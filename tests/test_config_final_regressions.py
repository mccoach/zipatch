# -*- coding: utf-8 -*-

import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import TclError
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, SaveStatus, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG


class PersistenceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.manager = ConfigSaveManager(self.config, self.path, needs_write=True)
        self.manager.save()

    def test_session_save_failure_is_retried_by_ordinary_save(self):
        self.manager.accept(self.config, {"active_mode": "patch"}, persist=False)
        with patch("core.config.os.replace", side_effect=PermissionError("denied")):
            self.assertEqual(
                self.manager.save(include_session=True).status, SaveStatus.FAILED,
            )
        self.assertTrue(self.manager.needs_save)
        self.assertEqual(self.manager.save().status, SaveStatus.SAVED)
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["active_mode"], "patch")

    def test_empty_history_is_stable_across_load(self):
        self.manager.accept(self.config["entry_history"], {"scan.source_folder": []})
        self.manager.save()
        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(loaded["entry_history"]["scan.source_folder"], [])

    def test_failed_state_returning_to_saved_value_does_not_write(self):
        self.manager.accept(self.config, {"active_mode": "patch"}, persist=False)
        with patch("core.config.os.replace", side_effect=PermissionError("denied")):
            self.manager.save(include_session=True)
        self.manager.accept(self.config, {"active_mode": "merge"}, persist=False)
        before = self.manager.metrics["write_attempts"]
        self.assertEqual(self.manager.save().status, SaveStatus.UNCHANGED)
        self.assertEqual(self.manager.metrics["write_attempts"], before)
        self.assertIsNotNone(self.manager.last_save_error)


class ApplicationPageRegressionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        config = deep_merge_config(DEFAULT_CONFIG, {})
        manager = ConfigSaveManager(config, self.path, needs_write=True)
        manager.save()
        self.errors = []
        self.root.report_callback_exception = self.report_error
        # 只隔离窗口置顶和平台最大化之外的焦点抢占，不替换配置和面板链路。
        with patch("app.raise_and_focus"):
            self.app = ZipatchApp(self.root, config_path=self.path)

    def report_error(self, error_type, error, traceback):
        self.errors.append(error)

    def destroy_root(self):
        try:
            self.root.destroy()
        except TclError:
            pass

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [])

    def test_normal_start_has_no_write(self):
        self.assertEqual(self.app.config_manager.metrics["write_attempts"], 0)
        self.assertEqual(set(self.app.panels), {"merge"})

    def test_clean_page_switch_reuses_panels_and_tabs_without_serialization(self):
        before = dict(self.app.config_manager.metrics)
        buttons = dict(self.app.tabs.buttons)
        for mode in ("scan", "patch", "restore", "merge", "patch", "merge"):
            self.app.show_panel(mode)
        cached = dict(self.app.panels)
        self.app.show_panel("patch")
        self.app.show_panel("merge")
        for key in buttons:
            self.assertIs(self.app.tabs.buttons[key], buttons[key])
        for key in cached:
            self.assertIs(self.app.panels[key], cached[key])
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.app.config_manager.metrics[key], before[key])

    def test_page_switch_accepts_draft_and_late_focus_is_idempotent(self):
        panel = self.app.current_panel
        editor = next(
            editor for editor in self.app.commits.select("merge")
            if editor.field == "source_folder"
        )
        panel.source_folder.set("E:\\example")
        before = self.app.config_manager.metrics["successful_replaces"]
        self.app.show_panel("scan")
        editor.on_focus_out()
        self.assertEqual(self.app.config_data["merge"]["source_folder"], "E:\\example")
        self.assertEqual(
            self.app.config_manager.metrics["successful_replaces"] - before, 1,
        )

    def test_long_result_display_save_and_clear(self):
        result = "【完整结果开始】\n" + "结果正文" * 150_000 + "\n【完整结果结束】\n"
        self.assertGreater(len(result), 500_000)
        self.app.show_panel("patch")
        panel = self.app.current_panel
        before = dict(self.app.config_manager.metrics)

        with self.app.commits.operation():
            saved = panel.finish_result(result)
        self.assertTrue(saved.persisted)
        self.assertEqual(panel.result_text.get("1.0", "end-1c"), result)
        self.assertEqual(panel.cfg["last_result_text"], result)
        self.assertEqual(str(panel.result_text.cget("state")), "disabled")
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(
                self.app.config_manager.metrics[key] - before[key], 1,
            )

        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(loaded["patch"]["last_result_text"], result)

        panel.clear_result()
        self.assertEqual(panel.result_text.get("1.0", "end-1c"), "")
        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(loaded["patch"]["last_result_text"], "")
        self.assertEqual(self.app.commits.operation_depth, 0)

    def test_restore_mode_accepts_hidden_backup_directory(self):
        self.app.show_panel("patch")
        panel = self.app.current_panel
        panel.patch_mode.set("restore")
        self.app.commits.submit_values(panel.cfg, {"patch_mode": "restore"})
        panel.refresh_mode_ui()
        panel.backup_dir.set("99_归档\\test_backup")
        cfg = panel.prepare_parameters()
        self.assertEqual(cfg["backup_dir"], "99_归档\\test_backup")
        self.assertEqual(
            self.app.config_data["entry_history"]["patch.backup_dir"][0],
            "99_归档\\test_backup",
        )


if __name__ == "__main__":
    unittest.main()