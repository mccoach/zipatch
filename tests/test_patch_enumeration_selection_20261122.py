# -*- coding: utf-8 -*-

"""修改包在第一次目录枚举前集中选择OP，跳过裁剪及真实页面接入。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.patch_executor import PatchExecutor
from services.patch_service import apply_patch, preview_patch
from core.recovery_protection import read_recovery_evidence


@unittest.skipUnless(os.name == "nt", "Windows修改包枚举范围")
class PatchEnumerationSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.actual = self.root / "actual"
        self.actual.mkdir()
        self.first = self.actual / "first"
        self.second = self.actual / "second"
        self.first.mkdir()
        self.second.mkdir()
        (self.first / "body").write_bytes(b"first")
        (self.second / "body").write_bytes(b"second")
        self.alias = self.project / "route"
        os.symlink(self.actual, self.alias, target_is_directory=True)
        self.backups = self.root / "backups"
        self.backups.mkdir()

    def tearDown(self):
        try:
            if os.path.lexists(self.alias):
                os.rmdir(self.alias)
        finally:
            self.temporary.cleanup()

    def operations(self):
        return [
            {"op": "delete_dir", "id": "first001", "path": "route/first"},
            {"op": "delete_dir", "id": "second001", "path": "route/second"},
        ]

    def executor(self, selector):
        return PatchExecutor(
            None, self.project, allow_delete=True,
            backup_dir=str(self.backups), normalize_text_newlines=False,
            select_patch_enumeration=selector,
            authorize_patch_scope=lambda scopes, root:
            tuple("authorize" for _ in scopes),
        )

    def preview(self, executor, operations=None):
        return executor.preview({
            "version": "3.0",
            "operations": self.operations() if operations is None else operations,
        })

    def test_all_op_ranges_are_selected_before_any_directory_enumeration(self):
        entered = []
        approved = False
        original_scandir = os.scandir

        def choose(scopes, root):
            nonlocal approved
            self.assertEqual(entered, [])
            self.assertEqual(root, self.project)
            self.assertEqual(
                tuple(scope.operation["id"] for scope in scopes),
                ("first001", "second001"),
            )
            self.assertEqual(
                tuple(scope.accesses[0].actual_path for scope in scopes),
                (self.first, self.second),
            )
            approved = True
            return ("authorize", "skip")

        def scandir(path):
            target = Path(path)
            if target == self.second:
                raise AssertionError("未选OP不得枚举实际目录")
            if target == self.first:
                self.assertTrue(approved)
                entered.append(target)
            return original_scandir(path)

        executor = self.executor(choose)
        with patch("os.scandir", new=scandir):
            result = self.preview(executor)
        self.assertFalse(result[2], result[0])
        self.assertEqual(entered, [self.first])
        self.assertTrue(executor.plan.actions[0].execute)
        self.assertFalse(executor.plan.actions[1].execute)
        self.assertEqual(executor.plan.actions[1].accesses, ())
        self.assertEqual(
            tuple(member.target for member in executor.projection.objects),
            (self.first,),
        )
        self.assertIn("先行枚举范围选择跳过", executor.plan.actions[1].skip_reason)

    def test_cancelled_selection_enumerates_nothing_and_creates_no_backup(self):
        executor = self.executor(lambda scopes, root: None)
        with patch(
            "os.scandir", side_effect=AssertionError("取消不得枚举任何目录树"),
        ):
            with self.assertRaisesRegex(ValueError, "已取消"):
                self.preview(executor)
        self.assertIsNone(executor.plan)
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_skipped_op_changes_do_not_remain_execution_dependency(self):
        ordinary = self.project / "ordinary"
        ordinary.write_bytes(b"before")
        executor = self.executor(lambda scopes, root: ("skip",))
        operations = [
            self.operations()[0],
            {
                "op": "replace_exact", "id": "edit001", "path": "ordinary",
                "old": "before", "new": "after", "count": "1",
            },
        ]
        result = self.preview(executor, operations)
        self.assertFalse(result[2], result[0])
        os.rmdir(self.alias)
        os.symlink(self.backups, self.alias, target_is_directory=True)
        executor.apply()
        self.assertEqual(ordinary.read_bytes(), b"after")
        self.assertEqual((self.first / "body").read_bytes(), b"first")
        evidence = read_recovery_evidence(executor.backup_root)
        self.assertEqual(len(evidence.document.value["objects"]), 1)
        self.assertEqual(evidence.materials["o000001"].source_bytes, b"before")

    def test_selected_access_retarget_stops_before_any_tree_enumeration(self):
        def choose(scopes, root):
            os.rmdir(self.alias)
            os.symlink(self.backups, self.alias, target_is_directory=True)
            return ("authorize", "skip")

        executor = self.executor(choose)
        with patch(
            "os.scandir", side_effect=AssertionError("改指向后不得枚举"),
        ):
            result = self.preview(executor)
        self.assertTrue(result[2])
        self.assertIsNone(executor.plan)
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_invalid_later_operation_prevents_first_enumeration_and_selection(self):
        executor = self.executor(
            lambda scopes, root: self.fail("整包参数失败不得开始选择"),
        )
        operations = [
            self.operations()[0],
            {"op": "delete_file", "id": "bad001", "path": "route/second"},
        ]
        with patch(
            "os.scandir", side_effect=AssertionError("参数失败不得枚举"),
        ):
            result = self.preview(executor, operations)
        self.assertTrue(result[2])
        self.assertIsNone(executor.plan)

    def test_dual_directory_operation_is_one_choice_with_both_fixed_ends(self):
        def choose(scopes, root):
            self.assertEqual(len(scopes), 1)
            self.assertEqual(
                tuple(access.actual_path for access in scopes[0].accesses),
                (self.first, self.second),
            )
            return ("skip",)

        executor = self.executor(choose)
        with patch(
            "os.scandir", side_effect=AssertionError("双端OP跳过不得枚举"),
        ):
            result = self.preview(executor, [{
                "op": "copy_dir", "id": "copy001",
                "path": "route/first", "new_path": "route/second",
                "if_exists": "overwrite",
            }])
        self.assertFalse(result[2], result[0])
        self.assertEqual(executor.projection.objects, ())
        self.assertFalse(executor.plan.actions[0].execute)

    def test_formal_service_selected_tree_executes_with_protection(self):
        boundary = "ZIPATCH_BOUNDARY_ENUM_TEST_20261122225500_K8M4"
        package = "\n".join((
            f'<<ZIPATCH_V3 boundary="{boundary}">>',
            '---OP delete_dir id="first001"',
            "---PATH", "route/first", boundary,
            "---END_OP",
            '---OP delete_dir id="second001"',
            "---PATH", "route/second", boundary,
            "---END_OP", "<</ZIPATCH_V3>>",
        ))
        preview = preview_patch(
            None, self.project, package, allow_delete=True,
            backup_dir=str(self.backups), normalize_text_newlines=False,
            select_patch_enumeration=lambda scopes, root: ("authorize", "skip"),
            authorize_patch_scope=lambda scopes, root: ("authorize", "authorize"),
        )
        self.assertFalse(preview.has_errors, preview.preview_text)
        result = apply_patch(preview)
        self.assertFalse(self.first.exists())
        self.assertEqual((self.second / "body").read_bytes(), b"second")
        evidence = read_recovery_evidence(result.backup_root)
        self.assertEqual(len(evidence.document.value["objects"]), 1)
        self.assertEqual(
            Path(evidence.document.value["objects"][0]["target"]), self.first,
        )

    def test_real_patch_page_passes_both_selection_stages(self):
        import tkinter as tk
        from copy import deepcopy
        from types import SimpleNamespace

        from core.config import ConfigSaveManager
        from core.constants import DEFAULT_CONFIG
        from panels.patch_panel import PatchPanel
        from services.patch_service import invalidate_preview
        from ui.config_editing import ConfigCommitController

        boundary = "ZIPATCH_BOUNDARY_PAGE_ENUM_TEST_20261122225500_Q4M8"
        package = "\n".join((
            f'<<ZIPATCH_V3 boundary="{boundary}">>',
            '---OP delete_dir id="delete001"',
            "---PATH", "route/first", boundary,
            "---END_OP", "<</ZIPATCH_V3>>",
        ))
        root = tk.Tk()
        root.withdraw()
        config = deepcopy(DEFAULT_CONFIG)
        manager = ConfigSaveManager(
            config, path=self.root / "config.json", needs_write=True,
        )
        commits = ConfigCommitController(root, manager)
        app = SimpleNamespace(
            root=root, config_data=config, commits=commits,
            log=lambda message: None, status_var=tk.StringVar(master=root),
        )
        preview = None
        titles = []

        def choose(parent, commits, *, title, introduction, choices, details):
            titles.append(title)
            self.assertEqual(len(choices), 1)
            if title == "选择修改包先行目录枚举范围":
                self.assertIn(str(self.first), details)
                self.assertIn("尚未枚举", introduction)
                return ("skip",)
            self.assertTrue(choices[0].fixed_skip)
            return ("skip",)

        try:
            panel = PatchPanel(root, app)
            cfg = {
                "project_root": str(self.project), "patch_text": package,
                "output_rules": None, "normalize_text_newlines": False,
                "allow_delete": True, "allow_multi_replace_exact": False,
                "backup_dir": str(self.backups),
            }
            with patch(
                "ui.work_scope.select_work_scope", side_effect=choose,
            ):
                preview = panel.run_preview(cfg)
            self.assertFalse(preview.has_errors, preview.preview_text)
            self.assertEqual(titles, [
                "选择修改包先行目录枚举范围", "选择本次修改操作",
            ])
            self.assertEqual(preview.success_count, 0)
            self.assertEqual(tuple(self.backups.iterdir()), ())
        finally:
            if preview is not None:
                invalidate_preview(preview)
            root.destroy()


if __name__ == "__main__":
    unittest.main()