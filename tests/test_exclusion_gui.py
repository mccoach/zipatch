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
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.exclusion_editor import ExclusionDraftSession
from ui.favorites import FavoriteTextBoxController
from ui.widgets import create_entry_row
from ui.window_manager import register_popup


class ExclusionGuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.config["scan"]["exclude_folders"] = "cache"
        self.config["scan"]["exclude_files"] = "*.log"
        self.manager = ConfigSaveManager(self.config, self.path, needs_write=True)
        self.assertTrue(self.manager.save().persisted)
        self.commits = ConfigCommitController(self.root, self.manager)
        self.errors = []
        self.root.report_callback_exception = self.report_callback_error
        self.dialog = tk.Toplevel(self.root)
        self.widgets = {}
        for field in ("exclude_folders", "exclude_files"):
            self.widgets[field] = create_managed_text_box(
                self.dialog, field, self.config["scan"][field],
                commits=self.commits,
                favorite_key=f"scan_{field}",
                wrap_config_key=f"scan.{field}",
            )
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

    def report_callback_error(self, error_type, error, traceback):
        self.errors.append(error)

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [])

    def replace(self, field, content):
        widget = self.widgets[field]
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        self.root.update()

    def value(self, field):
        return self.widgets[field].get("1.0", "end-1c")

    def test_editing_paste_undo_and_focus_do_not_commit(self):
        before = dict(self.manager.metrics)
        self.replace("exclude_files", '"src/cache/"\n bad|name')
        widget = self.widgets["exclude_files"]
        widget.event_generate("<FocusOut>")
        self.root.update()
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        self.assertEqual(self.value("exclude_files"), '"src/cache/"\n bad|name')
        self.assertEqual(self.status.get(), "已修改，尚未校验")
        for tag in ("exclusion_error", "exclusion_warning", "exclusion_disabled"):
            self.assertEqual(widget.tag_ranges(tag), ())
        tool = widget._text_editor_controller
        tool.undo()
        self.root.update()
        tool.redo()
        self.root.update()
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.manager.metrics[key], before[key])

    def test_save_normalizes_both_lists_and_persists_once(self):
        self.replace("exclude_folders", '\n"src/cache/"\n|bad//<name>')
        self.replace("exclude_files", '"*.LOG"\n:""\n')
        before = dict(self.manager.metrics)
        with patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=True):
            self.assertTrue(self.session.save_and_close())
        self.assertFalse(self.dialog.winfo_exists())
        self.assertEqual(
            self.config["scan"]["exclude_folders"],
            "src\\cache\n|bad//<name>",
        )
        self.assertEqual(self.config["scan"]["exclude_files"], "*.LOG\n:")
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.manager.metrics[key] - before[key], 1)
        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(
            loaded["scan"]["exclude_folders"],
            "src\\cache\n|bad//<name>",
        )

    def test_red_blocks_both_lists_and_maps_current_display_lines(self):
        original = self.path.read_bytes()
        self.replace("exclude_folders", '"src/cache/"')
        self.replace("exclude_files", '\n""\n bad|name\n README*')
        observed = []

        def report(parent, named_results):
            observed.extend(named_results)
            return False

        with patch("ui.exclusion_editor.confirm_exclusion_diagnostics", side_effect=report):
            self.assertFalse(self.session.save_and_close())
        self.assertTrue(self.dialog.winfo_exists())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.config["scan"]["exclude_folders"], "cache")
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        self.assertEqual(self.value("exclude_folders"), "src\\cache")
        self.assertEqual(self.value("exclude_files"), " bad|name\n README*")
        file_result = dict(observed)["文件名单"]
        self.assertEqual(
            [(item.display_line, item.state) for item in file_result.diagnostics],
            [(1, "error"), (2, "warning")],
        )
        widget = self.widgets["exclude_files"]
        self.assertEqual(
            str(widget.tag_ranges("exclusion_error")[0]), "1.0",
        )
        self.assertEqual(
            str(widget.tag_ranges("exclusion_warning")[0]), "2.0",
        )

    def test_yellow_return_then_confirm_is_scoped_to_current_save(self):
        self.replace("exclude_files", " README*")
        with patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=False):
            self.assertFalse(self.session.save_and_close())
        widget = self.widgets["exclude_files"]
        self.assertTrue(widget.tag_ranges("exclusion_warning"))
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        self.replace("exclude_files", " LICENSE*")
        self.assertEqual(widget.tag_ranges("exclusion_warning"), ())
        self.assertEqual(self.status.get(), "已修改，尚未校验")
        with patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=True):
            self.assertTrue(self.session.save_and_close())
        self.assertEqual(self.config["scan"]["exclude_files"], " LICENSE*")

    def test_disk_failure_keeps_window_and_previous_config(self):
        original = self.path.read_bytes()
        self.replace("exclude_folders", "src/cache/")
        self.replace("exclude_files", "test_*.py")
        with (
            patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=True),
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.assertFalse(self.session.save_and_close())
        self.assertTrue(self.dialog.winfo_exists())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.config["scan"]["exclude_folders"], "cache")
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        self.assertEqual(self.value("exclude_folders"), "src\\cache")
        self.assertIn("保存失败", self.status.get())

    def test_favorite_preparation_validates_without_accepting_config(self):
        with patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=True):
            prepared = self.session.prepare_favorite(
                "exclude_files", '"src/cache/*.log/"',
            )
        self.assertEqual(prepared, "src\\cache\\*.log")
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")
        with patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=False):
            self.assertIsNone(
                self.session.prepare_favorite("exclude_files", "<none>"),
            )
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")

    def test_actual_favorite_add_uses_validation_hook(self):
        self.replace("exclude_files", '"src/cache/*.log/"')
        favorite = FavoriteTextBoxController(
            self.dialog, tk.Frame(self.dialog),
            self.widgets["exclude_files"], self.commits,
            "scan_exclude_files",
            self.widgets["exclude_files"]._content_operation_completed,
        )
        with (
            patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=True),
            patch("ui.favorites.ask_favorite_name", return_value="new rules"),
        ):
            favorite.save_current_text_as_favorite()
        self.assertEqual(
            self.config["favorites"]["scan_exclude_files"],
            [{"name": "new rules", "content": "src\\cache\\*.log"}],
        )
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")

    def test_favorite_red_does_not_open_name_input_or_save(self):
        self.replace("exclude_files", "<none>")
        favorite = FavoriteTextBoxController(
            self.dialog, tk.Frame(self.dialog),
            self.widgets["exclude_files"], self.commits,
            "scan_exclude_files",
            self.widgets["exclude_files"]._content_operation_completed,
        )
        before = self.path.read_bytes()
        with (
            patch("ui.exclusion_editor.confirm_exclusion_diagnostics", return_value=False),
            patch("ui.favorites.ask_favorite_name") as ask_name,
        ):
            favorite.save_current_text_as_favorite()
        ask_name.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.config["favorites"]["scan_exclude_files"], [])

    def test_favorite_backfill_preserves_raw_text_until_save(self):
        favorite = FavoriteTextBoxController(
            self.dialog, tk.Frame(self.dialog),
            self.widgets["exclude_files"], self.commits,
            "scan_exclude_files",
            self.widgets["exclude_files"]._content_operation_completed,
        )
        favorite.load_favorite(
            {"name": "raw", "content": ' "src/cache/"\n|bad/path'},
        )
        self.root.update()
        self.assertEqual(
            self.value("exclude_files"), ' "src/cache/"\n|bad/path',
        )
        self.assertEqual(self.config["scan"]["exclude_files"], "*.log")

    def test_unrelated_entry_normalization_is_unchanged(self):
        variable = tk.StringVar(master=self.root, value="")
        entry = create_entry_row(
            self.root, "普通输入", variable, commits=self.commits,
            config=self.config["merge"], field="output_filename", page="merge",
        )
        variable.set("  result.txt  ")
        self.commits.submit_editors([entry._config_binding])
        self.assertEqual(self.config["merge"]["output_filename"], "result.txt")
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["merge"]["output_filename"], "result.txt")


if __name__ == "__main__":
    unittest.main()