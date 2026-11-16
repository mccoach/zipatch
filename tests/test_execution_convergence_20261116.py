# -*- coding: utf-8 -*-

"""账本完整性失败收口、工作类型约束及目录转文件单次准备。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import core.directory_file_replacement as replacement_core
from core.content_publication import prepare_copy_content_artifact
from core.execution_ledger import ExecutionLedger
from core.execution_state import ExecutionState
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import FilesystemChangeResult
from core.protection_projection import FixedEffect, state_from_bytes


class LedgerIntegrityExitTests(unittest.TestCase):
    def check_invalid_exit(self, changed_results, *, defect=None):
        ledger = ExecutionLedger(lambda prepared: prepared.results)
        prior = object()
        accepted = object()
        ledger.append_results((prior,))
        prepared = SimpleNamespace(
            results=(), current=Path("diagnostic"), phase="integrity",
        )
        stages = []

        def execute(active):
            active.results = (accepted,)
            self.assertIs(ledger.results[-1], accepted)
            active.results = changed_results
            if defect is not None:
                raise defect

        with self.assertRaises((TypeError, ValueError)) as captured:
            ledger.execute_active(
                prepared, execute,
                stage=lambda current, phase: stages.append((current, phase)),
            )
        self.assertEqual(len(ledger.results), 2)
        self.assertIs(ledger.results[0], prior)
        self.assertIs(ledger.results[1], accepted)
        self.assertIs(ledger.active_change, prepared)
        self.assertEqual(stages, [(prepared.current, prepared.phase)])

        # 诊断现场不是仍在运行的动作，不再触发重入拒绝。
        following = SimpleNamespace(results=(), current=None, phase="completed")
        ledger.execute_active(
            following, lambda active: None,
            stage=lambda current, phase: None,
        )
        self.assertIsNone(ledger.active_change)
        self.assertEqual(len(ledger.results), 2)
        return captured.exception

    def test_replaced_prefix_keeps_last_accepted_receipts(self):
        self.check_invalid_exit((object(),))

    def test_shortened_prefix_keeps_last_accepted_receipts(self):
        self.check_invalid_exit(())

    def test_non_tuple_view_keeps_last_accepted_receipts(self):
        self.check_invalid_exit([])

    def test_executor_defect_remains_in_integrity_error_context(self):
        defect = RuntimeError("executor defect")
        failure = self.check_invalid_exit((object(),), defect=defect)
        self.assertIs(failure.__context__, defect)

    def test_invalid_first_result_does_not_enter_archive(self):
        ledger = ExecutionLedger(lambda prepared: prepared.results)
        prepared = SimpleNamespace(results=(), current=None, phase="invalid")

        def execute(active):
            active.results = []

        with self.assertRaises(TypeError):
            ledger.execute_active(
                prepared, execute, stage=lambda current, phase: None,
            )
        self.assertEqual(ledger.results, ())
        self.assertIs(ledger.active_change, prepared)


class WorkResultConstraintTests(unittest.TestCase):
    def receipt(self):
        return FilesystemChangeResult(
            Path("formal"), "remove_file", True, True, "completed",
        )

    def test_direct_append_rejects_entire_invalid_batch(self):
        state = ExecutionState()
        receipt = self.receipt()
        with self.assertRaises(TypeError):
            state.append_results((receipt, object()))
        self.assertEqual(state.results, ())
        state.append_results((receipt,))
        self.assertIs(state.results[0], receipt)

    def test_active_view_rejects_non_work_receipt_and_retains_prefix(self):
        state = ExecutionState()
        removal = replacement_core.prepare_tree_removal
        with TemporaryDirectory() as temporary:
            tree = Path(temporary) / "tree"
            tree.mkdir()
            prepared = removal(observe_tree(tree))
            receipt = self.receipt()

            def execute(active):
                object.__setattr__(active, "_results", (receipt,))
                self.assertIs(state.results[0], receipt)
                object.__setattr__(active, "_results", (receipt, object()))

            with self.assertRaises(TypeError):
                state.execute_active(
                    prepared, execute, stage=lambda current, phase: None,
                )
            self.assertEqual(state.results, (receipt,))
            self.assertIs(state.active_change, prepared)
            state.append_results(())
            self.assertTrue(tree.is_dir())


class DirectoryFilePreparationTests(unittest.TestCase):
    def test_one_removal_qualification_and_verified_final_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "old.txt").write_bytes(b"old")
            source_path = root / "source.txt"
            source_path.write_bytes(b"desired")
            source = observe_file(
                source_path, include_bytes=True, include_digest=True,
                include_mtime=True,
            )
            before = observe_tree(
                target, include_bytes=True, include_mtime=True,
            )
            effect = FixedEffect(before, state_from_bytes(b"desired"))
            original = replacement_core.prepare_tree_removal
            with patch.object(
                replacement_core, "prepare_tree_removal", wraps=original,
            ) as prepare:
                prepared = replacement_core.prepare_directory_file_replacement(
                    effect, source, mtime_ns=source.mtime_ns,
                    durable_publication=False,
                )
                self.assertEqual(prepare.call_count, 1)
            self.assertTrue(target.is_dir())
            self.assertEqual((target / "old.txt").read_bytes(), b"old")
            self.assertIs(prepared.artifact.content, source.source_bytes)
            result = replacement_core.execute_directory_file_replacement(prepared)
            self.assertTrue(result.succeeded)
            self.assertEqual(target.read_bytes(), b"desired")
            self.assertEqual(target.stat().st_mtime_ns, source.mtime_ns)
            self.assertEqual(source_path.read_bytes(), b"desired")
            self.assertEqual(prepared.final_observation.digest, source.digest)

    def test_copy_content_does_not_accept_tree_target(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            source_path = root / "source.txt"
            source_path.write_bytes(b"desired")
            source = observe_file(
                source_path, include_bytes=True, include_digest=True,
                include_mtime=True,
            )
            before = observe_tree(target)
            with self.assertRaises(TypeError):
                prepare_copy_content_artifact(
                    source, before, preserve_target_mode=False,
                )
            self.assertTrue(target.is_dir())
            self.assertEqual(source_path.read_bytes(), b"desired")
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["source.txt", "target"],
            )


if __name__ == "__main__":
    unittest.main()
