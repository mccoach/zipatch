# -*- coding: utf-8 -*-

"""独立恢复页注册、旧入口移除及完成输入后的保护目录跟随。"""

import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core.config import ConfigSaveManager, load_user_config, normalize_feature_order
from core.constants import DEFAULT_CONFIG
from feature_registry import get_feature_registry
from panels.backup_restore_panel import BackupRestorePanel
from panels.patch_panel import PatchPanel
from panels.restore_panel import RestorePanel
from ui.config_editing import ConfigCommitController


class BackupRestorePageTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"没有可用Tk显示环境：{error}")
        self.root.withdraw()
        self.directory = TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(
            self.config, path=self.base / "config.json", needs_write=True,
        )
        self.commits = ConfigCommitController(self.root, self.manager)
        self.app = SimpleNamespace(
            root=self.root, config_data=self.config, commits=self.commits,
            log=lambda message: None, status_var=tk.StringVar(master=self.root),
        )

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "directory"):
            self.directory.cleanup()

    def test_registry_appends_fifth_page_without_reordering_existing_pages(self):
        registry = get_feature_registry()
        self.config["ui"]["feature_order"] = ["patch", "merge", "scan", "restore"]
        self.assertTrue(normalize_feature_order(self.config, registry))
        self.assertEqual(
            self.config["ui"]["feature_order"],
            ["patch", "merge", "scan", "restore", "backup_restore"],
        )
        self.assertEqual(registry["backup_restore"]["title"], "备份还原")

    def test_old_business_pages_have_no_restore_mode_or_source_controls(self):
        for panel_class in (PatchPanel, RestorePanel):
            with self.subTest(panel=panel_class.__name__):
                panel = panel_class(self.root, self.app)
                self.assertFalse(hasattr(panel, "restore_source_entry"))
                self.assertFalse(hasattr(panel, "restore_project_root"))
                self.assertFalse(hasattr(panel, "patch_mode"))
                self.assertFalse(hasattr(panel, "execution_mode"))
                self.assertFalse(hasattr(panel, "record_backup_restore_result"))
                panel.destroy()

    def test_completed_source_follows_parent_and_manual_override_is_preserved(self):
        first_parent = self.base / "first"
        second_parent = self.base / "second"
        first_parent.mkdir()
        second_parent.mkdir()
        first = first_parent / "task"
        second = second_parent / "task"
        first.mkdir()
        second.mkdir()
        panel = BackupRestorePanel(self.root, self.app)
        panel.restore_source_dir.set(str(first))
        # 连续输入尚未接受，不应立即跳动保护目录。
        self.assertEqual(panel.backup_dir.get(), "")
        self.commits.submit_editors(
            (panel.restore_source_entry._config_binding,), self.root,
        )
        self.assertEqual(panel.backup_dir.get(), str(first_parent))
        manual = self.base / "manual"
        panel.backup_dir.set(str(manual))
        self.commits.submit_editors(
            (panel.backup_dir_entry._config_binding,), self.root,
        )
        self.assertEqual(panel.backup_dir.get(), str(manual))
        panel.follow_source()
        self.assertEqual(panel.backup_dir.get(), str(manual))
        panel.restore_source_dir.set(str(second))
        self.commits.submit_editors(
            (panel.restore_source_entry._config_binding,), self.root,
        )
        self.assertEqual(panel.backup_dir.get(), str(second_parent))
        loaded, _ = load_user_config(self.manager.path)
        self.assertEqual(
            loaded["backup_restore"]["backup_dir"], str(second_parent),
        )

    def test_history_acceptance_follows_source_in_hidden_window(self):
        parent = self.base / "history-parent"
        parent.mkdir()
        source = parent / "task"
        source.mkdir()
        self.config["entry_history"]["backup_restore.restore_source_dir"] = [
            str(source),
        ]
        panel = BackupRestorePanel(self.root, self.app)
        self.assertFalse(panel.restore_source_entry.winfo_ismapped())
        panel.restore_source_entry._history_plugin.apply_history(str(source))
        self.assertEqual(panel.backup_dir.get(), str(parent))
        self.assertEqual(
            self.config["backup_restore"]["backup_dir"], str(parent),
        )
        loaded, _ = load_user_config(self.manager.path)
        self.assertEqual(
            loaded["backup_restore"]["restore_source_dir"], str(source),
        )
        self.assertEqual(loaded["backup_restore"]["backup_dir"], str(parent))

    def test_invalid_partial_source_and_cancelled_browse_do_not_change_protection(self):
        panel = BackupRestorePanel(self.root, self.app)
        panel.backup_dir.set(str(self.base / "manual"))
        self.commits.accept_editors((panel.backup_dir_entry._config_binding,))
        panel.restore_source_dir.set("partial")
        self.commits.submit_editors(
            (panel.restore_source_entry._config_binding,), self.root,
        )
        self.assertEqual(panel.backup_dir.get(), str(self.base / "manual"))

    def test_missing_parameters_do_not_start_recovery_or_append_traceback(self):
        panel = BackupRestorePanel(self.root, self.app)
        with patch(
            "panels.backup_restore_panel.safe_show_error",
        ) as error, patch(
            "panels.backup_restore_panel.run_backup_restore",
        ) as run, patch.object(
            self.root, "after_idle", wraps=self.root.after_idle,
        ) as delayed, patch.object(
            panel.restore_source_entry, "focus_set",
            wraps=panel.restore_source_entry.focus_set,
        ) as focus:
            panel.run(True)
        error.assert_called_once()
        run.assert_not_called()
        delayed.assert_not_called()
        focus.assert_called_once()
        self.assertEqual(
            str(panel.restore_source_entry.cget("state")), "normal",
        )
        self.assertEqual(self.config["backup_restore"]["last_result_text"], "")
        self.assertEqual(self.commits.operation_depth, 0)
        panel.destroy()
        # 销毁后处理事件不应残留属于该页面的延迟焦点调用。
        self.root.update()


if __name__ == "__main__":
    unittest.main()