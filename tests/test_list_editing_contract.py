# -*- coding: utf-8 -*-

"""名单编辑、固定结果和交互恢复，仅使用临时配置及项目。"""

import tempfile
import time
import tkinter as tk
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG
from core.exclusion_rules import PreparedExclusions
from core.rule_lists import RuleListResult, analyze_rule_list, validate_file_rules
from core.text_io import replace_text_keep_undo
from panels.merge_panel import MergePanel
from panels.scan_panel import ScanPanel
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.exclusion_editor import ExclusionDraftSession
from ui.favorites import FavoriteTextBoxController, ask_favorite_name
from ui.newline_settings import NewlineDraftSession
from ui.window_manager import (
    register_popup, restore_editor_interaction, take_modal_control,
    unregister_popup,
)


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ResultTests(unittest.TestCase):
    def test_public_construction_and_mutation_are_rejected(self):
        result = analyze_rule_list("*.txt", "file")
        with self.assertRaises(TypeError):
            RuleListResult(
                normalized_text="enabled", rules=(), diagnostics=(),
                line_map=(1,), object_kind="file",
            )
        with self.assertRaises(TypeError):
            replace(result, rules=())
        with self.assertRaises(FrozenInstanceError):
            result.has_errors = True
        with self.assertRaises(TypeError):
            PreparedExclusions(result, result)

    def test_fixed_configuration_results_are_retained_without_editor(self):
        config = {"rule": "*.txt"}
        commits = ConfigCommitController(None, ConfigSaveManager({}))
        with patch(
            "ui.config_editing.analyze_rule_list", wraps=analyze_rule_list,
        ) as analyze:
            first = commits.prepare_rule(config, "rule", "file", config["rule"])
            for _ in range(5):
                self.assertIs(
                    first,
                    commits.prepare_rule(config, "rule", "file", config["rule"]),
                )
            analyze.assert_called_once()
        self.assertEqual(len(commits.rule_results), 1)

    def test_candidate_does_not_prepare_another_configuration(self):
        commits = ConfigCommitController(None, ConfigSaveManager({}))
        with patch(
            "ui.config_editing.analyze_rule_list", wraps=analyze_rule_list,
        ) as analyze:
            result = commits.prepare_rule(
                {"rule": "<bad>"}, "rule", "file", "*.txt",
            )
        analyze.assert_called_once_with("*.txt", "file")
        self.assertFalse(result.has_errors)
        self.assertEqual(commits.rule_results, {})

    def test_categories_illegal_results_and_current_cache_are_distinct(self):
        config = {"rule": ":"}
        commits = ConfigCommitController(None, ConfigSaveManager({}))
        file_result = commits.prepare_rule(config, "rule", "file")
        directory = commits.prepare_rule(config, "rule", "directory")
        self.assertFalse(file_result.has_errors)
        self.assertTrue(directory.has_errors)
        self.assertIs(directory, commits.prepare_rule(config, "rule", "directory"))
        config["rule"] = "<bad>"
        invalid = commits.prepare_rule(config, "rule", "file")
        self.assertIs(invalid, commits.prepare_rule(config, "rule", "file"))
        with self.assertRaises(ValueError):
            validate_file_rules(invalid)
        self.assertEqual(len(commits.rule_results), 2)

    def test_scan_and_directory_merge_reuse_fixed_configuration_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_bytes(b"A")
            config = deep_merge_config(DEFAULT_CONFIG, {})
            manager = ConfigSaveManager(config, root / "config.json", needs_write=True)
            manager.save()
            commits = ConfigCommitController(None, manager)
            for page, panel_class, method, module, service in (
                ("scan", ScanPanel, "_execute", "panels.scan_panel", "panoramic_scan"),
                ("merge", MergePanel, "_execute_regular_merge",
                 "panels.merge_panel", "merge_project_files"),
            ):
                cfg = config[page]
                cfg.update(
                    source_folder=str(source), output_folder=str(root),
                    exclude_folders="", exclude_files="",
                    open_after_done=False,
                )
                panel = SimpleNamespace(
                    cfg=cfg, commits=commits, root=None,
                    prepare=Mock(side_effect=lambda fields: dict(cfg)),
                    set_status=Mock(), log=Mock(), handle_error=Mock(),
                    resolve_common_output_and_markers=Mock(
                        return_value=(str(root / "merge.txt"), "HEADER", "FOOTER"),
                    ),
                )
                result = SimpleNamespace(
                    output_file=str(root / "result.txt"),
                    total_files=1, file_count=1, dir_count=0,
                )
                with (
                    patch(module + "." + service, return_value=result),
                    patch(module + ".safe_show_info"),
                    patch(
                        "ui.config_editing.analyze_rule_list",
                        wraps=analyze_rule_list,
                    ) as analyze,
                ):
                    for _ in range(3):
                        getattr(panel_class, method)(panel)
                    self.assertEqual(analyze.call_count, 2)
                panel.handle_error.assert_not_called()


class ListGuiTests(unittest.TestCase):
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
        self.addCleanup(self.cleanup)
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.path = Path(self.temporary.name) / "config.json"
        self.manager = ConfigSaveManager(self.config, self.path, needs_write=True)
        self.assertTrue(self.manager.save().persisted)
        self.commits = ConfigCommitController(self.root, self.manager)

    def cleanup(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def session(self, page="scan", initial="old"):
        dialog = tk.Toplevel(self.root)
        take_modal_control(dialog)
        if page == "newline":
            config = self.config["settings"]
            config["crlf_file_list"] = initial
            widget = create_managed_text_box(
                dialog, "名单", initial, commits=self.commits,
                enable_favorites=False, wrap_config_key="settings.crlf_file_list",
            )
            session = NewlineDraftSession(
                dialog, self.commits, widget, tk.StringVar(master=dialog),
            )
            widgets = {"crlf_file_list": widget}
        else:
            config = self.config[page]
            for field in ("exclude_folders", "exclude_files"):
                config[field] = initial
            widgets = {
                field: create_managed_text_box(
                    dialog, field, initial, commits=self.commits,
                    enable_favorites=False, wrap_config_key=f"{page}.{field}",
                )
                for field in ("exclude_folders", "exclude_files")
            }
            session = ExclusionDraftSession(
                dialog, self.commits, config, widgets,
                tk.StringVar(master=dialog),
            )
        register_popup(dialog, session.close, prepare_close=session.prepare_close)
        self.root.update()
        return session, widgets

    def favorite(self, dialog, widget, key="scan_exclude_files"):
        return FavoriteTextBoxController(
            dialog, tk.Frame(dialog), widget, self.commits, key,
            widget._content_operation_completed,
        )

    def test_initial_cleanup_and_pending_persistence_are_separate(self):
        session, widgets = self.session(initial='\n"src/cache/"\n')
        self.assertEqual(
            widgets["exclude_files"].get("1.0", "end-1c"), "\nsrc/cache/\n",
        )
        self.assertFalse(session.has_changes(session.values()))
        self.manager.accept(self.config["scan"], {"include_date": True})
        with patch("ui.dialogs.messagebox.askyesnocancel") as ask:
            self.assertTrue(session.close())
        ask.assert_not_called()
        self.assertTrue(self.manager.needs_save)

    def test_five_fields_shortcut_selection_cursor_and_undo(self):
        original = self.path.read_bytes()
        for page in ("scan", "merge", "newline"):
            session, widgets = self.session(
                page, "abc\n|abc\n||abc\n abc\n",
            )
            for widget in widgets.values():
                self.assertTrue(widget.bind("<Control-grave>"))
                widget.tag_add("sel", "1.0", "5.0")
                widget.mark_set("insert", "2.2")
                widget._text_editor_controller.toggle_disabled_lines()
                self.assertEqual(
                    widget.get("1.0", "end-1c"),
                    "|abc\nabc\n|abc\n| abc\n",
                )
                self.assertEqual(widget.index("insert"), "2.1")
                self.assertEqual(
                    tuple(map(str, widget.tag_ranges("sel"))), ("1.0", "5.0"),
                )
                widget._text_editor_controller.undo()
                self.assertEqual(
                    widget.get("1.0", "end-1c"),
                    "abc\n|abc\n||abc\n abc\n",
                )
                widget._text_editor_controller.redo()
                self.assertEqual(
                    widget.get("1.0", "end-1c"),
                    "|abc\nabc\n|abc\n| abc\n",
                )
            session.destroy()
        self.assertEqual(self.path.read_bytes(), original)

    def test_shortcut_event_empty_line_wrap_and_locked_state(self):
        session, widgets = self.session(initial="abcdef\n\nend")
        widget = widgets["exclude_files"]
        widget.configure(width=2, wrap="word")
        widget.focus_force()
        self.root.update()
        widget.mark_set("insert", "1.3")
        widget.event_generate("<Control-grave>")
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), "|abcdef\n\nend")
        widget.mark_set("insert", "2.0")
        widget.event_generate("<Control-grave>")
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), "|abcdef\n|\nend")
        widget.configure(state="disabled")
        widget.event_generate("<Control-grave>")
        self.root.update()
        self.assertEqual(widget.get("1.0", "end-1c"), "|abcdef\n|\nend")
        session.destroy()

    def test_no_undo_or_redo_history_is_not_reported_as_failure(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        widget._text_editor_controller.undo()
        widget._text_editor_controller.redo()
        self.assertEqual(self.errors, [])
        self.assertEqual(widget.get("1.0", "end-1c"), "old")
        session.destroy()

    def test_quote_only_input_does_not_create_an_undo_step(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        before = self.commits.metrics["text_reads"]
        widget.insert("end-1c", '"', ("tagged",))
        self.root.update()
        self.assertEqual(self.commits.metrics["text_reads"], before)
        widget._text_editor_controller.undo()
        self.assertEqual(self.errors, [])
        self.assertEqual(widget.get("1.0", "end-1c"), "old")
        session.destroy()

    def test_explicit_replacement_and_cleaning_are_one_undo(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        replace_text_keep_undo(widget, '"a"\'“b”`')
        self.assertEqual(widget.get("1.0", "end-1c"), "a'“b”`")
        widget._text_editor_controller.undo()
        self.assertEqual(widget.get("1.0", "end-1c"), "old")
        widget._text_editor_controller.redo()
        self.assertEqual(widget.get("1.0", "end-1c"), "a'“b”`")
        session.destroy()

    def test_paste_has_synchronous_result_and_one_undo(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        widget.focus_force()
        self.root.update()
        widget.tag_add("sel", "1.0", "end-1c")
        widget.clipboard_clear()
        widget.clipboard_append('"new.txt"')
        with patch(
            "ui.config_editing.analyze_rule_list", wraps=analyze_rule_list,
        ) as analyze:
            widget.event_generate("<<Paste>>")
            self.assertEqual(widget.get("1.0", "end-1c"), "new.txt")
            view = session.list_editor.views["exclude_files"]
            self.assertEqual(view.text, "new.txt")
            self.assertEqual(view.result.source_text, "new.txt")
            self.assertEqual(analyze.call_count, 1)
            self.root.update()
            self.assertEqual(analyze.call_count, 1)
        widget._text_editor_controller.undo()
        self.assertEqual(widget.get("1.0", "end-1c"), "old")
        widget._text_editor_controller.redo()
        self.assertEqual(widget.get("1.0", "end-1c"), "new.txt")
        session.destroy()

    def test_continuous_insert_preserves_group_and_history(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        widget.edit_separator()
        widget.insert("end-1c", "A")
        widget.insert("end-1c", '"')
        widget.insert("end-1c", "B")
        widget._content_operation_completed()
        self.assertEqual(widget.get("1.0", "end-1c"), "oldAB")
        widget._text_editor_controller.undo()
        self.assertEqual(widget.get("1.0", "end-1c"), "old")
        widget._text_editor_controller.redo()
        self.assertEqual(widget.get("1.0", "end-1c"), "oldAB")
        session.destroy()

    def test_standardization_reuses_result_and_changes_mapping_synchronously(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        replace_text_keep_undo(widget, '\n"src/cache/"\n<bad>')
        result = session.list_editor.views["exclude_files"].result
        with patch(
            "ui.rule_list_editor.confirm_rule_diagnostics", return_value=False,
        ):
            self.assertFalse(session.accept(session.values()))
        self.assertIs(session.list_editor.views["exclude_files"].result, result)
        self.assertFalse(session.list_editor.views["exclude_files"].input_lines)
        self.assertEqual(str(widget.tag_ranges("exclusion_error")[0]), "2.0")
        self.root.update()
        self.assertEqual(str(widget.tag_ranges("exclusion_error")[0]), "2.0")
        session.destroy()

    def test_failed_candidate_and_successful_configuration_are_separate(self):
        session, widgets = self.session(initial="*.log")
        widget = widgets["exclude_files"]
        old = self.commits.prepare_rule(
            self.config["scan"], "exclude_files", "file",
        )
        replace_text_keep_undo(widget, "*.txt")
        candidate = session.list_editor.views["exclude_files"].result
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.assertFalse(session.accept(session.values()))
        self.assertIs(session.list_editor.views["exclude_files"].result, candidate)
        self.assertIs(
            self.commits.prepare_rule(
                self.config["scan"], "exclude_files", "file",
            ), old,
        )
        self.assertTrue(session.has_changes(session.values()))
        self.assertTrue(session.accept(session.values()))
        self.assertIs(
            self.commits.prepare_rule(
                self.config["scan"], "exclude_files", "file",
            ), candidate,
        )
        session.destroy()

    def test_reopen_reuses_configuration_result(self):
        session, _ = self.session(initial="*.txt")
        result = session.list_editor.views["exclude_files"].result
        session.destroy()
        with patch(
            "ui.config_editing.analyze_rule_list",
            side_effect=AssertionError("reanalysis"),
        ):
            reopened, _ = self.session(initial="*.txt")
            self.assertIs(reopened.list_editor.views["exclude_files"].result, result)
            reopened.destroy()

    def test_equivalent_return_and_distinct_invalid_bodies(self):
        session, widgets = self.session(initial="src/cache/")
        widget = widgets["exclude_files"]
        replace_text_keep_undo(widget, "|src/cache/")
        self.assertTrue(session.has_changes(session.values()))
        replace_text_keep_undo(widget, "src\\cache")
        self.assertFalse(session.has_changes(session.values()))
        replace_text_keep_undo(widget, "<bad>")
        first = session.list_editor.comparison()
        replace_text_keep_undo(widget, "<different>")
        self.assertNotEqual(first, session.list_editor.comparison())
        session.destroy()

    def test_proxy_filter_exception_reports_original_context(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        error = TypeError("filter defect")
        with patch("ui.list_text_editing.clean_rule_quotes", side_effect=error):
            with self.assertRaises(tk.TclError):
                widget.insert("end-1c", "X")
        self.assertEqual(self.errors, [error])
        self.errors.clear()
        self.assertEqual(widget.get("1.0", "end-1c"), "old")
        widget.insert("end-1c", "Y")
        widget._content_operation_completed()
        self.assertEqual(widget.get("1.0", "end-1c"), "oldY")
        session.destroy()

    def test_proxy_destroy_releases_commands(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        command = widget._w
        original = widget._list_text_editing.original_command
        session.destroy()
        self.assertEqual(self.root.tk.call("info", "commands", command), "")
        self.assertEqual(self.root.tk.call("info", "commands", original), "")

    def test_ordinary_text_is_not_a_list(self):
        widget = create_managed_text_box(
            self.root, "普通正文", '"original"', commits=self.commits,
            enable_favorites=False,
        )
        self.assertFalse(hasattr(widget, "_list_text_editing"))
        self.assertEqual(widget.bind("<Control-grave>"), "")
        self.assertEqual(widget.get("1.0", "end-1c"), '"original"')

    def test_favorite_load_returns_actual_editing_for_all_texts(self):
        dialog = tk.Toplevel(self.root)
        take_modal_control(dialog)
        register_popup(dialog)
        widgets = [
            create_managed_text_box(
                dialog, f"附加文本{number}", "before", commits=self.commits,
                enable_favorites=False,
            )
            for number in range(2)
        ]
        favorite = self.favorite(dialog, widgets[0], "merge_regular_preamble")
        widgets[0].focus_force()
        self.root.update()
        favorite.show_popup()
        self.root.update()
        self.assertIs(self.root.focus_get(), widgets[0])
        self.assertTrue(
            favorite.load_favorite({"name": "sample", "content": '"after"'}),
        )
        self.root.update()
        self.assertIs(self.root.grab_current(), dialog)
        self.assertIs(self.root.focus_get(), widgets[0])
        for widget in widgets:
            self.assertTrue(restore_editor_interaction(widget))
            self.root.update()
            widget.insert("end-1c", " EDIT")
            widget.delete("1.0", "1.1")
            self.assertTrue(widget.get("1.0", "end-1c").endswith(" EDIT"))
            widget._text_editor_controller.open_find()
            self.assertTrue(widget._text_editor_controller.search_bar.winfo_manager())
            widget._text_editor_controller.hide_search_bar()
        dialog.destroy()

    def test_all_favorite_operation_endings_restore_parent_interaction(self):
        session, widgets = self.session(initial="*.txt")
        widget = widgets["exclude_files"]
        favorite = self.favorite(session.dialog, widget)
        cases = (
            ("add", "new", True),
            ("cancel_add", None, True),
            ("overwrite", "new", True),
            ("cancel_overwrite", "new", False),
            ("rename", "renamed", True),
            ("cancel_rename", None, True),
            ("delete", None, True),
            ("cancel_delete", None, False),
        )
        for action, name, confirmed in cases:
            with self.subTest(action=action):
                widget.focus_force()
                self.root.update()
                with (
                    patch("ui.favorites.ask_favorite_name", return_value=name),
                    patch("ui.favorites.safe_ask_yes_no", return_value=confirmed),
                ):
                    if "rename" in action:
                        favorite.rename_favorite({"name": "new", "content": "*.txt"})
                    elif "delete" in action:
                        favorite.delete_favorite({"name": "renamed", "content": "*.txt"})
                    else:
                        favorite.save_current_text_as_favorite()
                self.root.update()
                self.assertIs(self.root.grab_current(), session.dialog)
                self.assertIs(self.root.focus_get(), widget)
                self.assertEqual(self.commits.operation_depth, 0)
                widget.insert("end-1c", "X")
                widget.delete("end-2c", "end-1c")
                widget._content_operation_completed()
        session.destroy()

    def test_favorite_save_failure_returns_to_editor(self):
        session, widgets = self.session(initial="*.txt")
        widget = widgets["exclude_files"]
        favorite = self.favorite(session.dialog, widget)
        with (
            patch("ui.favorites.ask_favorite_name", return_value="new"),
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            favorite.save_current_text_as_favorite()
        self.root.update()
        self.assertIs(self.root.grab_current(), session.dialog)
        self.assertIs(self.root.focus_get(), widget)
        self.assertTrue(self.manager.needs_save)
        session.destroy()

    def test_name_close_routes_and_direct_destroy_return_control(self):
        owner = tk.Toplevel(self.root)
        take_modal_control(owner)
        register_popup(owner)
        widget = tk.Text(owner)
        widget.pack()
        for action in ("保存", "取消", "escape", "title", "destroy"):
            widget.focus_force()
            self.root.update()
            timed_out = []

            def close():
                child = next(
                    child for child in owner.winfo_children()
                    if isinstance(child, tk.Toplevel)
                )
                if action == "destroy":
                    child.destroy()
                elif action == "escape":
                    child.focus_force()
                    child.event_generate("<Escape>")
                elif action == "title":
                    child.tk.call(child.protocol("WM_DELETE_WINDOW"))
                else:
                    button = next(
                        current for current in descendants(child)
                        if isinstance(current, tk.Button)
                        and current.cget("text") == action
                    )
                    button.invoke()

            def timeout():
                timed_out.append(True)
                for child in owner.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        child.destroy()

            callback = owner.after(60, close)
            deadline = owner.after(3000, timeout)
            try:
                result = ask_favorite_name(owner, "sample")
            finally:
                owner.after_cancel(callback)
                owner.after_cancel(deadline)
            self.assertEqual(timed_out, [])
            self.assertIs(self.root.grab_current(), owner)
            self.assertEqual(result, "sample" if action == "保存" else None)
            widget.insert("end-1c", "EDIT")
        owner.destroy()

    def test_new_owner_or_explicit_release_is_not_overwritten(self):
        owner = tk.Toplevel(self.root)
        take_modal_control(owner)
        first = tk.Toplevel(owner)
        take_modal_control(first)
        second = tk.Toplevel(owner)
        take_modal_control(second)
        first.destroy()
        self.assertIs(self.root.grab_current(), second)
        second.grab_release()
        second.destroy()
        self.assertIsNone(self.root.grab_current())
        owner.destroy()

    def test_import_success_cancel_and_failure_finish_interaction(self):
        owner = tk.Toplevel(self.root)
        take_modal_control(owner)
        register_popup(owner)
        widget = create_managed_text_box(
            owner, "正文", "old", commits=self.commits,
            enable_favorites=False, enable_import_text=True,
        )
        button = next(
            current for current in descendants(owner)
            if isinstance(current, tk.Button) and current.cget("text") == "导入"
        )
        for path, error in (("", None), ("source.txt", None), ("source.txt", OSError("denied"))):
            with self.subTest(path=path, error=error):
                observed = []

                def read(selected):
                    observed.append(self.commits.operation_depth)
                    if error is not None:
                        raise error
                    return '"new"', "utf-8"

                with (
                    patch("ui.dialogs.filedialog.askopenfilename", return_value=path),
                    patch("ui.dialogs.read_text_with_fallback_encodings", side_effect=read),
                    patch("ui.dialogs.safe_show_info"),
                    patch("ui.dialogs.safe_show_error"),
                ):
                    button.invoke()
                self.root.update()
                self.assertEqual(observed, [] if not path else [1])
                self.assertEqual(self.commits.operation_depth, 0)
                self.assertIs(self.root.grab_current(), owner)
                self.assertIs(self.root.focus_get(), widget)
                widget.insert("end-1c", "EDIT")
        owner.destroy()

    def test_legal_lock_prevents_favorite_and_focus_recovery(self):
        session, widgets = self.session()
        widget = widgets["exclude_files"]
        favorite = self.favorite(session.dialog, widget)
        with self.commits.operation():
            self.assertFalse(favorite.load_favorite({"name": "x", "content": "changed"}))
        widget.configure(state="disabled")
        self.assertFalse(favorite.load_favorite({"name": "x", "content": "changed"}))
        self.assertFalse(restore_editor_interaction(widget))
        self.assertEqual(widget.get("1.0", "end-1c"), "old")
        session.destroy()


class ApplicationListTests(unittest.TestCase):
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
        self.addCleanup(self.cleanup)
        folder = Path(self.temporary.name)
        (folder / "a.txt").write_bytes(b"A")
        config = deep_merge_config(DEFAULT_CONFIG, {})
        config["merge"]["source_folder"] = str(folder)
        config["merge"]["demand_file_list_text"] = '"a.txt"\n\n'
        self.path = folder / "config.json"
        manager = ConfigSaveManager(config, self.path, needs_write=True)
        manager.save()
        self.original = self.path.read_bytes()
        with patch("app.raise_and_focus"):
            self.app = ZipatchApp(self.root, self.path)

    def cleanup(self):
        if hasattr(self, "session"):
            self.session.destroy()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "后台检查超时")
        self.root.update()

    def test_sixth_field_quotes_versions_undo_and_final_lock(self):
        self.session = self.app.current_panel.open_demand_merge_dialog()
        session = self.session
        widget = session.rule_text
        self.assertEqual(widget.get("1.0", "end-1c"), "a.txt\n\n")
        self.assertTrue(session.prepare_close())
        self.wait_until(lambda: all(rule.match is not None for rule in session.rules))
        version = session.version
        widget.mark_set("insert", "1.0")
        widget._text_editor_controller.toggle_disabled_lines()
        self.assertGreater(session.version, version)
        self.assertTrue(session.rules[0].match.disabled)
        widget._text_editor_controller.undo()
        self.assertEqual(widget.get("1.0", "end-1c"), "a.txt\n\n")
        widget._text_editor_controller.redo()
        self.assertEqual(widget.get("1.0", "end-1c"), "|a.txt\n\n")
        replace_text_keep_undo(widget, '"a.txt"\n\n')
        self.assertEqual([rule.raw for rule in session.rules], ["a.txt", "", ""])
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertTrue(session.start_final("save"))
        before = widget.get("1.0", "end-1c")
        widget._text_editor_controller.toggle_disabled_lines()
        self.assertEqual(widget.get("1.0", "end-1c"), before)
        self.assertTrue(session.cancel_final())
        self.wait_until(lambda: not session.busy)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_both_formal_newline_entries_have_the_shortcut(self):
        for mode in ("patch", "restore"):
            self.app.show_panel(mode)
            button = next(
                current for current in descendants(self.app.current_panel)
                if isinstance(current, tk.Button) and current.cget("text") == "换行设置"
            )
            timed_out = []
            observed = []

            def inspect():
                dialog = next(
                    child for child in self.root.winfo_children()
                    if isinstance(child, tk.Toplevel) and child.title() == "换行设置"
                )
                widget = next(
                    current for current in descendants(dialog)
                    if isinstance(current, tk.Text)
                )
                self.assertTrue(widget.bind("<Control-grave>"))
                widget.mark_set("insert", "1.0")
                widget._text_editor_controller.toggle_disabled_lines()
                self.assertTrue(widget.get("1.0", "end-1c").startswith("|*.bat"))
                observed.append(True)
                with patch("ui.dialogs.messagebox.askyesnocancel", return_value=False):
                    next(
                        current for current in descendants(dialog)
                        if isinstance(current, tk.Button) and current.cget("text") == "取消"
                    ).invoke()

            def timeout():
                timed_out.append(True)
                for child in self.root.winfo_children():
                    if isinstance(child, tk.Toplevel):
                        child.destroy()

            callback = self.root.after(80, inspect)
            deadline = self.root.after(4000, timeout)
            try:
                button.invoke()
            finally:
                self.root.after_cancel(callback)
                self.root.after_cancel(deadline)
            self.assertEqual(timed_out, [])
            self.assertEqual(observed, [True])


if __name__ == "__main__":
    unittest.main()