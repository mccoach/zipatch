# -*- coding: utf-8 -*-

"""共享收藏名称选择与正式覆盖链；只使用临时配置和真实 Tk。"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.favorites import FavoriteTextBoxController, ask_favorite_name
from ui.window_manager import register_popup, take_modal_control


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class FavoriteNameSelectionTests(unittest.TestCase):
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
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.config["favorites"]["merge_regular_preamble"] = [
            {"name": "first", "content": "FIRST OLD"},
            {"name": "second", "content": "SECOND OLD"},
        ]
        self.config["favorites"]["scan_preamble"] = [
            {"name": "other category", "content": "OTHER"},
        ]
        self.path = Path(self.temporary.name) / "config.json"
        self.manager = ConfigSaveManager(
            self.config, self.path, needs_write=True,
        )
        self.assertTrue(self.manager.save().persisted)
        self.commits = ConfigCommitController(self.root, self.manager)
        self.owner = tk.Toplevel(self.root)
        take_modal_control(self.owner)
        register_popup(self.owner)
        self.widget = create_managed_text_box(
            self.owner, "正文", "CURRENT", commits=self.commits,
            enable_favorites=False,
        )
        self.favorite = FavoriteTextBoxController(
            self.owner, tk.Frame(self.owner), self.widget, self.commits,
            "merge_regular_preamble",
            self.widget._content_operation_completed,
        )
        self.widget.focus_force()
        self.root.update()

    def cleanup_root(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def interact(self, action, call):
        observed = []

        def inspect():
            candidates = [
                child for child in self.owner.winfo_children()
                if isinstance(child, tk.Toplevel)
                and child.title() == "收藏名称"
            ]
            if not candidates:
                callback_ids.append(self.owner.after(10, inspect))
                return
            dialog = candidates[0]
            lists = [
                widget for widget in descendants(dialog)
                if isinstance(widget, tk.Listbox)
            ]
            if not dialog.winfo_viewable() or any(
                not widget.winfo_viewable() for widget in lists
            ):
                callback_ids.append(self.owner.after(10, inspect))
                return
            observed.append(True)
            action(dialog)

        def timeout():
            self.errors.append(AssertionError("收藏名称窗口未按预期结束"))
            for child in self.owner.winfo_children():
                if isinstance(child, tk.Toplevel) and child.title() == "收藏名称":
                    child.destroy()

        callback_ids = [self.owner.after(80, inspect)]
        deadline = self.owner.after(3000, timeout)
        try:
            result = call()
        finally:
            for callback in callback_ids:
                self.owner.after_cancel(callback)
            self.owner.after_cancel(deadline)
        self.assertEqual(observed, [True])
        self.assertIs(self.root.grab_current(), self.owner)
        self.assertEqual(self.commits.operation_depth, 0)
        self.assertEqual(self.errors, [])
        return result

    def button(self, dialog, text):
        return next(
            widget for widget in descendants(dialog)
            if isinstance(widget, tk.Button) and widget.cget("text") == text
        )

    def select(self, dialog, index):
        names = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, tk.Listbox)
        )
        self.assertEqual(names.get(0, "end"), ("first", "second"))
        names.selection_set(index)
        names.event_generate("<<ListboxSelect>>")
        entry = next(
            widget for widget in descendants(dialog)
            if isinstance(widget, tk.Entry)
        )
        self.assertEqual(entry.get(), names.get(index))
        return entry

    def test_select_existing_reuses_confirmation_and_single_submit(self):
        before = self.manager.metrics["successful_replaces"]

        def choose(dialog):
            self.select(dialog, 1)
            self.button(dialog, "保存").invoke()

        with patch("ui.favorites.safe_ask_yes_no", return_value=True) as confirm:
            self.interact(choose, self.favorite.save_current_text_as_favorite)
        confirm.assert_called_once()
        self.assertIn("second", confirm.call_args.args[1])
        expected = [
            {"name": "first", "content": "FIRST OLD"},
            {"name": "second", "content": "CURRENT"},
        ]
        self.assertEqual(
            self.config["favorites"]["merge_regular_preamble"], expected,
        )
        self.assertEqual(
            self.config["favorites"]["scan_preamble"],
            [{"name": "other category", "content": "OTHER"}],
        )
        self.assertEqual(
            self.manager.metrics["successful_replaces"] - before, 1,
        )
        loaded, changed = load_user_config(self.path)
        self.assertFalse(changed)
        self.assertEqual(loaded["favorites"]["merge_regular_preamble"], expected)
        self.assertIs(self.root.focus_get(), self.widget)

    def test_selected_name_can_be_edited_to_add_new(self):
        def choose(dialog):
            entry = self.select(dialog, 0)
            entry.delete(0, "end")
            entry.insert(0, "new name")
            self.button(dialog, "保存").invoke()

        with patch("ui.favorites.safe_ask_yes_no") as confirm:
            self.interact(choose, self.favorite.save_current_text_as_favorite)
        confirm.assert_not_called()
        self.assertEqual(
            self.config["favorites"]["merge_regular_preamble"],
            [
                {"name": "first", "content": "FIRST OLD"},
                {"name": "second", "content": "SECOND OLD"},
                {"name": "new name", "content": "CURRENT"},
            ],
        )

    def test_declined_overwrite_keeps_favorites_and_disk(self):
        disk = self.path.read_bytes()
        before = self.manager.metrics["write_attempts"]

        def choose(dialog):
            self.select(dialog, 0)
            self.button(dialog, "保存").invoke()

        with patch("ui.favorites.safe_ask_yes_no", return_value=False):
            self.interact(choose, self.favorite.save_current_text_as_favorite)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(self.manager.metrics["write_attempts"], before)
        self.assertIs(self.root.focus_get(), self.widget)

    def test_empty_category_still_allows_new_name(self):
        self.favorite.favorite_key = "patch_text"

        def choose(dialog):
            names = next(
                widget for widget in descendants(dialog)
                if isinstance(widget, tk.Listbox)
            )
            self.assertEqual(names.size(), 0)
            entry = next(
                widget for widget in descendants(dialog)
                if isinstance(widget, tk.Entry)
            )
            entry.insert(0, "first entry")
            self.button(dialog, "保存").invoke()

        self.interact(choose, self.favorite.save_current_text_as_favorite)
        self.assertEqual(
            self.config["favorites"]["patch_text"],
            [{"name": "first entry", "content": "CURRENT"}],
        )

    def test_selection_cancel_routes_do_not_submit(self):
        disk = self.path.read_bytes()
        for route in ("取消", "escape", "title", "destroy"):
            with self.subTest(route=route):
                def choose(dialog):
                    self.select(dialog, 0)
                    if route == "escape":
                        dialog.focus_force()
                        dialog.event_generate("<Escape>")
                    elif route == "title":
                        dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
                    elif route == "destroy":
                        dialog.destroy()
                    else:
                        self.button(dialog, route).invoke()

                self.interact(choose, self.favorite.save_current_text_as_favorite)
                self.assertEqual(self.path.read_bytes(), disk)
                self.assertIs(self.root.focus_get(), self.widget)

    def test_rename_dialog_remains_name_only(self):
        def choose(dialog):
            self.assertFalse(any(
                isinstance(widget, tk.Listbox)
                for widget in descendants(dialog)
            ))
            self.button(dialog, "取消").invoke()

        result = self.interact(
            choose, lambda: ask_favorite_name(self.owner, "first"),
        )
        self.assertIsNone(result)

    def test_overwrite_failure_keeps_disk_and_returns_interaction(self):
        disk = self.path.read_bytes()

        def choose(dialog):
            self.select(dialog, 0)
            self.button(dialog, "保存").invoke()

        with (
            patch("ui.favorites.safe_ask_yes_no", return_value=True),
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.interact(choose, self.favorite.save_current_text_as_favorite)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertTrue(self.manager.needs_save)
        self.assertIs(self.root.focus_get(), self.widget)
        self.assertEqual(str(self.widget.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()