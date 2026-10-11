# -*- coding: utf-8 -*-

import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG
from services.demand_rules import (
    DemandCheckCancelled,
    ParsedDemandRule,
    _wildcard_files,
    inspect_demand_rule,
)
from ui.favorites import FavoriteTextBoxController


class DemandRemainingRuleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "docs").mkdir()
        (self.root / "README.md").write_text("README", encoding="utf-8")
        (self.root / "other.txt").write_text("OTHER", encoding="utf-8")
        self.cancel = Event()

    def revisiting_rule(self):
        # 绝对规则允许显式父目录段。
        # 直接测试已经解析的规则，使目录状态推进测试不依赖宿主盘符格式。
        return ParsedDemandRule(
            expression="absolute-root\\d*\\..\\*.md",
            disabled=False,
            relative=False,
            root=str(self.root),
            segments=("d*", "..", "*.md"),
            wildcard=True,
        )

    def test_revisited_directory_lazily_checks_newly_matched_entry_once(self):
        import services.demand_rules as module
        original_scandir = module.os.scandir
        original_stat = module.os.stat
        opened = []
        checked = []

        def enumerate_directory(path):
            opened.append(os.path.abspath(path))
            return original_scandir(path)

        def query_attributes(path, *args, **kwargs):
            checked.append(os.path.abspath(path))
            return original_stat(path, *args, **kwargs)

        with (
            patch("services.demand_rules.os.scandir", side_effect=enumerate_directory),
            patch("services.demand_rules.os.stat", side_effect=query_attributes),
        ):
            result = _wildcard_files(
                self.revisiting_rule(), self.root, self.cancel,
            )

        self.assertTrue(result.completed, result.issue)
        self.assertEqual(result.files, (str(self.root / "README.md"),))
        self.assertEqual(opened.count(str(self.root)), 1)
        self.assertEqual(checked.count(str(self.root / "README.md")), 1)
        self.assertNotIn(str(self.root / "other.txt"), checked)

    def test_revisited_entry_attribute_failure_rejects_whole_selection(self):
        import services.demand_rules as module
        original_stat = module.os.stat

        def query_attributes(path, *args, **kwargs):
            if os.path.abspath(path) == str(self.root / "README.md"):
                raise PermissionError("revisited entry denied")
            return original_stat(path, *args, **kwargs)

        with patch("services.demand_rules.os.stat", side_effect=query_attributes):
            result = _wildcard_files(
                self.revisiting_rule(), self.root, self.cancel,
            )

        self.assertFalse(result.completed)
        self.assertEqual(result.files, ())
        self.assertIn("revisited entry denied", result.issue)

    def test_cancellation_after_revisited_attribute_query_returns_no_result(self):
        import services.demand_rules as module
        original_stat = module.os.stat

        def query_attributes(path, *args, **kwargs):
            attributes = original_stat(path, *args, **kwargs)
            if os.path.abspath(path) == str(self.root / "README.md"):
                self.cancel.set()
            return attributes

        with patch("services.demand_rules.os.stat", side_effect=query_attributes):
            with self.assertRaises(DemandCheckCancelled):
                _wildcard_files(
                    self.revisiting_rule(), self.root, self.cancel,
                )

    def test_exact_file_with_directory_suffix_is_never_selected(self):
        for expression in ("README.md\\", "README.md/", "README.md\\."):
            with self.subTest(expression=expression):
                result = inspect_demand_rule(
                    expression, str(self.root), self.cancel,
                )
                self.assertFalse(result.completed)
                self.assertEqual(result.files, ())
                self.assertTrue(result.issue)

    def test_absolute_parent_rule_uses_same_revisited_directory_chain(self):
        if os.name != "nt":
            self.skipTest("完整盘符绝对规则须在 Windows 验证")
        expression = str(self.root / "d*" / ".." / "*.md")
        result = inspect_demand_rule(
            expression, "", self.cancel,
        )
        self.assertTrue(result.completed, result.issue)
        self.assertEqual(result.files, (str(self.root / "README.md"),))


class DemandRemainingGuiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.errors = []
        self.root.report_callback_exception = (
            lambda kind, error, traceback: self.errors.append(error)
        )
        self.addCleanup(self.cleanup_root)
        self.source = Path(self.directory.name)
        (self.source / "a.md").write_text("A", encoding="utf-8")
        (self.source / "b.md").write_text("B", encoding="utf-8")
        self.path = self.source / "config.json"
        config = deep_merge_config(DEFAULT_CONFIG, {})
        config["merge"]["source_folder"] = str(self.source)
        config["merge"]["demand_file_list_text"] = "a.md\n\n"
        manager = ConfigSaveManager(config, self.path, needs_write=True)
        self.assertTrue(manager.save().persisted)
        with patch("app.raise_and_focus"):
            self.app = ZipatchApp(self.root, config_path=self.path)
        self.panel = self.app.current_panel
        self.session = self.panel.open_demand_merge_dialog()
        self.wait_until(lambda: all(
            rule.match is not None for rule in self.session.rules
        ))

    def cleanup_root(self):
        if hasattr(self, "session"):
            self.session.destroy()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [])

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "测试等待期限内任务未完成")
        self.root.update()

    def replace_rules(self, content):
        text = self.session.rule_text
        text.delete("1.0", "end")
        text.insert("1.0", content)
        self.root.update()

    def test_replace_all_preserves_trailing_physical_lines_without_saving(self):
        text = self.session.rule_text
        before = dict(self.app.config_manager.metrics)
        tool = text._text_editor_controller
        tool.find_var.set("a.md")
        tool.replace_var.set("b.md")
        tool.replace_all()
        self.root.update()

        self.assertEqual(text.get("1.0", "end-1c"), "b.md\n\n")
        self.assertEqual(
            [rule.raw for rule in self.session.rules], ["b.md", "", ""],
        )
        self.assertEqual(self.panel.cfg["demand_file_list_text"], "a.md\n\n")
        self.assertEqual(self.app.config_manager.metrics, before)
        self.wait_until(lambda: all(
            rule.match is not None for rule in self.session.rules
        ))
        self.assertTrue(text.tag_ranges("demand_success"))

    def test_favorite_preserves_cleaned_expression_and_trailing_physical_lines(self):
        self.replace_rules('"a.md"\n\n')
        favorite = FavoriteTextBoxController(
            self.session.dialog,
            tk.Frame(self.session.dialog),
            self.session.rule_text,
            self.app.commits,
            "demand_file_list",
            self.session.rule_text._content_operation_completed,
        )
        with patch("ui.favorites.ask_favorite_name", return_value="physical lines"):
            favorite.save_current_text_as_favorite()
        self.root.update()

        expected = {"name": "physical lines", "content": "a.md\n\n"}
        self.assertEqual(
            self.app.config_data["favorites"]["demand_file_list"], [expected],
        )
        self.assertEqual(
            self.session.rule_text.get("1.0", "end-1c"), "a.md\n\n",
        )
        self.assertEqual(self.panel.cfg["demand_file_list_text"], "a.md\n\n")
        loaded, _ = load_user_config(self.path)
        self.assertEqual(loaded["favorites"]["demand_file_list"], [expected])

    def test_save_phase_rejects_cancel_and_keeps_lifecycle_until_return(self):
        self.replace_rules("b.md\n\n")
        original_accept = self.app.commits.accept_draft
        observations = []

        def accept(config, values, parent):
            observations.append((
                self.session.busy,
                self.app.commits.operation_depth,
                str(self.session.cancel_button.cget("state")),
                self.session.cancel_final(),
            ))
            return original_accept(config, values, parent)

        with patch.object(self.app.commits, "accept_draft", side_effect=accept):
            self.assertTrue(self.session.start_final("save"))
            self.wait_until(lambda: not self.session.busy)

        self.assertEqual(observations, [(True, 1, "disabled", False)])
        self.assertEqual(self.panel.cfg["demand_file_list_text"], "b.md\n\n")
        self.assertEqual(self.app.commits.operation_depth, 0)
        self.assertEqual(str(self.session.rule_text.cget("state")), "normal")
        self.assertEqual(str(self.session.cancel_button.cget("state")), "disabled")

    def test_save_failure_feedback_cannot_cancel_or_release_operation(self):
        original_disk = self.path.read_bytes()
        self.replace_rules("b.md\n\n")
        observations = []

        def report(*args, **kwargs):
            observations.append((
                self.session.busy,
                self.app.commits.operation_depth,
                str(self.session.cancel_button.cget("state")),
                self.session.cancel_final(),
            ))
            self.assertFalse(self.session.close())

        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error", side_effect=report),
        ):
            self.assertTrue(self.session.start_final("save"))
            self.wait_until(lambda: not self.session.busy)

        self.assertEqual(observations, [(True, 1, "disabled", False)])
        self.assertEqual(self.path.read_bytes(), original_disk)
        self.assertEqual(self.panel.cfg["demand_file_list_text"], "a.md\n\n")
        self.assertEqual(
            self.session.rule_text.get("1.0", "end-1c"), "b.md\n\n",
        )
        self.assertIn("保存失败", self.session.status.get())
        self.assertEqual(self.app.commits.operation_depth, 0)
        self.assertEqual(str(self.session.rule_text.cget("state")), "normal")

    def test_cancel_before_final_check_still_prevents_save(self):
        original_disk = self.path.read_bytes()
        self.replace_rules("b.md")
        self.assertTrue(self.session.start_final("save"))
        self.assertTrue(self.session.cancel_final())
        self.wait_until(lambda: not self.session.busy)
        self.assertEqual(self.path.read_bytes(), original_disk)
        self.assertEqual(self.panel.cfg["demand_file_list_text"], "a.md\n\n")
        self.assertEqual(self.app.commits.operation_depth, 0)


if __name__ == "__main__":
    unittest.main()