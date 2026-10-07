# -*- coding: utf-8 -*-

import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import TclError
from unittest.mock import patch

from core.config import ConfigSaveManager, SaveStatus, deep_merge_config
from core.constants import DEFAULT_CONFIG
from core.text_io import get_text_value
from ui.config_editing import ConfigCommitController
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.widgets import create_entry_row, make_checkbutton, make_radiobutton
from ui.window_manager import (
    close_registered_popup, exit_blocking_popup,
    prepare_all_for_exit, register_popup,
)


class EditorGuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.path = Path(self.directory.name) / "config.json"
        self.manager = ConfigSaveManager(self.config, self.path, needs_write=True)
        self.manager.save()
        self.controller = ConfigCommitController(self.root, self.manager)
        self.callback_errors = []
        self.root.report_callback_exception = self.report_callback_error

    def report_callback_error(self, error_type, error, traceback):
        self.callback_errors.append(error)

    def tearDown(self):
        if hasattr(self, "callback_errors"):
            self.assertEqual(self.callback_errors, [], "Unexpected Tk callback error")

    def destroy_root(self):
        try:
            self.root.destroy()
        except TclError:
            pass

    def make_entry(self):
        variable = tk.StringVar(master=self.root, value="")
        entry = create_entry_row(
            self.root, "路径", variable, commits=self.controller,
            config=self.config["scan"], field="source_folder", page="scan",
            history_key="scan.source_folder",
        )
        return variable, entry, entry._config_binding

    def make_text(self):
        return create_managed_text_box(
            self.root, "正文", "", commits=self.controller,
            config=self.config["patch"], field="patch_text", page="patch",
            enable_favorites=False, wrap_config_key="patch.patch_text",
        )

    def make_draft(self):
        dialog = tk.Toplevel(self.root)
        widget = tk.Text(dialog)
        widget.pack()
        widget.insert("1.0", self.config["merge"]["preamble_text"])
        session = TextDraftSession(
            dialog, self.controller, self.config["merge"],
            {"preamble_text": widget},
        )
        register_popup(
            dialog, session.close, prepare_close=session.prepare_close,
            focus_on_register=False,
        )
        return session, widget

    def test_typing_only_changes_draft(self):
        variable, entry, binding = self.make_entry()
        before = dict(self.manager.metrics)
        for index in range(20):
            variable.set("x" * (index + 1))
        self.assertTrue(binding.dirty)
        self.assertEqual(self.config["scan"]["source_folder"], "")
        for key in ("json_generations", "write_attempts"):
            self.assertEqual(self.manager.metrics[key], before[key])

    def test_accept_then_focus_out_is_idempotent(self):
        variable, entry, binding = self.make_entry()
        variable.set("  final  ")
        before = self.manager.metrics["successful_replaces"]
        self.controller.submit_editors([binding])
        binding.on_focus_out()
        self.assertEqual(self.config["scan"]["source_folder"], "final")
        self.assertEqual(self.config["entry_history"]["scan.source_folder"], ["final"])
        self.assertEqual(self.manager.metrics["successful_replaces"] - before, 1)

    def test_focus_out_then_page_accept_is_idempotent(self):
        variable, entry, binding = self.make_entry()
        variable.set("final")
        before = self.manager.metrics["successful_replaces"]
        binding.on_focus_out()
        self.controller.submit_editors(self.controller.select("scan"))
        self.assertEqual(self.manager.metrics["successful_replaces"] - before, 1)

    def test_unmodified_entry_has_no_serialization(self):
        variable, entry, binding = self.make_entry()
        before = dict(self.manager.metrics)
        binding.on_return()
        binding.on_focus_out()
        self.assertEqual(
            self.manager.metrics["json_generations"], before["json_generations"],
        )

    def test_browse_suppression_preserves_intermediate_draft(self):
        variable, entry, binding = self.make_entry()
        variable.set("intermediate")
        with binding.suspend_focus_commit():
            binding.on_focus_out()
            self.assertEqual(self.config["scan"]["source_folder"], "")
            variable.set("chosen")
        self.controller.submit_editors([binding])
        self.assertEqual(self.config["entry_history"]["scan.source_folder"], ["chosen"])

    def test_browse_exception_cleans_local_state(self):
        variable, entry, binding = self.make_entry()
        variable.set("draft")
        with self.assertRaises(ValueError):
            with self.controller.operation(), binding.suspend_focus_commit():
                raise ValueError("selection failed")
        self.assertEqual(binding._suspend_depth, 0)
        self.assertEqual(self.controller.operation_depth, 0)
        self.assertTrue(binding.dirty)
        self.assertEqual(self.config["scan"]["source_folder"], "")

    def test_option_does_not_accept_unfinished_entry(self):
        variable, entry, binding = self.make_entry()
        variable.set("unfinished")
        option = tk.BooleanVar(master=self.root, value=False)
        checkbox = make_checkbutton(
            self.root, "日期", option, commits=self.controller,
            config=self.config["scan"], field="include_date",
        )
        checkbox.invoke()
        self.assertTrue(self.config["scan"]["include_date"])
        self.assertEqual(self.config["scan"]["source_folder"], "")
        self.assertTrue(binding.dirty)

    def test_program_set_requires_explicit_acceptance(self):
        option = tk.BooleanVar(master=self.root, value=False)
        make_checkbutton(
            self.root, "日期", option, commits=self.controller,
            config=self.config["scan"], field="include_date",
        )
        option.set(True)
        self.assertFalse(self.config["scan"]["include_date"])
        self.controller.submit_values(
            self.config["scan"], {"include_date": option.get()},
        )
        self.assertTrue(self.config["scan"]["include_date"])

    def test_option_display_failure_restores_field(self):
        option = tk.BooleanVar(master=self.root, value=False)
        observed = []

        def fail():
            raise ValueError("display defect")

        def report(error_type, error, traceback):
            observed.append(error)

        checkbox = make_checkbutton(
            self.root, "日期", option, commits=self.controller,
            config=self.config["scan"], field="include_date", command=fail,
        )
        with patch.object(self.root, "report_callback_exception", side_effect=report):
            checkbox.invoke()
        self.assertEqual(len(observed), 1)
        self.assertIsInstance(observed[0], ValueError)
        self.assertFalse(option.get())
        self.assertFalse(self.config["scan"]["include_date"])
        self.controller.submit_values(self.config["scan"], {"include_size": True})
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(disk["scan"]["include_date"])
        self.assertTrue(disk["scan"]["include_size"])

    def test_selected_radio_is_noop(self):
        variable = tk.StringVar(master=self.root, value="overwrite")
        radio = make_radiobutton(
            self.root, "覆盖", variable, "overwrite", commits=self.controller,
            config=self.config["restore"], field="existing_file_policy",
        )
        before = self.manager.metrics["write_attempts"]
        radio.invoke()
        self.assertEqual(self.manager.metrics["write_attempts"], before)

    def test_failed_save_retries_without_new_edit(self):
        variable, entry, binding = self.make_entry()
        variable.set("final")
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            result = self.controller.submit_editors([binding])
        self.assertEqual(result.status, SaveStatus.FAILED)
        self.assertFalse(binding.dirty)
        self.assertTrue(self.controller.submit_editors([binding]).persisted)

    def test_history_delete_is_not_reinserted(self):
        variable, entry, binding = self.make_entry()
        variable.set("final")
        self.controller.submit_editors([binding])
        with patch.object(entry._history_plugin, "show_popup"):
            entry._history_plugin.delete_history("final")
        self.controller.submit_editors([binding])
        self.assertEqual(self.config["entry_history"]["scan.source_folder"], [])

    def test_text_repeat_does_not_read_again(self):
        text = self.make_text()
        text.insert("1.0", "body")
        binding = text._config_binding
        self.controller.submit_editors([binding])
        before = self.controller.metrics["text_reads"]
        self.controller.submit_editors([binding])
        self.assertEqual(self.controller.metrics["text_reads"], before)

    def test_prepared_text_keeps_existing_newline_semantics(self):
        text = self.make_text()
        text.insert("1.0", "body\n\n")
        text._content_operation_completed("body\n\n")
        self.assertEqual(self.config["patch"]["patch_text"], get_text_value(text))
        before = self.controller.metrics["text_reads"]
        text._config_binding.on_focus_out()
        self.assertEqual(self.controller.metrics["text_reads"], before)

    def test_replace_all_reads_once_and_accepts_final_value(self):
        text = self.make_text()
        text.insert("1.0", "first first")
        tool = text._text_editor_controller
        tool.find_var.set("first")
        tool.replace_var.set("second\n")
        before = self.controller.metrics["text_reads"]
        writes = self.manager.metrics["successful_replaces"]
        tool.replace_all()
        self.assertEqual(self.controller.metrics["text_reads"] - before, 1)
        self.assertEqual(self.config["patch"]["patch_text"], get_text_value(text))
        self.assertEqual(self.manager.metrics["successful_replaces"] - writes, 1)

    def test_noop_replace_does_not_write(self):
        text = self.make_text()
        text.insert("1.0", "body")
        self.controller.submit_editors([text._config_binding])
        tool = text._text_editor_controller
        tool.find_var.set("body")
        tool.replace_var.set("body")
        before = self.manager.metrics["write_attempts"]
        tool.replace_all()
        self.assertEqual(self.manager.metrics["write_attempts"], before)

    def test_destroy_removes_registration_and_trace(self):
        variable, entry, binding = self.make_entry()
        entry.destroy()
        self.assertNotIn(binding, self.controller.editors)
        self.assertIsNone(binding._trace_id)
        variable.set("after destroy")
        self.assertEqual(self.config["scan"]["source_folder"], "")

    def test_readonly_text_has_no_undo(self):
        text = create_managed_text_box(
            self.root, "结果", "initial", commits=self.controller,
            readonly=True, enable_favorites=False,
            wrap_config_key="patch.result_text",
        )
        self.assertFalse(self.root.tk.getboolean(text.cget("undo")))
        self.assertEqual(str(text.cget("state")), "disabled")

    def test_exit_cancel_keeps_all_draft_windows(self):
        first, first_widget = self.make_draft()
        second, second_widget = self.make_draft()
        for widget in (first_widget, second_widget):
            widget.delete("1.0", "end")
            widget.insert("1.0", "draft")
        with patch("ui.dialogs.messagebox.askyesnocancel", side_effect=[False, None]):
            self.assertFalse(prepare_all_for_exit(self.root))
        for session in (first, second):
            self.assertTrue(session.dialog.winfo_exists())
        self.assertEqual(get_text_value(first_widget), "draft")
        self.assertEqual(get_text_value(second_widget), "draft")

    def test_draft_failure_discard_then_other_save(self):
        session, widget = self.make_draft()
        original = self.config["merge"]["preamble_text"]
        widget.delete("1.0", "end")
        widget.insert("1.0", "abandoned")
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.assertFalse(session.save_and_close())
        self.assertTrue(session.dialog.winfo_exists())
        self.assertEqual(self.config["merge"]["preamble_text"], original)
        with patch("ui.dialogs.messagebox.askyesnocancel", return_value=False):
            self.assertTrue(session.close())
        self.controller.submit_values(self.config["scan"], {"include_date": True})
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["merge"]["preamble_text"], original)

    def test_draft_close_is_blocked_during_business(self):
        session, widget = self.make_draft()
        with self.controller.operation():
            self.assertFalse(session.close())
            self.assertFalse(session.save_and_close())
        self.assertTrue(session.dialog.winfo_exists())

    def test_close_confirmation_cannot_reenter_close(self):
        session, widget = self.make_draft()
        widget.insert("end", "changed")
        observed = []

        def choose(*args, **kwargs):
            observed.append(session.close())
            return None

        with patch("ui.dialogs.messagebox.askyesnocancel", side_effect=choose):
            self.assertFalse(session.close())
        self.assertEqual(observed, [False])
        self.assertFalse(session._close_in_progress)
        self.assertEqual(self.controller.operation_depth, 0)
        self.assertTrue(session.dialog.winfo_exists())

    def test_draft_destroy_is_idempotent(self):
        session, widget = self.make_draft()
        session.destroy()
        session.destroy()
        self.assertTrue(session._destroyed)

    def test_popup_close_is_blocked_during_exit_preparation(self):
        dialog = tk.Toplevel(self.root)
        register_popup(dialog, focus_on_register=False)
        self.root._zipatch_closing = True
        try:
            self.assertFalse(close_registered_popup(dialog))
            self.assertTrue(dialog.winfo_exists())
        finally:
            self.root._zipatch_closing = False
        self.assertTrue(close_registered_popup(dialog))

    def test_exit_blocker_removed_on_destroy(self):
        dialog = tk.Toplevel(self.root)
        register_popup(dialog, exit_blocker=True, focus_on_register=False)
        self.assertIs(exit_blocking_popup(self.root), dialog)
        dialog.destroy()
        self.assertIsNone(exit_blocking_popup(self.root))


if __name__ == "__main__":
    unittest.main()