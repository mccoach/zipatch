# -*- coding: utf-8 -*-

"""V1.2 收口、模态归还、收藏浮窗和正常撤销无操作回归。"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG
from core.rule_lists import analyze_rule_list
from core.text_io import replace_text_keep_undo
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.exclusion_editor import ExclusionDraftSession
from ui.favorites import FavoriteTextBoxController
from ui.newline_settings import NewlineDraftSession
from ui.window_manager import (
    register_popup, take_modal_control, validate_temporary_popups,
)


class ListV12RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.errors = []
        self.root.report_callback_exception = (
            lambda kind, error, trace: self.errors.append(error)
        )
        self.addCleanup(self.cleanup_root)
        self.root.geometry("900x700+120+100")
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.path = Path(self.temporary.name) / "config.json"
        manager = ConfigSaveManager(self.config, self.path, needs_write=True)
        self.assertTrue(manager.save().persisted)
        self.commits = ConfigCommitController(self.root, manager)
        self.root.update()

    def cleanup_root(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def make_session(self, page):
        owner = tk.Toplevel(self.root)
        owner.geometry("800x620+150+120")
        take_modal_control(owner)
        if page == "newline":
            widget = create_managed_text_box(
                owner, "名单", self.config["settings"]["crlf_file_list"],
                commits=self.commits, enable_favorites=False,
                wrap_config_key="settings.crlf_file_list",
            )
            session = NewlineDraftSession(
                owner, self.commits, widget, tk.StringVar(master=owner),
            )
            widgets = {"crlf_file_list": widget}
        else:
            widgets = {
                field: create_managed_text_box(
                    owner, field, self.config[page][field],
                    commits=self.commits, enable_favorites=False,
                    wrap_config_key=f"{page}.{field}",
                )
                for field in ("exclude_folders", "exclude_files")
            }
            session = ExclusionDraftSession(
                owner, self.commits, self.config[page], widgets,
                tk.StringVar(master=owner),
            )
        register_popup(
            owner, session.close, prepare_close=session.prepare_close,
        )
        self.root.update()
        return session, widgets

    def test_five_syntax_fields_save_has_no_repeated_read_or_analysis(self):
        for page in ("scan", "merge", "newline"):
            with self.subTest(page=page):
                session, widgets = self.make_session(page)
                for widget in widgets.values():
                    widget.delete("1.0", "end")
                    widget.insert("1.0", '\n"scripts/*.CMD/"')
                # 首次原文事件收口属于编辑阶段，不混入保存阶段计数。
                self.root.update()
                results = {
                    field: session.list_editor.views[field].result
                    for field in widgets
                }
                before = self.commits.metrics["text_reads"]
                with patch(
                    "ui.config_editing.analyze_rule_list",
                    wraps=analyze_rule_list,
                ) as analyze:
                    self.assertTrue(session.accept(session.values()))
                    self.root.update()
                    self.assertEqual(
                        self.commits.metrics["text_reads"], before,
                    )
                    analyze.assert_not_called()
                    self.assertTrue(session.accept(session.values()))
                    self.root.update()
                    self.assertEqual(
                        self.commits.metrics["text_reads"], before,
                    )
                    analyze.assert_not_called()
                for field, widget in widgets.items():
                    self.assertEqual(
                        widget.get("1.0", "end-1c"), "scripts\\*.CMD",
                    )
                    self.assertIs(
                        session.list_editor.views[field].result,
                        results[field],
                    )
                    self.assertFalse(
                        session.list_editor.views[field].input_lines,
                    )
                session.destroy()

    def test_unprocessed_input_is_read_once_at_first_completion(self):
        session, widgets = self.make_session("newline")
        widget = widgets["crlf_file_list"]
        before = self.commits.metrics["text_reads"]
        widget.insert("end-1c", "\n*.txt")
        with patch(
            "ui.config_editing.analyze_rule_list", wraps=analyze_rule_list,
        ) as analyze:
            values = session.values()
            self.assertEqual(self.commits.metrics["text_reads"] - before, 1)
            analyze.assert_called_once()
            self.assertEqual(values["crlf_file_list"], "*.bat\n*.CMD\n*.txt")
            self.assertTrue(session.accept(values))
            self.root.update()
            self.assertEqual(self.commits.metrics["text_reads"] - before, 1)
            analyze.assert_called_once()
        session.destroy()

    def test_tagged_insert_and_replace_clean_only_text_arguments(self):
        session, widgets = self.make_session("newline")
        widget = widgets["crlf_file_list"]
        replace_text_keep_undo(widget, "")
        widget.insert("1.0", '"A"', ("first",), '"B"', ("second",))
        widget._content_operation_completed()
        self.assertEqual(widget.get("1.0", "end-1c"), "AB")
        self.assertEqual(
            tuple(map(str, widget.tag_ranges("first"))), ("1.0", "1.1"),
        )
        self.assertEqual(
            tuple(map(str, widget.tag_ranges("second"))), ("1.1", "1.2"),
        )
        with widget._list_text_editing.edit_group():
            widget.replace(
                "1.0", "end-1c", '"C"', ("third",), '"D"', ("fourth",),
            )
        self.assertEqual(widget.get("1.0", "end-1c"), "CD")
        self.assertEqual(
            session.list_editor.views["crlf_file_list"].text, "CD",
        )
        widget._text_editor_controller.undo()
        self.assertEqual(widget.get("1.0", "end-1c"), "AB")
        widget._text_editor_controller.redo()
        self.assertEqual(widget.get("1.0", "end-1c"), "CD")
        session.destroy()

    def test_empty_history_does_not_raise_through_command_proxy(self):
        session, widgets = self.make_session("newline")
        widget = widgets["crlf_file_list"]
        editing = widget._list_text_editing
        original = editing.text
        revision = editing.revision
        before = self.commits.metrics["text_reads"]
        for _ in range(100):
            widget.edit_undo()
            widget.edit_redo()
            widget._text_editor_controller.undo()
            widget._text_editor_controller.redo()
        self.root.update()
        self.assertEqual(editing.revision, revision)
        self.assertEqual(self.commits.metrics["text_reads"], before)
        self.assertEqual(widget.get("1.0", "end-1c"), original)
        self.assertEqual(self.errors, [])
        session.destroy()

    def test_repeated_toggle_undo_then_escape_keeps_application(self):
        for page in ("scan", "merge", "newline"):
            with self.subTest(page=page):
                session, widgets = self.make_session(page)
                for widget in widgets.values():
                    widget.focus_force()
                    self.root.update()
                    original = widget.get("1.0", "end-1c")
                    widget.mark_set("insert", "1.0")
                    for _ in range(50):
                        widget.event_generate("<Control-grave>")
                        widget.event_generate("<Control-z>")
                    self.root.update()
                    self.assertEqual(widget.get("1.0", "end-1c"), original)
                widget.focus_force()
                self.root.update()
                with patch("ui.dialogs.messagebox.askyesnocancel") as ask:
                    widget.event_generate("<Escape>")
                    self.root.update()
                ask.assert_not_called()
                self.assertFalse(session.dialog.winfo_exists())
                self.assertTrue(self.root.winfo_exists())
                self.assertEqual(self.errors, [])

    def test_exit_preparation_returns_grab_without_forcing_editing(self):
        owner = tk.Toplevel(self.root)
        take_modal_control(owner)
        register_popup(owner)
        widget = tk.Text(owner)
        widget.pack()
        widget.focus_force()
        self.root.update()
        child = tk.Toplevel(owner)
        take_modal_control(child)
        self.root._zipatch_closing = True
        try:
            child.destroy()
            self.assertIs(self.root.grab_current(), owner)
            self.assertTrue(owner.winfo_exists())
        finally:
            self.root._zipatch_closing = False
        owner.destroy()

    def test_popup_is_visible_at_button_and_survives_opening_focus(self):
        owner = tk.Toplevel(self.root)
        owner.geometry("800x600+160+140")
        take_modal_control(owner)
        register_popup(owner)
        widgets = [
            create_managed_text_box(
                owner, f"正文{number}", "before", height=5,
                commits=self.commits, enable_favorites=False,
            )
            for number in range(2)
        ]
        row = tk.Frame(owner)
        row.pack(fill="x")
        favorite = FavoriteTextBoxController(
            owner, row, widgets[0], self.commits,
            "merge_regular_preamble", widgets[0]._content_operation_completed,
        )
        self.root.update()
        widgets[1].focus_force()
        self.root.update()
        disk = self.path.read_bytes()

        for _ in range(3):
            favorite.show_popup()
            self.root.update()
            popup = favorite.popup
            self.assertIsNotNone(popup)
            self.assertTrue(popup.winfo_viewable())
            self.assertIs(popup.master, owner)
            button_y = favorite.button.winfo_rooty()
            expected_y = button_y + favorite.button.winfo_height() + 2
            # 下方空间不足时，正式交互允许紧邻按钮向上显示。
            # 不把合法向上定位误判为浮窗位置错误。
            if expected_y + popup.winfo_height() > popup.winfo_screenheight() - 40:
                expected_y = max(0, button_y - popup.winfo_height() - 2)
            self.assertEqual(popup.winfo_rooty(), expected_y)
            self.assertGreaterEqual(popup.winfo_rootx(), 0)
            self.assertLessEqual(
                popup.winfo_rootx() + popup.winfo_width(),
                popup.winfo_screenwidth(),
            )
            validate_temporary_popups(self.root)
            self.assertIs(favorite.popup, popup)
            self.assertIs(self.root.grab_current(), owner)
            self.assertIs(self.root.focus_get(), widgets[1])
            favorite.toggle_popup()
            self.root.update()
            self.assertIsNone(favorite.popup)

        favorite.show_popup()
        self.root.update()
        self.assertTrue(favorite.load_favorite(
            {"name": "sample", "content": '"ordinary text"'},
        ))
        self.root.update()
        self.assertIsNone(favorite.popup)
        self.assertIs(self.root.focus_get(), widgets[0])
        self.assertEqual(widgets[0].get("1.0", "end-1c"), '"ordinary text"')
        self.assertEqual(self.path.read_bytes(), disk)

        other = tk.Toplevel(self.root)
        other_entry = tk.Entry(other)
        other_entry.pack()
        other_entry.focus_force()
        self.root.update()
        # 外部窗口接管焦点后，临时浮窗仍按既有规则自动关闭。
        with patch(
            "ui.window_manager.get_focus_owner", return_value=other_entry,
        ):
            favorite.show_popup()
            validate_temporary_popups(self.root)
        self.assertIsNone(favorite.popup)
        other.destroy()
        owner.destroy()

    def test_popup_below_and_above_positioning_preserves_interaction(self):
        owner = tk.Toplevel(self.root)
        owner.geometry("600x400+100+80")
        take_modal_control(owner)
        register_popup(owner)
        row = tk.Frame(owner)
        row.pack(fill="x")
        widget = create_managed_text_box(
            owner, "普通正文", "before", height=3,
            commits=self.commits, enable_favorites=False,
        )
        favorite = FavoriteTextBoxController(
            owner, row, widget, self.commits,
            "merge_regular_preamble", widget._content_operation_completed,
        )
        self.root.update()
        widget.focus_force()
        self.root.update()
        screen_height = owner.winfo_screenheight()
        disk = self.path.read_bytes()

        # 只控制按钮的屏幕纵坐标，浮窗仍由真实正式入口创建、布局和显示。
        # 不替换定位算法，也不依赖桌面缩放把按钮偶然放在某个位置。
        for placement, button_y in (
            ("below", 0),
            ("above", screen_height - 20),
        ):
            with self.subTest(placement=placement):
                with patch.object(
                    favorite.button, "winfo_rooty", return_value=button_y,
                ):
                    favorite.show_popup()
                    self.root.update()
                    popup = favorite.popup
                    self.assertIsNotNone(popup)
                    self.assertTrue(popup.winfo_viewable())
                    height = popup.winfo_height()
                    below_y = button_y + favorite.button.winfo_height() + 2
                    if placement == "below":
                        self.assertLessEqual(
                            below_y + height, screen_height - 40,
                            "测试下方场景必须有足够空间",
                        )
                        expected_y = below_y
                    else:
                        self.assertGreater(
                            below_y + height, screen_height - 40,
                            "测试上方场景必须触发下方空间不足",
                        )
                        expected_y = max(0, button_y - height - 2)
                    self.assertEqual(popup.winfo_rooty(), expected_y)
                    self.assertGreaterEqual(popup.winfo_rooty(), 0)
                    self.assertLessEqual(
                        popup.winfo_rooty() + height, screen_height,
                    )
                    self.assertIs(self.root.grab_current(), owner)
                    self.assertIs(self.root.focus_get(), widget)
                    validate_temporary_popups(self.root)
                    self.assertIs(favorite.popup, popup)
                    favorite.toggle_popup()
                    self.root.update()
                    self.assertIsNone(favorite.popup)

        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(self.errors, [])
        owner.destroy()

    def test_plain_text_settings_notice_excludes_list_shortcut(self):
        from ui.dialogs import create_text_settings_dialog

        observed = []

        def inspect_and_close():
            dialog = next(
                child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel)
                and child.title() == "普通附加文本说明测试"
            )
            labels = [
                child.cget("text") for child in dialog.winfo_children()
                if isinstance(child, tk.Label)
            ]
            notice = next(
                value for value in labels if value.startswith("快捷键：")
            )
            self.assertNotIn("Ctrl+`", notice)
            for shortcut in ("Ctrl+F", "Ctrl+H", "Ctrl+Z", "Ctrl+Y"):
                self.assertIn(shortcut, notice)
            observed.append(notice)
            from ui.window_manager import close_registered_popup
            self.assertTrue(close_registered_popup(dialog))

        def timeout():
            self.errors.append(AssertionError("普通附加文本说明测试未正常关闭"))
            for child in self.root.winfo_children():
                if isinstance(child, tk.Toplevel) and child.title() == "普通附加文本说明测试":
                    child.destroy()

        callback = self.root.after(80, inspect_and_close)
        deadline = self.root.after(3000, timeout)
        try:
            saved = create_text_settings_dialog(
                self.root, "普通附加文本说明测试",
                self.config["scan"], self.commits,
                [
                    ("preamble_text", "前言", "scan_preamble", "scan.preamble", 3),
                    ("ending_text", "后语", "scan_ending", "scan.ending", 3),
                ],
                700, 500,
            )
        finally:
            self.root.after_cancel(callback)
            self.root.after_cancel(deadline)
        self.assertFalse(saved)
        self.assertEqual(len(observed), 1)
        self.assertTrue(self.root.winfo_exists())
        self.assertEqual(self.errors, [])

    def test_five_syntax_fields_replace_all_preserves_physical_lines(self):
        disk = self.path.read_bytes()
        for page in ("scan", "merge", "newline"):
            with self.subTest(page=page):
                session, widgets = self.make_session(page)
                for field, widget in widgets.items():
                    with self.subTest(field=field):
                        original = "alpha\n\n"
                        replace_text_keep_undo(widget, original)
                        widget.edit_reset()
                        tool = widget._text_editor_controller
                        tool.find_var.set("alpha")
                        tool.replace_var.set("beta")
                        before = self.commits.metrics["text_reads"]
                        with patch(
                            "ui.config_editing.analyze_rule_list",
                            wraps=analyze_rule_list,
                        ) as analyze:
                            tool.replace_all()
                            self.assertEqual(
                                widget.get("1.0", "end-1c"), "beta\n\n",
                            )
                            self.assertEqual(
                                session.list_editor.views[field].text, "beta\n\n",
                            )
                            self.assertEqual(
                                self.commits.metrics["text_reads"], before,
                            )
                            analyze.assert_called_once()
                            self.root.update()
                            self.assertEqual(
                                self.commits.metrics["text_reads"], before,
                            )
                            analyze.assert_called_once()
                        tool.undo()
                        self.assertEqual(
                            widget.get("1.0", "end-1c"), original,
                        )
                        tool.redo()
                        self.assertEqual(
                            widget.get("1.0", "end-1c"), "beta\n\n",
                        )
                session.destroy()
        self.assertEqual(self.path.read_bytes(), disk)

    def test_successful_proxy_commands_do_not_report_outer_exception(self):
        session, widgets = self.make_session("newline")
        widget = widgets["crlf_file_list"]
        editing = widget._list_text_editing
        original = editing.text
        automatic = widget.cget("autoseparators")
        disk = self.path.read_bytes()
        outer_error = tk.TclError("outer operation failed")

        def fail_with_cleanup():
            try:
                raise outer_error
            finally:
                # 外层异常正在传播，以下代理命令自身均成功。
                self.assertEqual(widget.get("1.0", "end-1c"), original)
                widget.edit_separator()
                widget.configure(autoseparators=False)
                widget.configure(autoseparators=automatic)
                widget.insert("end-1c", '"')
                widget.replace("1.0", "end-1c", original)
                widget.edit_reset()
                widget.edit_undo()
                widget.edit_redo()
                widget.configure(state="disabled")
                try:
                    widget.insert("end-1c", "BLOCKED")
                finally:
                    widget.configure(state="normal")

        with self.assertRaises(tk.TclError) as caught:
            fail_with_cleanup()
        self.assertIs(caught.exception, outer_error)
        self.assertEqual(self.errors, [])
        self.assertEqual(widget.get("1.0", "end-1c"), original)
        self.assertEqual(editing.revision, editing.completed_revision)
        self.assertEqual(
            widget.tk.getboolean(widget.cget("autoseparators")),
            widget.tk.getboolean(automatic),
        )
        self.assertEqual(self.path.read_bytes(), disk)
        self.root.update()
        self.assertEqual(self.errors, [])
        session.destroy()

    def test_failed_standardization_does_not_confirm_unwritten_view(self):
        session, widgets = self.make_session("newline")
        widget = widgets["crlf_file_list"]
        replace_text_keep_undo(widget, "\nscripts/*.CMD/\n")
        editor = session.list_editor
        editing = widget._list_text_editing
        previous = editor.views["crlf_file_list"]
        baseline = dict(editor.baseline_comparison)
        accepted = self.config["settings"]["crlf_file_list"]
        disk = self.path.read_bytes()
        original_replace = widget.replace
        original_error = tk.TclError("standardization partially failed")

        def partial_replace(start, end, content):
            original_replace(start, end, "partial.txt\n\n")
            raise original_error

        with patch.object(widget, "replace", side_effect=partial_replace):
            with self.assertRaises(tk.TclError) as caught:
                session.accept(session.values())
        self.assertIs(caught.exception, original_error)
        self.assertIs(editor.views["crlf_file_list"], previous)
        self.assertEqual(editor.baseline_comparison, baseline)
        self.assertEqual(editing.text, previous.text)
        self.assertNotEqual(editing.revision, editing.completed_revision)
        self.assertEqual(
            self.config["settings"]["crlf_file_list"], accepted,
        )
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertFalse(session.saved)
        self.assertEqual(self.commits.operation_depth, 0)

        before = self.commits.metrics["text_reads"]
        with patch(
            "ui.config_editing.analyze_rule_list",
            wraps=analyze_rule_list,
        ) as analyze:
            self.assertEqual(editing.complete(), "partial.txt\n\n")
            self.assertEqual(
                self.commits.metrics["text_reads"] - before, 1,
            )
            analyze.assert_called_once()
            current = editor.views["crlf_file_list"]
            self.assertEqual(current.text, "partial.txt\n\n")
            self.assertEqual(current.result.source_text, "partial.txt\n\n")
            self.root.update()
            self.assertEqual(
                self.commits.metrics["text_reads"] - before, 1,
            )
            analyze.assert_called_once()
        self.assertEqual(editor.baseline_comparison, baseline)
        self.assertEqual(
            self.config["settings"]["crlf_file_list"], accepted,
        )
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(self.errors, [])
        session.destroy()

    def test_partial_tcl_mutation_failure_requires_actual_text_recheck(self):
        session, widgets = self.make_session("newline")
        widget = widgets["crlf_file_list"]
        editing = widget._list_text_editing
        previous = editing.text
        accepted = self.config["settings"]["crlf_file_list"]
        original = editing.original_command
        backing = original + "_failure_test"
        widget.tk.call("rename", original, backing)
        widget.tk.eval(
            f"proc {original} {{args}} {{"
            f"set result [uplevel #0 [linsert $args 0 {backing}]];"
            'if {[lindex $args 0] eq "insert"} {'
            'error "partial mutation failure"};'
            "return $result}"
        )
        try:
            with self.assertRaises(tk.TclError):
                widget.insert("end-1c", "\n*.txt")
        finally:
            widget.tk.call("rename", original, "")
            widget.tk.call("rename", backing, original)
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], tk.TclError)
        self.assertIn("partial mutation failure", str(self.errors[0]))
        self.errors.clear()
        self.assertEqual(editing.text, previous)
        self.assertNotEqual(editing.revision, editing.completed_revision)
        self.assertEqual(self.config["settings"]["crlf_file_list"], accepted)
        final = editing.complete()
        self.assertEqual(final, previous + "\n*.txt")
        self.assertEqual(
            session.list_editor.views["crlf_file_list"].text, final,
        )
        self.assertEqual(self.config["settings"]["crlf_file_list"], accepted)
        self.root.update()
        session.destroy()


if __name__ == "__main__":
    unittest.main()