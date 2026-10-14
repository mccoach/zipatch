# -*- coding: utf-8 -*-

"""真实 Tk、正式入口和临时配置；不访问用户配置。"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG

from services.patch_service import apply_patch
from ui.dialogs import create_managed_text_box
from ui.favorites import FavoriteTextBoxController
from ui.newline_settings import NewlineDraftSession, show_newline_help
from ui.window_manager import prepare_all_for_exit, register_popup


BOUNDARY = "ZIPATCH_BOUNDARY_GUI_FIXED_20261012183400_91C8D274A6B3"


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class NewlineGuiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.errors = []
        self.root.report_callback_exception = (
            lambda kind, error, traceback: self.errors.append(error)
        )
        self.addCleanup(self.cleanup)
        self.path = Path(self.temporary.name) / "config.json"
        manager = ConfigSaveManager(
            deep_merge_config(DEFAULT_CONFIG, {}), self.path, needs_write=True,
        )
        self.assertTrue(manager.save().persisted)
        with patch("app.raise_and_focus"):
            self.app = ZipatchApp(self.root, self.path)

    def cleanup(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def session(self):
        dialog = tk.Toplevel(self.root)
        widget = create_managed_text_box(
            dialog, "CRLF 文件名单",
            self.app.config_data["settings"]["crlf_file_list"],
            commits=self.app.commits, favorite_key="crlf_file_list",
            wrap_config_key="settings.crlf_file_list",
        )
        status = tk.StringVar(master=dialog)
        session = NewlineDraftSession(dialog, self.app.commits, widget, status)
        register_popup(dialog, session.close, prepare_close=session.prepare_close)
        self.root.update()
        return session, widget

    def replace(self, widget, content):
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        self.root.update()

    def favorite(self, session, widget):
        return FavoriteTextBoxController(
            session.dialog, tk.Frame(session.dialog), widget, self.app.commits,
            "crlf_file_list", widget._content_operation_completed,
        )

    def test_layout_and_restore_mode_visibility(self):
        self.app.show_panel("patch")
        panel = self.app.current_panel
        button = panel.newline_settings_button
        self.assertEqual(button.cget("text"), "换行设置")
        self.assertEqual(button.winfo_manager(), "pack")
        siblings = button.master.pack_slaves()
        previous = siblings[siblings.index(button) - 1]
        self.assertEqual(previous.cget("text"), "打开备份目录")
        panel.patch_mode.set("restore")
        panel.refresh_mode_ui()
        self.assertEqual(button.winfo_manager(), "")
        panel.patch_mode.set("apply")
        panel.refresh_mode_ui()
        self.assertEqual(button.winfo_manager(), "pack")
        self.app.show_panel("restore")
        buttons = [
            widget for widget in descendants(self.app.current_panel)
            if isinstance(widget, tk.Button)
        ]
        setting = next(button for button in buttons if button.cget("text") == "换行设置")
        execute = next(button for button in buttons if button.cget("text") == "开始还原")
        self.assertIs(setting.master, execute.master)
        self.assertEqual(setting.pack_info()["side"], "left")

    def test_both_formal_buttons_open_shared_window_and_wrap_preference(self):
        for mode in ("patch", "restore"):
            self.app.show_panel(mode)
            buttons = [
                widget for widget in descendants(self.app.current_panel)
                if isinstance(widget, tk.Button) and widget.cget("text") == "换行设置"
            ]
            self.assertEqual(len(buttons), 1)
            observations = []

            def inspect_close():
                dialog = next(
                    child for child in self.root.winfo_children()
                    if isinstance(child, tk.Toplevel) and child.title() == "换行设置"
                )
                widgets = list(descendants(dialog))
                text = next(widget for widget in widgets if isinstance(widget, tk.Text))
                self.assertEqual(text.get("1.0", "end-1c"), "*.bat\n*.CMD")
                self.assertEqual(str(text.cget("wrap")), "word" if mode == "patch" else "none")
                self.replace(text, "UNSAVED")
                if mode == "patch":
                    toggle = next(
                        widget for widget in widgets if isinstance(widget, tk.Checkbutton)
                        and widget.cget("text") == "自动换行"
                    )
                    toggle.invoke()
                    self.assertEqual(
                        self.app.config_data["settings"]["crlf_file_list"], "*.bat\n*.CMD",
                    )
                observations.append(dialog.title())
                cancel = next(
                    widget for widget in widgets
                    if isinstance(widget, tk.Button) and widget.cget("text") == "取消"
                )
                with patch("ui.dialogs.messagebox.askyesnocancel", return_value=False):
                    cancel.invoke()

            def timeout():
                self.errors.append(AssertionError("正式换行窗口未关闭"))
                for child in self.root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        child.destroy()

            callback = self.root.after(80, inspect_close)
            deadline = self.root.after(4000, timeout)
            try:
                buttons[0].invoke()
            finally:
                self.root.after_cancel(callback)
                self.root.after_cancel(deadline)
            self.assertEqual(observations, ["换行设置"])
        loaded, changed = load_user_config(self.path)
        self.assertFalse(changed)
        self.assertFalse(loaded["text_wrap"]["settings.crlf_file_list"])
        self.assertEqual(loaded["settings"]["crlf_file_list"], "*.bat\n*.CMD")

    def test_saved_setting_reopens_from_same_authority(self):
        first, widget = self.session()
        self.replace(widget, "*.txt")
        self.assertTrue(first.save_and_close())
        second, widget = self.session()
        self.assertEqual(widget.get("1.0", "end-1c"), "*.txt")
        second.destroy()

    def test_edit_highlight_focus_and_red_do_not_submit(self):
        session, widget = self.session()
        original = self.path.read_bytes()
        self.replace(widget, '<bad>\n README \n|<bad>')
        widget.event_generate("<FocusOut>")
        self.root.update()
        for tag in ("exclusion_error", "exclusion_warning", "exclusion_disabled"):
            self.assertTrue(widget.tag_ranges(tag))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.app.config_data["settings"]["crlf_file_list"], "*.bat\n*.CMD")
        with patch("ui.rule_list_editor.confirm_rule_diagnostics", return_value=True):
            self.assertFalse(session.save_and_close())
        self.assertTrue(session.dialog.winfo_exists())
        session.destroy()

    def test_save_reads_once_without_reanalysis_after_writeback(self):
        session, widget = self.session()
        self.replace(widget, '\n"scripts/*.CMD/"')
        import ui.config_editing as module
        before = self.app.commits.metrics["text_reads"]
        with patch.object(module, "analyze_rule_list", wraps=module.analyze_rule_list) as analyze:
            self.assertTrue(session.accept(session.values()))
            self.root.update()
        self.assertEqual(self.app.commits.metrics["text_reads"] - before, 0)
        self.assertEqual(analyze.call_count, 0)
        self.assertEqual(
            self.app.config_data["settings"]["crlf_file_list"], "scripts\\*.CMD",
        )
        session.destroy()

    def test_yellow_current_confirmation_and_internal_space(self):
        session, widget = self.session()
        self.replace(widget, "my notes.cmd")
        self.assertFalse(widget.tag_ranges("exclusion_warning"))
        self.replace(widget, " README ")
        with patch("ui.rule_list_editor.confirm_rule_diagnostics", return_value=False):
            self.assertFalse(session.save_and_close())
        with patch("ui.rule_list_editor.confirm_rule_diagnostics", return_value=True):
            self.assertTrue(session.save_and_close())
        self.assertEqual(self.app.config_data["settings"]["crlf_file_list"], " README ")

    def test_failed_save_restores_config_keeps_draft_and_retries(self):
        session, widget = self.session()
        self.replace(widget, "*.txt")
        original = self.path.read_bytes()
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.assertFalse(session.save_and_close())
        self.assertTrue(session.dialog.winfo_exists())
        self.assertEqual(widget.get("1.0", "end-1c"), "*.txt")
        self.assertEqual(self.app.config_data["settings"]["crlf_file_list"], "*.bat\n*.CMD")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(session.save_and_close())

    def test_favorite_add_overwrite_delete_and_no_business_acceptance(self):
        session, widget = self.session()
        favorite = self.favorite(session, widget)
        for raw, expected in (('"scripts/*.cmd/"', "scripts\\*.cmd"), ("*.txt", "*.txt")):
            self.replace(widget, raw)
            with (
                patch("ui.favorites.ask_favorite_name", return_value="rules"),
                patch("ui.favorites.safe_ask_yes_no", return_value=True),
            ):
                favorite.save_current_text_as_favorite()
            self.assertEqual(
                self.app.config_data["favorites"]["crlf_file_list"],
                [{"name": "rules", "content": expected}],
            )
            self.assertEqual(
                self.app.config_data["settings"]["crlf_file_list"], "*.bat\n*.CMD",
            )
        value = self.app.config_data["favorites"]["crlf_file_list"][0]
        with patch("ui.favorites.safe_ask_yes_no", return_value=True):
            favorite.delete_favorite(value)
        self.assertEqual(self.app.config_data["favorites"]["crlf_file_list"], [])
        session.destroy()

    def test_favorite_rename_and_raw_backfill(self):
        session, widget = self.session()
        self.replace(widget, "UNSAVED")
        value = {"name": "old", "content": '"scripts/*.cmd/"'}
        self.app.commits.submit_values(
            self.app.config_data["favorites"], {"crlf_file_list": [value]},
        )
        favorite = self.favorite(session, widget)
        with patch("ui.favorites.ask_favorite_name", return_value="new"):
            favorite.rename_favorite(value)
        self.assertEqual(widget.get("1.0", "end-1c"), "UNSAVED")
        self.assertEqual(
            self.app.config_data["favorites"]["crlf_file_list"],
            [{"name": "new", "content": "scripts\\*.cmd"}],
        )
        favorite.load_favorite({"name": "raw", "content": '"other/*.cmd/"'})
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), 'other/*.cmd/')
        self.assertEqual(self.app.config_data["settings"]["crlf_file_list"], "*.bat\n*.CMD")
        session.destroy()

    def test_invalid_and_empty_favorite_rejected(self):
        session, widget = self.session()
        favorite = self.favorite(session, widget)
        self.replace(widget, "<bad>")
        with (
            patch("ui.rule_list_editor.confirm_rule_diagnostics", return_value=True),
            patch("ui.favorites.ask_favorite_name") as ask,
        ):
            favorite.save_current_text_as_favorite()
        ask.assert_not_called()
        self.replace(widget, "")
        with (
            patch("ui.favorites.safe_show_error"),
            patch("ui.favorites.ask_favorite_name") as ask,
        ):
            favorite.save_current_text_as_favorite()
        ask.assert_not_called()
        self.assertEqual(self.app.config_data["favorites"]["crlf_file_list"], [])
        self.assertTrue(session.save_and_close())
        self.assertEqual(self.app.config_data["settings"]["crlf_file_list"], "")

    def test_fixed_preview_uses_original_setting_after_real_config_update(self):
        project = Path(self.temporary.name) / "project"
        project.mkdir()
        self.app.show_panel("patch")
        panel = self.app.current_panel
        panel.project_root.set(str(project))
        self.app.commits.submit_values(panel.cfg, {"backup_enabled": False})
        package = "\n".join((
            f'<<ZIPATCH_V3 boundary="{BOUNDARY}">>',
            '---OP write_file id="fixed"',
            "---PATH", "a.cmd", BOUNDARY,
            "---CONTENT", "A\nB", BOUNDARY,
            "---END_OP", "<</ZIPATCH_V3>>",
        ))
        panel.patch_text.delete("1.0", "end")
        panel.patch_text.insert("1.0", package)
        cfg = panel.prepare_parameters()
        self.assertEqual(cfg["output_rules"].normalized_text, "*.bat\n*.CMD")
        preview = panel.run_preview(cfg)
        self.assertEqual(preview.failed_count, 0, preview.preview_text)
        self.app.commits.submit_values(
            self.app.config_data["settings"], {"crlf_file_list": ""},
        )
        apply_patch(preview)
        self.assertEqual((project / "a.cmd").read_bytes(), b"A\r\nB")

    def test_exit_cancel_preserves_window_and_undo_redo(self):
        session, widget = self.session()
        widget.edit_reset()
        tool = widget._text_editor_controller
        tool.find_var.set("*.CMD")
        tool.replace_var.set("*.txt")
        tool.replace_all()
        self.root.update()
        changed = widget.get("1.0", "end-1c")
        tool.undo()
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), "*.bat\n*.CMD")
        tool.redo()
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), changed)
        with patch("ui.dialogs.messagebox.askyesnocancel", return_value=None):
            self.assertFalse(prepare_all_for_exit(self.root))
        self.assertTrue(session.dialog.winfo_exists())
        self.assertEqual(self.app.config_data["settings"]["crlf_file_list"], "*.bat\n*.CMD")
        session.destroy()

    def test_help_three_close_routes_restore_grab(self):
        for action in ("button", "escape", "title"):
            owner = tk.Toplevel(self.root)
            owner.grab_set()
            original = self.path.read_bytes()
            timed_out = []

            def close():
                dialog = next(
                    child for child in owner.winfo_children() if isinstance(child, tk.Toplevel)
                )
                if action == "title":
                    dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
                elif action == "escape":
                    dialog.focus_force()
                    dialog.event_generate("<Escape>")
                else:
                    button = next(
                        widget for widget in descendants(dialog)
                        if isinstance(widget, tk.Button) and widget.cget("text") == "关闭"
                    )
                    button.invoke()

            def timeout():
                timed_out.append(True)
                for child in owner.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        child.destroy()

            callback = owner.after(80, close)
            deadline = owner.after(3000, timeout)
            try:
                show_newline_help(owner)
            finally:
                owner.after_cancel(callback)
                owner.after_cancel(deadline)
            self.assertEqual(timed_out, [])
            self.assertIs(owner.grab_current(), owner)
            self.assertEqual(self.path.read_bytes(), original)
            owner.destroy()


if __name__ == "__main__":
    unittest.main()