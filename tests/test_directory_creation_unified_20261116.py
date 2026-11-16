# -*- coding: utf-8 -*-

"""必要目录共同机制及真实入口；只修改独占临时目录。"""

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager, SaveStatus
from core.constants import DEFAULT_CONFIG
from core.directory_creation import (
    DirectoryCreationState, fixed_directory_observations,
)
from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import (
    FilesystemChangeError, execute_tree_copy, prepare_tree_copy,
)
from services.scan_service import panoramic_scan
from services.merge_service import merge_files


class DirectoryCreationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def ensure(self, state, plan, receipts, **keywords):
        return state.ensure_all(
            plan, record_step=receipts.append,
            stage=lambda target, phase: None, **keywords,
        )

    def test_fixed_projection_does_not_create_and_reuse_adds_no_steps(self):
        target = self.root / "new" / "nested" / "target.txt"
        expected = observe_file(target)
        plan = fixed_directory_observations(expected)
        self.assertEqual(
            tuple(member.path for member in plan),
            (self.root / "new", target.parent),
        )
        self.assertFalse(target.parent.exists())
        receipts = []
        state = DirectoryCreationState()
        self.ensure(state, plan, receipts)
        original = tuple(receipts)
        self.ensure(state, plan, receipts)
        self.assertEqual(tuple(receipts), original)
        self.assertEqual(len(state.created), 2)
        self.assertFalse(target.exists())
        state.recheck_parents(expected)

    def test_external_directory_does_not_gain_creation_ownership(self):
        target = self.root / "new" / "target.txt"
        plan = fixed_directory_observations(observe_file(target))
        target.parent.mkdir()
        receipts = []
        state = DirectoryCreationState()
        with self.assertRaises(FilesystemChangeError):
            self.ensure(state, plan, receipts)
        self.assertEqual(len(receipts), 1)
        self.assertFalse(receipts[0].attempted)
        self.assertFalse(receipts[0].succeeded)
        self.assertEqual(dict(state.created), {})
        self.assertTrue(target.parent.is_dir())

    def test_rebuilt_same_name_blocks_reuse_and_new_child(self):
        target = self.root / "new" / "nested" / "target.txt"
        plan = fixed_directory_observations(observe_file(target))
        state = DirectoryCreationState()
        receipts = []
        self.ensure(state, plan[:1], receipts)
        moved = self.root / "retained-original"
        (self.root / "new").rename(moved)
        (self.root / "new").mkdir()
        with self.assertRaises(ValueError):
            self.ensure(state, plan, receipts)
        self.assertEqual(len(receipts), 1)
        self.assertFalse(target.parent.exists())
        self.assertTrue(moved.is_dir())

    def test_failure_retains_real_prefix_without_creating_following_directory(self):
        target = self.root / "first" / "second" / "third" / "target.txt"
        plan = fixed_directory_observations(observe_file(target))
        original_mkdir = Path.mkdir

        def create(path, *arguments, **keywords):
            if path == self.root / "first" / "second":
                raise PermissionError("directory denied")
            return original_mkdir(path, *arguments, **keywords)

        state = DirectoryCreationState()
        receipts = []
        with patch.object(Path, "mkdir", create):
            with self.assertRaises(FilesystemChangeError):
                self.ensure(state, plan, receipts)
        self.assertEqual(len(receipts), 2)
        self.assertTrue(receipts[0].succeeded)
        self.assertFalse(receipts[1].succeeded)
        self.assertTrue(receipts[1].attempted)
        self.assertEqual(len(state.created), 1)
        self.assertFalse(target.parent.exists())

    def test_config_save_uses_same_directory_mechanism(self):
        target = self.root / "config" / "nested" / "configuration.json"
        configuration = deepcopy(DEFAULT_CONFIG)
        manager = ConfigSaveManager(
            configuration, path=target, needs_write=True,
        )
        original = DirectoryCreationState.ensure_all
        calls = []

        def tracked(state, expectations, **keywords):
            expectations = tuple(expectations)
            calls.append(tuple(member.path for member in expectations))
            return original(state, expectations, **keywords)

        with patch.object(DirectoryCreationState, "ensure_all", tracked):
            result = manager.save()
        self.assertEqual(result.status, SaveStatus.SAVED)
        self.assertEqual(len(result.directory_results), 2)
        self.assertEqual(
            json.loads(target.read_bytes().decode("utf-8")), configuration,
        )
        self.assertEqual(calls, [(target.parent.parent, target.parent)])

    def test_scan_and_merge_use_same_directory_mechanism_without_backup(self):
        source = self.root / "source"
        source.mkdir()
        (source / "sample.py").write_bytes(b"print('sample')")
        exclusions = prepare_exclusions("", "")
        original = DirectoryCreationState.ensure_all
        calls = []

        def tracked(state, expectations, **keywords):
            expectations = tuple(expectations)
            calls.append(tuple(member.path for member in expectations))
            return original(state, expectations, **keywords)

        scan_target = self.root / "scan" / "nested" / "result.txt"
        merge_target = self.root / "merge" / "nested" / "result.txt"
        with patch.object(DirectoryCreationState, "ensure_all", tracked):
            scanned = panoramic_scan(
                str(source), str(scan_target), exclusions, "", "",
            )
            merged = merge_files(
                "directory", str(source), "", str(merge_target), exclusions,
                "", "", "---以下是源代码---", "---源代码结束---",
                record_file_mtime=False,
            )
        self.assertTrue(scanned.output_result.succeeded)
        self.assertTrue(merged.output_result.succeeded)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(scanned.output_result.directory_results), 2)
        self.assertEqual(len(merged.output_result.directory_results), 2)
        self.assertFalse(tuple(self.root.rglob("recovery.json")))

    def test_tree_copy_delivers_verified_final_observation(self):
        source = self.root / "source"
        source.mkdir()
        (source / "nested").mkdir()
        (source / "nested" / "sample.txt").write_bytes(b"source")
        observed = observe_tree(
            source, include_bytes=True, include_mtime=True,
        )
        target = self.root / "target"
        prepared = prepare_tree_copy(observed, observe_file(target))
        self.assertIsNone(prepared.final_observation)
        result = execute_tree_copy(prepared)
        self.assertTrue(result.succeeded)
        actual = prepared.final_observation
        self.assertEqual(actual.path, target)
        self.assertEqual(actual.content_records, observed.content_records)
        self.assertEqual(actual.file_times, observed.file_times)
        self.assertEqual(
            (target / "nested" / "sample.txt").read_bytes(), b"source",
        )


@unittest.skipUnless(os.name == "nt", "正式备份材料提交要求Windows")
class ProtectedDirectoryConsumerTests(unittest.TestCase):
    def test_work_and_internal_directories_share_algorithm_but_not_results(self):
        from core.recovery_protection import read_recovery_evidence
        from services.patch_service import apply_patch, preview_patch

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            backups = root / "backups"
            boundary = "ZIPATCH_BOUNDARY_DIRECTORY_TEST_20261116133000_N8R3"
            source = (
                f'<<ZIPATCH_V3 boundary="{boundary}">>\n'
                '---OP write_file id="create" if_exists="error"\n'
                f"---PATH\nnew/nested/sample.txt\n{boundary}\n"
                f"---CONTENT\ncreated\n{boundary}\n"
                "---END_OP\n<</ZIPATCH_V3>>\n"
            )
            preview = preview_patch(
                None, str(project), source,
                backup_dir=str(backups), normalize_text_newlines=False,
            )
            self.assertFalse(preview.has_errors, preview.preview_text)
            original = DirectoryCreationState.ensure_all
            calls = []

            def tracked(state, expectations, **keywords):
                expectations = tuple(expectations)
                calls.append((state, tuple(member.path for member in expectations)))
                return original(state, expectations, **keywords)

            with patch.object(DirectoryCreationState, "ensure_all", tracked):
                result = apply_patch(preview)
            executor = preview.execution
            session = executor.protection
            self.assertGreaterEqual(len(calls), 2)
            self.assertIsNot(
                executor.task_execution.state.directories,
                session._directory_state,
            )
            self.assertEqual(
                (project / "new/nested/sample.txt").read_bytes(), b"created",
            )
            work_results = executor.results
            self.assertEqual(
                tuple(receipt.kind for receipt in work_results[:-1]),
                ("create_directory", "create_directory"),
            )
            self.assertTrue(all(
                receipt.target.is_relative_to(project)
                for receipt in work_results
            ))
            self.assertTrue(all(
                not any(receipt is work for work in work_results)
                for receipt in session.directory_results
            ))
            read_recovery_evidence(result.backup_root)


if __name__ == "__main__":
    unittest.main()
