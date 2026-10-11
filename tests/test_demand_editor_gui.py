# -*- coding: utf-8 -*-

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG


class DemandEditorGuiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.errors = []
        self.root.report_callback_exception = (
            lambda error_type, error, traceback: self.errors.append(error)
        )
        self.path = Path(self.directory.name) / "config.json"
        config = deep_merge_config(DEFAULT_CONFIG, {})
        config["merge"]["source_folder"] = self.directory.name
        config["merge"]["demand_file_list_text"] = "|missing\nREADME.md"
        self.source = Path(self.directory.name) / "README.md"
        self.source.write_text("SOURCE", encoding="utf-8")
        manager = ConfigSaveManager(config, self.path, needs_write=True)
        self.assertTrue(manager.save().persisted)
        with patch("app.raise_and_focus"):
            self.app = ZipatchApp(self.root, config_path=self.path)
        self.panel = self.app.current_panel
        self.session = self.panel.open_demand_merge_dialog()

    def destroy_root(self):
        if hasattr(self, "session") and not self.session._destroyed:
            self.session.destroy()
        self.root.destroy()

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [])

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "后台检查未在测试等待期限内完成")
        self.root.update()

    def test_open_checks_without_configuration_write(self):
        before = dict(self.app.config_manager.metrics)
        self.wait_until(
            lambda: all(rule.match is not None for rule in self.session.rules)
        )
        self.assertTrue(
            self.session.rule_text.tag_ranges("demand_disabled")
        )
        self.assertTrue(
            self.session.rule_text.tag_ranges("demand_success")
        )
        self.assertEqual(self.app.config_manager.metrics, before)

    def test_edit_reuses_unchanged_match_but_invalidates_changed_rule(self):
        self.wait_until(
            lambda: all(rule.match is not None for rule in self.session.rules)
        )
        previous = self.session.rules[1]
        text = self.session.rule_text
        text.insert("1.0", "missing-file\n")
        self.root.update()
        self.assertIs(self.session.rules[2], previous)
        self.assertIsNone(self.session.rules[0].match)
        self.assertFalse(
            self.session.rule_text.tag_ranges("demand_success")
        )

    def test_final_save_can_preserve_red_rule_expression(self):
        text = self.session.rule_text
        text.delete("1.0", "end")
        text.insert("1.0", '"missing-file"')
        self.root.update()
        with patch("ui.demand_editor.safe_show_info"):
            self.assertTrue(self.session.start_final("save"))
            self.wait_until(lambda: not self.session.busy)
        self.assertEqual(
            self.panel.cfg["demand_file_list_text"], "missing-file",
        )
        self.assertTrue(text.tag_ranges("demand_error"))
        self.assertEqual(str(text.tag_ranges("demand_error")[0]), "1.0")
        self.assertEqual(self.session.rules[0].raw, "missing-file")
        self.assertTrue(self.session.rules[0].match.issue)
        self.assertIn("暂不能执行", self.session.status.get())
        self.assertEqual(self.app.commits.operation_depth, 0)
        self.root.update()
        self.assertTrue(text.tag_ranges("demand_error"))

    def test_final_execution_rejects_red_rules(self):
        text = self.session.rule_text
        text.delete("1.0", "end")
        text.insert("1.0", "missing-file")
        self.root.update()
        with (
            patch("ui.demand_editor.safe_show_error"),
            patch.object(self.panel, "execute_demand_merge") as execute,
        ):
            self.assertTrue(self.session.start_final("execute"))
            self.wait_until(lambda: not self.session.busy)
        execute.assert_not_called()
        self.assertEqual(self.app.commits.operation_depth, 0)


if __name__ == "__main__":
    unittest.main()