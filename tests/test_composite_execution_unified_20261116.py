# -*- coding: utf-8 -*-

"""真实复合子前缀、归档和材料观察交付；只使用独占临时目录。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.execution_state import ExecutionState
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import execute_tree_removal
from core.name_moves import (
    FixedNameMoveError, execute_observed_name_move,
    prepare_observed_name_move,
)
from core.task_observation import TaskObservationState


class NameCompositeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()
        (self.source / "source.txt").write_bytes(b"source")
        (self.target / "a.txt").write_bytes(b"a")
        (self.target / "b.txt").write_bytes(b"b")
        source = observe_tree(
            self.source, include_bytes=True, include_mtime=True,
        )
        target = observe_tree(
            self.target, include_bytes=True, include_mtime=True,
        )
        self.move = prepare_observed_name_move(source, target)
        self.state = ExecutionState()
        self.normal = TaskObservationState((source, target))

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self):
        return self.state.execute_active(
            self.move, execute_observed_name_move,
            stage=lambda target, phase: None,
        )

    def test_live_name_child_prefix_remains_same_after_normal_archive(self):
        visible = []

        def tracked(removal):
            def check():
                results = self.state.results
                self.normal.consume_results(results)
                if results:
                    visible.append(results[0])

            return execute_tree_removal(removal, before_step=check)

        with patch("core.name_moves.execute_tree_removal", tracked):
            result = self.execute()
        self.assertTrue(result.succeeded)
        self.assertTrue(visible)
        self.assertEqual(len(self.state.results), 4)
        self.assertTrue(all(
            receipt is self.state.results[0] for receipt in visible
        ))
        self.normal.consume_results(self.state.results)
        self.assertIsNone(self.move.active_change)
        self.assertFalse(self.source.exists())
        self.assertEqual((self.target / "source.txt").read_bytes(), b"source")

    def test_live_partial_failure_is_archived_once_and_keeps_child_scene(self):
        visible = []
        children = []

        def tracked(removal):
            children.append(removal)

            def check():
                results = self.state.results
                self.normal.consume_results(results)
                if results:
                    visible.append(results[0])
                    raise ValueError("stop after first child step")

            return execute_tree_removal(removal, before_step=check)

        with patch("core.name_moves.execute_tree_removal", tracked):
            with self.assertRaises(FixedNameMoveError):
                self.execute()
        self.assertEqual(len(self.state.results), 1)
        self.assertIs(visible[0], self.state.results[0])
        self.assertEqual(len(self.move.results), 1)
        self.assertIs(self.move.active_change, children[0])
        self.normal.consume_results(self.state.results)
        self.assertEqual((self.source / "source.txt").read_bytes(), b"source")
        self.assertFalse((self.target / "a.txt").exists())
        self.assertEqual((self.target / "b.txt").read_bytes(), b"b")


@unittest.skipUnless(os.name == "nt", "内部材料正式名称提交要求Windows")
class InternalMaterialCompositeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.destination = self.root / "materials"
        self.source.mkdir()
        self.destination.mkdir()
        (self.source / "sample.txt").write_bytes(b"source")

    def tearDown(self):
        self.temporary.cleanup()

    def test_internal_tree_live_prefix_and_published_observation(self):
        import core.internal_materials as materials
        from core.filesystem_changes import execute_tree_copy

        observed = observe_tree(
            self.source, include_bytes=True, include_mtime=True,
        )
        expected = observe_file(self.destination / "source")
        from core.execution_ledger import ExecutionLedger
        original = ExecutionLedger.execute_active
        seen = []

        def tracked(ledger, prepared, executor, **keywords):
            def copy_with_check(copy):
                def check():
                    current = ledger.results
                    if current:
                        seen.append(current[0])

                return execute_tree_copy(copy, recheck_context=check)

            self.assertIs(executor, materials.execute_tree_copy)
            return original(ledger, prepared, copy_with_check, **keywords)

        with patch.object(ExecutionLedger, "execute_active", tracked):
            prepared = materials.prepare_internal_material(
                observed, expected, durable_publication=False,
            )
        self.assertTrue(seen)
        self.assertTrue(all(
            receipt is prepared.results[0] for receipt in seen
        ))
        original_results = prepared.results
        receipt = materials.publish_internal_material(prepared)
        self.assertTrue(receipt.succeeded)
        self.assertEqual(receipt.observation.path, expected.path)
        self.assertEqual(
            receipt.observation.content_records, observed.content_records,
        )
        self.assertEqual(
            receipt.observation.file_times, observed.file_times,
        )
        self.assertTrue(all(
            before is after
            for before, after in zip(original_results, receipt.results)
        ))
        self.assertEqual(
            (expected.path / "sample.txt").read_bytes(), b"source",
        )
        self.assertIsNone(prepared.active_change)

    def test_internal_file_publication_delivers_verified_observation(self):
        from core.internal_materials import (
            prepare_internal_material, publish_internal_material,
        )

        source = self.source / "sample.txt"
        observed = observe_file(
            source, include_bytes=True, include_digest=True, include_mtime=True,
        )
        expected = observe_file(self.destination / "sample.txt")
        prepared = prepare_internal_material(
            observed, expected, durable_publication=False,
        )
        result = publish_internal_material(prepared)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.observation.path, expected.path)
        self.assertEqual(result.observation.digest, observed.digest)
        self.assertEqual(result.observation.link_count, 1)
        self.assertEqual(expected.path.read_bytes(), b"source")
        self.assertEqual(source.read_bytes(), b"source")


if __name__ == "__main__":
    unittest.main()
