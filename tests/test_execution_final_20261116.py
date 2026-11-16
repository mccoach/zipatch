# -*- coding: utf-8 -*-

"""最终账本、配置及主动恢复资格契约；只修改独占临时目录。"""

import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core.config import load_user_config
from core.constants import DEFAULT_CONFIG
from core.directory_file_replacement import (
    DirectoryFileReplacementError, execute_directory_file_replacement,
    prepare_directory_file_replacement,
)
from core.execution_ledger import ExecutionLedger
from core.execution_state import ExecutionState
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import execute_tree_removal
from core.protection_projection import FixedEffect, state_from_bytes
from core.task_observation import TaskObservationState


class ExecutionLedgerTests(unittest.TestCase):
    def test_defect_archives_same_prefix_and_retains_scene(self):
        ledger = ExecutionLedger(lambda prepared: prepared.results)
        receipt = object()
        prepared = SimpleNamespace(
            results=(), current=Path("failure"), phase="injected",
        )
        defect = RuntimeError("program defect")
        stages = []

        def execute(active):
            active.results = (receipt,)
            self.assertIs(ledger.results[0], receipt)
            raise defect

        with self.assertRaises(RuntimeError) as captured:
            ledger.execute_active(
                prepared, execute,
                stage=lambda current, phase: stages.append((current, phase)),
            )
        self.assertIs(captured.exception, defect)
        self.assertEqual(len(ledger.results), 1)
        self.assertIs(ledger.results[0], receipt)
        self.assertIs(ledger.active_change, prepared)
        self.assertEqual(stages, [(prepared.current, prepared.phase)])
        self.assertEqual(len(ledger.results), 1)

    def test_replaced_prefix_is_rejected(self):
        ledger = ExecutionLedger(lambda prepared: prepared.results)
        prepared = SimpleNamespace(
            results=(), current=None, phase="injected",
        )

        def execute(active):
            active.results = (object(),)
            ledger.results
            active.results = (object(),)

        with self.assertRaisesRegex(ValueError, "追加前缀"):
            ledger.execute_active(
                prepared, execute, stage=lambda current, phase: None,
            )

    def test_independent_scopes_do_not_share_steps_or_scene(self):
        first = ExecutionLedger(lambda prepared: prepared.results)
        second = ExecutionLedger(lambda prepared: prepared.results)
        receipt = object()
        first.append_results((receipt,))
        first.scene = object()
        self.assertEqual(second.results, ())
        self.assertIsNone(second.active_change)
        self.assertIs(first.results[0], receipt)


class FinalConfigurationTests(unittest.TestCase):
    def test_removed_policy_is_rejected_without_rewriting_source(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "configuration.json"
            configuration = deepcopy(DEFAULT_CONFIG)
            configuration["restore"]["existing_file_policy"] = "rename"
            raw = json.dumps(configuration, ensure_ascii=False).encode("utf-8")
            target.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "overwrite 或 skip"):
                load_user_config(target)
            self.assertEqual(target.read_bytes(), raw)

    def test_current_policy_loads_without_conversion(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "configuration.json"
            configuration = deepcopy(DEFAULT_CONFIG)
            configuration["restore"]["existing_file_policy"] = "skip"
            target.write_bytes(
                json.dumps(configuration, ensure_ascii=False).encode("utf-8"),
            )
            loaded, needs_write = load_user_config(target)
            self.assertEqual(loaded, configuration)
            self.assertFalse(needs_write)


class ReplacementLedgerTests(unittest.TestCase):
    def test_partial_removal_has_same_live_and_archived_step(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "a.txt").write_bytes(b"a")
            (target / "b.txt").write_bytes(b"b")
            source = root / "source.txt"
            source.write_bytes(b"desired")
            before = observe_tree(
                target, include_bytes=True, include_mtime=True,
            )
            material = observe_file(
                source, include_bytes=True, include_digest=True,
                include_mtime=True,
            )
            replacement = prepare_directory_file_replacement(
                FixedEffect(before, state_from_bytes(b"desired")),
                material, mtime_ns=material.mtime_ns,
                durable_publication=False,
            )
            state = ExecutionState()
            normal = TaskObservationState((before,))
            visible = []

            def tracked(removal, *, before_step=None):
                def check():
                    if before_step is not None:
                        before_step()
                    normal.consume_results(state.results)
                    if state.results:
                        visible.append(state.results[0])
                        self.assertIs(
                            replacement.results[0], state.results[0],
                        )
                        raise ValueError("stop after first removal")

                return execute_tree_removal(removal, before_step=check)

            with patch(
                "core.directory_file_replacement.execute_tree_removal",
                tracked,
            ):
                with self.assertRaises(DirectoryFileReplacementError):
                    state.execute_active(
                        replacement, execute_directory_file_replacement,
                        stage=lambda current, phase: None,
                    )
            self.assertEqual(len(state.results), 1)
            self.assertEqual(len(replacement.results), 1)
            self.assertIs(visible[0], state.results[0])
            self.assertIs(
                replacement.active_change, replacement.removal,
            )
            normal.consume_results(state.results)
            self.assertFalse((target / "a.txt").exists())
            self.assertEqual((target / "b.txt").read_bytes(), b"b")
            self.assertEqual(source.read_bytes(), b"desired")


@unittest.skipUnless(os.name == "nt", "正式备份材料发布要求Windows")
class ActiveRecoveryQualificationTests(unittest.TestCase):
    def test_service_projects_common_task_qualification(self):
        from services.patch_service import (
            apply_patch, preview_backup_restore, preview_patch,
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            target = project / "sample.txt"
            target.write_bytes(b"original")
            boundary = "ZIPATCH_BOUNDARY_QUALIFICATION_20261116142000_Q7"
            package = (
                f'<<ZIPATCH_V3 boundary="{boundary}">>\n'
                '---OP write_file id="change" if_exists="overwrite"\n'
                f"---PATH\nsample.txt\n{boundary}\n"
                f"---CONTENT\nchanged\n{boundary}\n"
                "---END_OP\n<</ZIPATCH_V3>>\n"
            )
            preview = preview_patch(
                None, str(project), package,
                backup_dir=str(root / "backups"),
                normalize_text_newlines=False,
            )
            self.assertFalse(preview.has_errors, preview.preview_text)
            applied = apply_patch(preview)
            recovery = preview_backup_restore(
                applied.backup_root,
                backup_dir=str(root / "restore-backups"),
                backup_project_root=str(project),
            )
            service = recovery.execution
            self.assertNotIn("consumed", vars(service))
            self.assertFalse(service.consumed)
            self.assertTrue(service.execution.invalidate())
            self.assertTrue(service.consumed)
            self.assertFalse(service.invalidate())
            with self.assertRaises(ValueError):
                service.confirm_execution()
            with self.assertRaises(ValueError):
                service.apply()
            self.assertEqual(target.read_bytes(), b"changed")


if __name__ == "__main__":
    unittest.main()
