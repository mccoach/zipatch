# -*- coding: utf-8 -*-

"""共同账本真实复合动作前缀、失败现场及创建目录身份保护。"""

import os
import tempfile
import unittest
from pathlib import Path

from core.execution_state import ExecutionState
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import (
    TreeRemovalError, execute_tree_removal,
    prepare_directory_creation, prepare_tree_removal,
)
from core.task_observation import TaskObservationState


class ExecutionStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_composite_prefix_keeps_receipt_identity_after_archive(self):
        tree = self.root / "tree"
        tree.mkdir()
        first = tree / "a.txt"
        second = tree / "b.txt"
        first.write_bytes(b"same")
        os.link(first, second)
        before = observe_tree(tree, include_bytes=True, include_mtime=True)
        projected = TaskObservationState((before,))
        state = ExecutionState()
        removal = prepare_tree_removal(before)
        consumed = []

        def check():
            current = state.results
            projected.consume_results(current)
            if current:
                consumed.append(current[0])

        state.execute_active(
            removal, execute_tree_removal,
            stage=lambda target, phase: None, before_step=check,
        )
        self.assertFalse(tree.exists())
        self.assertEqual(len(state.results), 3)
        self.assertTrue(all(receipt.succeeded for receipt in state.results))
        self.assertTrue(consumed)
        self.assertTrue(all(receipt is state.results[0] for receipt in consumed))
        projected.consume_results(state.results)
        self.assertIsNone(state.active_change)
        self.assertEqual(state.results, removal.results)

    def test_partial_failure_archives_once_and_retains_exact_scene(self):
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "a.txt").write_bytes(b"a")
        (tree / "b.txt").write_bytes(b"b")
        state = ExecutionState()
        removal = prepare_tree_removal(observe_tree(tree))
        stages = []

        def reject_second():
            if removal.results:
                raise ValueError("stop after first")

        with self.assertRaises(TreeRemovalError):
            state.execute_active(
                removal, execute_tree_removal,
                stage=lambda target, phase: stages.append((target, phase)),
                before_step=reject_second,
            )
        self.assertEqual(len(state.results), 1)
        self.assertIs(state.results[0], removal.results[0])
        self.assertEqual(len(state.results), 1)
        self.assertIs(state.active_change, removal)
        self.assertEqual(stages[-1], (removal.current, removal.phase))
        self.assertFalse((tree / "a.txt").exists())
        self.assertTrue((tree / "b.txt").exists())

    def test_created_directory_identity_is_not_reauthorized_by_same_name(self):
        directory = self.root / "created"
        state = ExecutionState()
        receipt = state.execute_step(
            prepare_directory_creation(observe_file(directory)),
            stage=lambda target, phase: None,
        )
        state.accept_created_directory(receipt)
        state.recheck_created_directories()
        original = self.root / "original"
        directory.rename(original)
        directory.mkdir()
        with self.assertRaises(ValueError):
            state.recheck_created_directories()
        self.assertTrue(directory.exists())
        self.assertTrue(original.exists())

    def test_unregistered_creation_cannot_become_task_owned(self):
        directory = self.root / "created"
        from core.filesystem_changes import execute_filesystem_change
        receipt = execute_filesystem_change(
            prepare_directory_creation(observe_file(directory)),
        )
        state = ExecutionState()
        with self.assertRaises(RuntimeError):
            state.accept_created_directory(receipt)
        self.assertEqual(dict(state.created_directories), {})


if __name__ == "__main__":
    unittest.main()