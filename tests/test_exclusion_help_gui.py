# -*- coding: utf-8 -*-

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import TclError
from unittest.mock import patch

from ui.dialogs import create_managed_text_box
from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG, THEME
from core.exclusion_rules import analyze_exclusion_list
from core.text_io import replace_text_keep_undo
from ui.config_editing import ConfigCommitController
from ui.exclusion_editor import (
    EXCLUSION_DISABLED_BACKGROUND,
    EXCLUSION_DISABLED_FOREGROUND,
    EXCLUSION_RULES_TEXT,
    EXCLUSION_WARNING_BACKGROUND,
    ExclusionDraftSession,
    create_exclusion_dialog,
    show_exclusion_rules,
)
from ui.window_manager import close_registered_popup


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ExclusionHelpGuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.callback_errors = []
        self.root.report_callback_exception = self.record_callback_error
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.manager = ConfigSaveManager(
            self.config, self.path, needs_write=True,
        )
        self.assertTrue(self.manager.save().persisted)
        self.commits = ConfigCommitController(self.root, self.manager)

    def destroy_root(self):
        try:
            self.root.destroy()
        except TclError:
            pass

    def record_callback_error(self, error_type, error, traceback):
        self.callback_errors.append(error)

    def tearDown(self):
        self.assertEqual(self.callback_errors, [])

    def emergency_close(self, owner):
        self.callback_errors.append(
            AssertionError("窗口未在预期交互后关闭"),
        )
        for child in owner.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()

    def verify_help_close(self, action):
        owner = tk.Toplevel(self.root)
        owner.grab_set()
        original = self.path.read_bytes()
        metrics = dict(self.manager.metrics)
        observed = []

        def inspect_and_close():
            dialog = next(
                child for child in owner.winfo_children()
                if isinstance(child, tk.Toplevel)
            )
            widgets = list(descendants(dialog))
            text = next(
                widget for widget in widgets if isinstance(widget, tk.Text)
            )
            self.assertEqual(text.get("1.0", "end-1c"), EXCLUSION_RULES_TEXT)
            self.assertEqual(str(text.cget("state")), "disabled")
            self.assertFalse(self.root.tk.getboolean(text.cget("undo")))
            self.assertTrue(
                any(isinstance(widget, tk.Scrollbar) for widget in widgets)
            )
            self.assertIs(owner.grab_current(), dialog)
            observed.append(dialog.title())

            if action == "button":
                button = next(
                    widget for widget in widgets
                    if isinstance(widget, tk.Button)
                    and widget.cget("text") == "关闭"
                )
                button.invoke()
            elif action == "escape":
                dialog.focus_force()
                dialog.event_generate("<Escape>")
            else:
                dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))

        callback = owner.after(50, inspect_and_close)
        timeout = owner.after(3000, lambda: self.emergency_close(owner))
        try:
            show_exclusion_rules(owner)
        finally:
            owner.after_cancel(callback)
            owner.after_cancel(timeout)

        self.assertEqual(observed, ["排除名单 - 填写规则"])
        self.assertTrue(owner.winfo_exists())
        self.assertIs(owner.grab_current(), owner)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.manager.metrics, metrics)
        owner.destroy()

    def test_help_button_close_restores_grab_without_saving(self):
        self.verify_help_close("button")

    def test_help_escape_restores_grab_without_saving(self):
        self.verify_help_close("escape")

    def test_help_title_close_restores_grab_without_saving(self):
        self.verify_help_close("title")

    def make_highlight_session(self, config):
        dialog = tk.Toplevel(self.root)
        widgets = {
            field: create_managed_text_box(
                dialog, field, config[field],
                commits=self.commits, enable_favorites=False,
                wrap_config_key=f"scan.{field}",
            )
            for field in ("exclude_folders", "exclude_files")
        }
        status = tk.StringVar(master=dialog, value="")
        session = ExclusionDraftSession(
            dialog, self.commits, config, widgets, status,
        )
        self.root.update()
        return session, widgets, status

    def test_initial_highlights_preserve_raw_text_and_input_lines(self):
        config = {
            "exclude_folders": "\n|cache\nfolder",
            "exclude_files": '\n""\n"bad|name"\n" README*"\n"|*.log"',
        }
        original = self.path.read_bytes()
        metrics = dict(self.manager.metrics)
        session, widgets, status = self.make_highlight_session(config)

        for field, widget in widgets.items():
            self.assertEqual(widget.get("1.0", "end-1c"), config[field])
        self.assertEqual(
            str(widgets["exclude_folders"].tag_ranges("exclusion_disabled")[0]),
            "2.0",
        )
        file_widget = widgets["exclude_files"]
        for tag, line in (
            ("exclusion_error", "3.0"),
            ("exclusion_warning", "4.0"),
            ("exclusion_disabled", "5.0"),
        ):
            self.assertEqual(str(file_widget.tag_ranges(tag)[0]), line)
        self.assertEqual(
            file_widget.tag_cget("exclusion_disabled", "foreground"),
            EXCLUSION_DISABLED_FOREGROUND,
        )
        self.assertEqual(
            file_widget.tag_cget("exclusion_disabled", "background"),
            EXCLUSION_DISABLED_BACKGROUND,
        )
        self.assertNotEqual(EXCLUSION_DISABLED_BACKGROUND, THEME["bg_input"])
        self.assertNotEqual(EXCLUSION_DISABLED_FOREGROUND, THEME["fg_dim"])
        self.assertEqual(self.manager.metrics, metrics)
        self.assertEqual(self.path.read_bytes(), original)
        session.destroy()

    def test_edits_update_current_highlights_without_clearing_other_list(self):
        config = {
            "exclude_folders": "|cache",
            "exclude_files": "*.log",
        }
        original = self.path.read_bytes()
        metrics = dict(self.manager.metrics)
        session, widgets, status = self.make_highlight_session(config)
        file_widget = widgets["exclude_files"]
        file_widget.delete("1.0", "end")
        file_widget.insert("1.0", "\n<none>\n README*\n|*.log")
        self.root.update()

        for tag, line in (
            ("exclusion_error", "2.0"),
            ("exclusion_warning", "3.0"),
            ("exclusion_disabled", "4.0"),
        ):
            self.assertEqual(str(file_widget.tag_ranges(tag)[0]), line)
        self.assertTrue(widgets["exclude_folders"].tag_ranges("exclusion_disabled"))
        self.assertEqual(
            file_widget.get("1.0", "end-1c"),
            "\n<none>\n README*\n|*.log",
        )
        self.assertEqual(status.get(), "已修改，尚未保存；高亮对应当前原文")

        file_widget.delete("1.0", "end")
        file_widget.insert("1.0", "correct.txt")
        self.root.update()
        for tag in ("exclusion_error", "exclusion_warning", "exclusion_disabled"):
            self.assertEqual(file_widget.tag_ranges(tag), ())
        self.assertTrue(widgets["exclude_folders"].tag_ranges("exclusion_disabled"))
        self.assertEqual(config["exclude_files"], "*.log")
        self.assertEqual(self.manager.metrics, metrics)
        self.assertEqual(self.path.read_bytes(), original)
        session.destroy()

    def test_undo_redo_and_backfill_rebuild_highlights_from_raw_text(self):
        config = {"exclude_folders": "", "exclude_files": "*.log"}
        original = self.path.read_bytes()
        session, widgets, status = self.make_highlight_session(config)
        widget = widgets["exclude_files"]
        widget.edit_reset()
        raw = '\n"|*.log"\n" README*"\n<none>'
        replace_text_keep_undo(widget, raw)
        widget._content_operation_completed(raw)
        self.root.update()
        for tag, line in (
            ("exclusion_disabled", "2.0"),
            ("exclusion_warning", "3.0"),
            ("exclusion_error", "4.0"),
        ):
            self.assertEqual(str(widget.tag_ranges(tag)[0]), line)
        self.assertEqual(widget.get("1.0", "end-1c"), raw)

        widget._text_editor_controller.undo()
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), "*.log")
        for tag in ("exclusion_error", "exclusion_warning", "exclusion_disabled"):
            self.assertEqual(widget.tag_ranges(tag), ())

        widget._text_editor_controller.redo()
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), raw)
        self.assertTrue(widget.tag_ranges("exclusion_disabled"))
        self.assertTrue(widget.tag_ranges("exclusion_warning"))
        self.assertTrue(widget.tag_ranges("exclusion_error"))
        self.assertEqual(config["exclude_files"], "*.log")
        self.assertEqual(self.path.read_bytes(), original)
        session.destroy()

    def test_saved_rules_reopen_with_highlights_without_another_save(self):
        config = self.config["scan"]
        session, widgets, status = self.make_highlight_session(config)
        widgets["exclude_folders"].delete("1.0", "end")
        widgets["exclude_folders"].insert("1.0", '\n"|cache"')
        widgets["exclude_files"].delete("1.0", "end")
        widgets["exclude_files"].insert("1.0", '\n" README*"\n"|*.log"')
        self.root.update()
        before = self.manager.metrics["successful_replaces"]
        with patch(
            "ui.exclusion_editor.confirm_exclusion_diagnostics",
            return_value=True,
        ):
            self.assertTrue(session.save_and_close())
        self.assertEqual(self.manager.metrics["successful_replaces"] - before, 1)
        self.assertEqual(config["exclude_folders"], "|cache")
        self.assertEqual(config["exclude_files"], " README*\n|*.log")

        original = self.path.read_bytes()
        metrics = dict(self.manager.metrics)
        reopened, reopened_widgets, status = self.make_highlight_session(config)
        self.assertTrue(
            reopened_widgets["exclude_folders"].tag_ranges("exclusion_disabled")
        )
        self.assertEqual(
            str(reopened_widgets["exclude_files"].tag_ranges("exclusion_warning")[0]),
            "1.0",
        )
        self.assertEqual(
            str(reopened_widgets["exclude_files"].tag_ranges("exclusion_disabled")[0]),
            "2.0",
        )
        self.assertEqual(self.manager.metrics, metrics)
        self.assertEqual(self.path.read_bytes(), original)
        reopened.destroy()

    def test_main_header_help_entry_and_shared_color_legend(self):
        observed = []
        original = self.path.read_bytes()
        metrics = dict(self.manager.metrics)

        def inspect_main():
            dialog = next(
                child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel)
                and child.title() == "名单界面测试"
            )
            widgets = list(descendants(dialog))
            labels = [
                widget.cget("text")
                for widget in widgets if isinstance(widget, tk.Label)
            ]
            self.assertIn(
                "快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。",
                labels,
            )
            for description in (
                "红：无效，阻止保存",
                "黄：有效，有空格风险",
                "灰：临时停用，不参与排除",
            ):
                self.assertIn(description, labels)
            self.assertFalse(
                any("【填写规则】" in label for label in labels)
            )
            colors = {
                widget.cget("background")
                for widget in widgets
                if isinstance(widget, tk.Label)
                and widget.cget("text") == "  "
            }
            self.assertTrue({
                THEME["danger_bg"],
                EXCLUSION_WARNING_BACKGROUND,
                EXCLUSION_DISABLED_BACKGROUND,
            }.issubset(colors))
            help_button = next(
                widget for widget in widgets
                if isinstance(widget, tk.Button)
                and widget.cget("text") == "填写规则"
            )

            def close_help():
                help_dialog = next(
                    child for child in dialog.winfo_children()
                    if isinstance(child, tk.Toplevel)
                )
                observed.append(help_dialog.title())
                self.assertTrue(close_registered_popup(help_dialog))

            callback = dialog.after(50, close_help)
            try:
                help_button.invoke()
            finally:
                dialog.after_cancel(callback)
            self.assertTrue(dialog.winfo_exists())
            self.assertIs(dialog.grab_current(), dialog)
            self.assertTrue(close_registered_popup(dialog))

        callback = self.root.after(50, inspect_main)
        timeout = self.root.after(
            3000, lambda: self.emergency_close(self.root),
        )
        try:
            saved = create_exclusion_dialog(
                self.root, "名单界面测试", self.config["scan"],
                self.commits, "scan",
            )
        finally:
            self.root.after_cancel(callback)
            self.root.after_cancel(timeout)

        self.assertFalse(saved)
        self.assertEqual(observed, ["排除名单 - 填写规则"])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.manager.metrics, metrics)


if __name__ == "__main__":
    unittest.main()