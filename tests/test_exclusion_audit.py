# -*- coding: utf-8 -*-

import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import TclError
from unittest.mock import patch

from core.config import ConfigSaveManager, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG
from core.exclusion_rules import analyze_exclusion_list
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.exclusion_editor import (
    ExclusionDraftSession, confirm_exclusion_diagnostics,
)
from ui.favorites import FavoriteTextBoxController
from ui.window_manager import register_popup, prepare_all_for_exit


class ExclusionLoadAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"

    def write_source(self, source):
        self.path.write_text(
            json.dumps(source, ensure_ascii=False), encoding="utf-8",
        )
        return self.path.read_bytes()

    def assert_rejected_without_write(self, source, expected_message):
        original = self.write_source(source)
        with self.assertRaises(ValueError) as caught:
            load_user_config(self.path)
        self.assertIn(expected_message, str(caught.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.path.with_name("config.json.tmp").exists())

    def test_invalid_exclusion_field_types_do_not_become_defaults(self):
        for page in ("scan", "merge"):
            for field in ("exclude_folders", "exclude_files"):
                for invalid in (None, [], {}, 1, False):
                    with self.subTest(page=page, field=field, invalid=invalid):
                        self.assert_rejected_without_write(
                            {page: {field: invalid}}, f"{page}.{field}",
                        )

    def test_invalid_configuration_sections_preserve_file(self):
        self.assert_rejected_without_write([], "配置顶层")
        for page in ("scan", "merge"):
            for invalid in (None, [], "invalid"):
                with self.subTest(page=page, invalid=invalid):
                    self.assert_rejected_without_write(
                        {page: invalid}, f"配置 {page}",
                    )
        self.assert_rejected_without_write(
            {"favorites": []}, "配置 favorites",
        )

    def test_invalid_exclusion_favorite_structures_preserve_file(self):
        for key in (
            "scan_exclude_folders", "scan_exclude_files",
            "merge_exclude_folders", "merge_exclude_files",
        ):
            with self.subTest(key=key, case="not list"):
                self.assert_rejected_without_write(
                    {"favorites": {key: {}}}, key,
                )
            for entry in (
                None,
                {},
                {"name": "sample", "content": None},
                {"name": 1, "content": "cache"},
                {"name": "sample", "content": []},
            ):
                with self.subTest(key=key, entry=entry):
                    self.assert_rejected_without_write(
                        {"favorites": {key: [entry]}},
                        f"{key} 第 1 项",
                    )

    def test_corrupt_json_and_encoding_do_not_rebuild_configuration(self):
        for content in (b"{broken", b"\xff\xfe\x00"):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(ValueError):
                    load_user_config(self.path)
                self.assertEqual(self.path.read_bytes(), content)

    def test_filesystem_read_failure_is_not_reported_as_success(self):
        original = self.write_source({})
        with patch(
            "core.config.Path.open",
            side_effect=PermissionError("read denied"),
        ):
            with self.assertRaises(PermissionError):
                load_user_config(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_loading_preserves_rule_and_favorite_text_without_interpretation(self):
        source = deep_merge_config(DEFAULT_CONFIG, {})
        raw = '\n"src/cache/"\n|bad//<name>\n README* \n<none>'
        source["scan"]["exclude_files"] = raw
        source["favorites"]["scan_exclude_files"] = [
            {"name": "raw rules", "content": raw},
        ]
        original = self.write_source(source)
        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(loaded["scan"]["exclude_files"], raw)
        self.assertEqual(
            loaded["favorites"]["scan_exclude_files"][0]["content"], raw,
        )
        self.assertEqual(self.path.read_bytes(), original)


class ExclusionMatchingAuditTests(unittest.TestCase):
    def test_long_pattern_does_not_depend_on_python_recursion_depth(self):
        segments = ("folder",) * 1500 + ("README",)
        result = analyze_exclusion_list("\\" + "\\".join(segments), "file")
        self.assertFalse(result.has_errors)
        self.assertTrue(result.matches(segments))
        self.assertFalse(result.matches(("extra",) + segments))

    def test_multiple_double_stars_preserve_zero_and_multiple_layer_semantics(self):
        result = analyze_exclusion_list("\\a\\**\\b\\**\\README", "file")
        self.assertFalse(result.has_errors)
        for parts in (
            ("a", "b", "README"),
            ("a", "x", "b", "README"),
            ("a", "b", "y", "z", "README"),
            ("a", "x", "b", "y", "README"),
        ):
            with self.subTest(parts=parts):
                self.assertTrue(result.matches(parts))
        self.assertFalse(result.matches(("x", "a", "b", "README")))
        self.assertFalse(result.matches(("a", "b", "README.txt")))

    def test_trailing_double_star_does_not_turn_file_into_directory(self):
        files = analyze_exclusion_list("\\tools\\**\\**", "file")
        directories = analyze_exclusion_list("\\tools\\**\\**", "directory")
        self.assertFalse(files.matches(("tools",)))
        self.assertTrue(files.matches(("tools", "file.txt")))
        self.assertTrue(files.matches(("tools", "sub", "file.txt")))
        self.assertTrue(directories.matches(("tools",)))
        self.assertTrue(directories.matches(("tools", "sub")))
        self.assertFalse(directories.matches(()))


class ExclusionGuiAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.root = tk.Tk()
        except TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.callback_errors = []
        self.root.report_callback_exception = self.record_callback_error

        self.path = Path(self.directory.name) / "config.json"
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.config["scan"]["exclude_folders"] = "cache"
        self.config["scan"]["exclude_files"] = "*.log"
        self.manager = ConfigSaveManager(
            self.config, self.path, needs_write=True,
        )
        self.assertTrue(self.manager.save().persisted)
        self.commits = ConfigCommitController(self.root, self.manager)
        self.dialog = tk.Toplevel(self.root)
        self.widgets = {
            field: create_managed_text_box(
                self.dialog, field, self.config["scan"][field],
                commits=self.commits, enable_favorites=False,
                wrap_config_key=f"scan.{field}",
            )
            for field in ("exclude_folders", "exclude_files")
        }
        self.status = tk.StringVar(master=self.dialog, value="尚未校验")
        self.session = ExclusionDraftSession(
            self.dialog, self.commits, self.config["scan"],
            self.widgets, self.status,
        )
        register_popup(
            self.dialog, self.session.close,
            prepare_close=self.session.prepare_close,
            focus_on_register=False,
        )
        self.root.update()

    def destroy_root(self):
        try:
            self.root.destroy()
        except TclError:
            pass

    def record_callback_error(self, error_type, error, traceback):
        self.callback_errors.append(error)

    def tearDown(self):
        if hasattr(self, "callback_errors"):
            self.assertEqual(self.callback_errors, [])

    def set_text(self, field, content):
        widget = self.widgets[field]
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        self.root.update()

    def make_favorite_controller(self):
        return FavoriteTextBoxController(
            self.dialog, tk.Frame(self.dialog),
            self.widgets["exclude_files"], self.commits,
            "scan_exclude_files",
            self.widgets["exclude_files"]._content_operation_completed,
        )

    def test_red_save_is_blocked_even_if_confirmation_returns_true(self):
        original = self.path.read_bytes()
        self.set_text("exclude_folders", "src/cache/")
        self.set_text("exclude_files", "<none>")
        with patch(
            "ui.exclusion_editor.confirm_exclusion_diagnostics",
            return_value=True,
        ):
            self.assertFalse(self.session.save_and_close())
        self.assertTrue(self.dialog.winfo_exists())
        self.assertEqual(self.config["scan"]["exclude_folders"], "cache")
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        self.assertEqual(self.path.read_bytes(), original)

    def test_red_favorite_is_blocked_even_if_confirmation_returns_true(self):
        with patch(
            "ui.exclusion_editor.confirm_exclusion_diagnostics",
            return_value=True,
        ):
            self.assertIsNone(
                self.session.prepare_favorite("exclude_files", "<none>"),
            )
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")

    def test_renaming_validates_favorite_without_backfilling_current_draft(self):
        self.set_text("exclude_files", "UNFINISHED*")
        value = {"name": "old", "content": '"src/cache/*.log/"'}
        self.manager.accept(
            self.config["favorites"], {"scan_exclude_files": [value]},
        )
        self.assertTrue(self.manager.save().persisted)
        controller = self.make_favorite_controller()
        with (
            patch("ui.favorites.ask_favorite_name", return_value="new"),
            patch(
                "ui.exclusion_editor.confirm_exclusion_diagnostics",
                return_value=True,
            ),
        ):
            controller.rename_favorite(value)
        self.assertEqual(
            self.widgets["exclude_files"].get("1.0", "end-1c"),
            "UNFINISHED*",
        )
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        self.assertEqual(
            self.config["favorites"]["scan_exclude_files"],
            [{"name": "new", "content": "src\\cache\\*.log"}],
        )

    def test_invalid_favorite_rename_keeps_original_favorite_and_disk(self):
        value = {"name": "old", "content": "\n\n<none>"}
        self.manager.accept(
            self.config["favorites"], {"scan_exclude_files": [value]},
        )
        self.assertTrue(self.manager.save().persisted)
        original = self.path.read_bytes()
        observed = []

        def confirm(parent, named_results):
            observed.extend(named_results)
            return True

        controller = self.make_favorite_controller()
        with (
            patch("ui.favorites.ask_favorite_name", return_value="new"),
            patch(
                "ui.exclusion_editor.confirm_exclusion_diagnostics",
                side_effect=confirm,
            ),
        ):
            controller.rename_favorite(value)
        self.assertEqual(
            self.config["favorites"]["scan_exclude_files"], [value],
        )
        self.assertEqual(self.path.read_bytes(), original)
        diagnostic = observed[0][1].diagnostics[0]
        self.assertEqual(diagnostic.input_line, 3)
        self.assertEqual(diagnostic.display_line, 3)
        self.assertEqual(
            self.widgets["exclude_files"].get("1.0", "end-1c"), "*.log",
        )

    def test_standardization_keeps_diagnostics_after_event_loop_returns(self):
        self.set_text("exclude_files", '\n""\n" bad|name"\n" README*"')
        with patch(
            "ui.exclusion_editor.confirm_exclusion_diagnostics",
            return_value=False,
        ):
            self.assertFalse(self.session.save_and_close())
        self.root.update()
        widget = self.widgets["exclude_files"]
        self.assertEqual(
            widget.get("1.0", "end-1c"), " bad|name\n README*",
        )
        self.assertEqual(str(widget.tag_ranges("exclusion_error")[0]), "1.0")
        self.assertEqual(str(widget.tag_ranges("exclusion_warning")[0]), "2.0")
        self.set_text("exclude_files", "correct.txt")
        self.assertEqual(widget.tag_ranges("exclusion_error"), ())
        self.assertEqual(widget.tag_ranges("exclusion_warning"), ())

    def run_real_diagnostic(self, content, action, closing):
        result = analyze_exclusion_list(content, "file")
        self.dialog.grab_set()
        self.root._zipatch_closing = closing
        observed = []

        def choose():
            children = [
                child for child in self.dialog.winfo_children()
                if isinstance(child, tk.Toplevel)
            ]
            self.assertEqual(len(children), 1)
            diagnostic = children[0]
            observed.append(diagnostic.title())
            if action == "escape":
                diagnostic.focus_force()
                diagnostic.event_generate("<Escape>")
            elif action == "title_close":
                command = diagnostic.protocol("WM_DELETE_WINDOW")
                diagnostic.tk.call(command)
            else:
                buttons = []

                def collect(widget):
                    if isinstance(widget, tk.Button):
                        buttons.append(widget)
                    for child in widget.winfo_children():
                        collect(child)

                collect(diagnostic)
                button = next(
                    button for button in buttons if button.cget("text") == action
                )
                button.invoke()

        callback = self.dialog.after(30, choose)
        timeout = self.dialog.after(2000, self.cancel_remaining_diagnostic)
        try:
            accepted = confirm_exclusion_diagnostics(
                self.dialog, (("文件名单", result),),
            )
        finally:
            self.dialog.after_cancel(callback)
            self.dialog.after_cancel(timeout)
            self.root._zipatch_closing = False
        self.assertEqual(len(observed), 1)
        self.assertIs(self.dialog.grab_current(), self.dialog)
        self.assertTrue(self.dialog.winfo_exists())
        return accepted

    def cancel_remaining_diagnostic(self):
        self.callback_errors.append(
            AssertionError("诊断窗口未在预期操作后关闭"),
        )
        for child in self.dialog.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()

    def test_real_diagnostic_escape_and_title_close_during_exit_preparation(self):
        for action in ("escape", "title_close"):
            with self.subTest(action=action):
                self.assertFalse(
                    self.run_real_diagnostic("<none>", action, closing=True),
                )

    def test_real_warning_confirm_and_return_buttons(self):
        self.assertTrue(
            self.run_real_diagnostic(" README*", "确认保存", closing=False),
        )
        self.assertFalse(
            self.run_real_diagnostic(" README*", "返回检查", closing=False),
        )

    def test_real_red_return_button(self):
        self.assertFalse(
            self.run_real_diagnostic("<none>\n README*", "返回修改", closing=False),
        )

    def test_exit_prepare_with_invalid_draft_keeps_window_and_configuration(self):
        original = self.path.read_bytes()
        self.set_text("exclude_files", "<none>")
        self.root._zipatch_closing = True
        try:
            with (
                patch("ui.dialogs.messagebox.askyesnocancel", return_value=True),
                patch(
                    "ui.exclusion_editor.confirm_exclusion_diagnostics",
                    return_value=True,
                ),
            ):
                self.assertFalse(prepare_all_for_exit(self.root))
        finally:
            self.root._zipatch_closing = False
        self.assertTrue(self.dialog.winfo_exists())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        self.assertEqual(self.commits.operation_depth, 0)
        self.assertFalse(self.session._close_in_progress)


if __name__ == "__main__":
    unittest.main()