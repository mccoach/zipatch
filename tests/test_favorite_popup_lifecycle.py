# -*- coding: utf-8 -*-

"""收藏管理留窗、固定几何、真实事件及窗口引用失效回归。"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.favorites import FAVORITE_ROW_HEIGHT, FavoriteTextBoxController
from ui.window_manager import (
    close_registered_popup, register_popup, take_modal_control,
    validate_temporary_popups,
)


class FavoritePopupLifecycleTests(unittest.TestCase):
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
        self.root.geometry("800x650+80+60")
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.key = "merge_regular_preamble"
        self.config["favorites"][self.key] = [
            {"name": "first", "content": "FIRST"},
            {"name": "second", "content": "SECOND"},
            {"name": "third", "content": "THIRD"},
        ]
        self.path = Path(self.temporary.name) / "config.json"
        self.manager = ConfigSaveManager(
            self.config, self.path, needs_write=True,
        )
        self.assertTrue(self.manager.save().persisted)
        self.commits = ConfigCommitController(self.root, self.manager)
        self.owner = tk.Toplevel(self.root)
        self.owner.geometry("650x500+100+80")
        take_modal_control(self.owner)
        register_popup(self.owner)
        self.text = create_managed_text_box(
            self.owner, "正文", "DRAFT", height=4,
            commits=self.commits, enable_favorites=False,
        )
        toolbar = tk.Frame(self.owner)
        toolbar.pack(fill="x")
        self.favorite = FavoriteTextBoxController(
            self.owner, toolbar, self.text, self.commits,
            self.key, self.text._content_operation_completed,
        )
        self.text.focus_force()
        self.root.update()

    def cleanup_root(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def open_popup(self):
        self.favorite.button.invoke()
        self.root.update()
        popup = self.favorite.popup
        self.assertIsNotNone(popup)
        self.assertTrue(popup.winfo_viewable())
        return popup

    def rows(self):
        body = self.favorite.popup.winfo_children()[0]
        return [
            child for child in body.winfo_children()
            if isinstance(child, tk.Frame)
        ]

    def name_button(self, row):
        return next(
            child for child in row.winfo_children()
            if isinstance(child, tk.Button)
            and not hasattr(child, "_favorite_action")
        )

    def action_button(self, name, action):
        row = next(
            row for row in self.rows()
            if self.name_button(row).cget("text") == name
        )
        return next(
            child for child in row.winfo_children()
            if getattr(child, "_favorite_action", None) == action
        )

    def assert_open(self, popup):
        self.root.update()
        self.assertIs(self.favorite.popup, popup)
        self.assertTrue(popup.winfo_exists())
        self.assertTrue(popup.winfo_viewable())
        validate_temporary_popups(self.root)
        self.assertIs(self.favorite.popup, popup)
        self.assertEqual(self.commits.operation_depth, 0)
        self.assertIs(self.root.grab_current(), self.owner)
        self.assertEqual(self.errors, [])

    def test_fixed_height_and_horizontal_seams(self):
        self.open_popup()
        self.assertEqual(FAVORITE_ROW_HEIGHT, 30)
        for row in self.rows():
            buttons = sorted(
                row.winfo_children(), key=lambda button: button.winfo_rootx(),
            )
            self.assertEqual(row.winfo_height(), FAVORITE_ROW_HEIGHT)
            self.assertEqual(len(buttons), 4)
            for button in buttons:
                self.assertEqual(button.winfo_height(), FAVORITE_ROW_HEIGHT)
                self.assertEqual(button.winfo_rooty(), row.winfo_rooty())
            for left, right in zip(buttons, buttons[1:]):
                self.assertEqual(
                    left.winfo_rootx() + left.winfo_width(),
                    right.winfo_rootx(),
                )
            self.assertEqual(
                [getattr(button, "_favorite_action", "name") for button in buttons],
                ["name", "rename", "delete", "pin"],
            )

    def test_repeated_pin_mouse_events_keep_same_window(self):
        popup = self.open_popup()
        destroyed = []
        popup.bind(
            "<Destroy>",
            lambda event: destroyed.append(True) if event.widget is popup else None,
            add="+",
        )
        before = self.manager.metrics["successful_replaces"]
        for name in ("third", "second", "first"):
            button = self.action_button(name, "pin")
            button.focus_force()
            self.root.update()
            button.event_generate("<Enter>")
            button.event_generate("<ButtonPress-1>", x=5, y=5)
            button.event_generate("<ButtonRelease-1>", x=5, y=5)
            self.assert_open(popup)
        self.assertEqual(destroyed, [])
        self.assertEqual(
            [entry["name"] for entry in self.favorite.favorites()],
            ["first", "second", "third"],
        )
        self.assertTrue(all(
            entry.get("pinned") for entry in self.favorite.favorites()
        ))
        self.assertEqual(
            self.manager.metrics["successful_replaces"] - before, 3,
        )
        self.action_button("second", "pin").invoke()
        self.assert_open(popup)
        self.assertNotIn("pinned", self.favorite.favorites()[-1])
        loaded, changed = load_user_config(self.path)
        self.assertFalse(changed)
        self.assertEqual(loaded["favorites"][self.key], self.favorite.favorites())

    def test_management_does_not_force_editor_window_activation(self):
        popup = self.open_popup()
        with patch("ui.favorites.restore_editor_interaction") as restore:
            self.action_button("third", "pin").invoke()
            self.assert_open(popup)
        restore.assert_not_called()
        self.assertEqual(self.text.get("1.0", "end-1c"), "DRAFT")

    def test_sibling_name_window_cannot_dismiss_active_management(self):
        popup = self.open_popup()
        original = self.path.read_bytes()

        def ask(parent, initial_name="", **kwargs):
            child = tk.Toplevel(self.owner)
            entry = tk.Entry(child)
            entry.pack()
            take_modal_control(child)
            register_popup(child)
            entry.focus_force()
            self.root.update()
            self.assertGreater(self.commits.operation_depth, 0)
            with patch(
                "ui.window_manager.get_focus_owner", return_value=entry,
            ):
                validate_temporary_popups(self.root)
            self.assertIs(self.favorite.popup, popup)
            self.assertTrue(popup.winfo_viewable())
            child.destroy()
            return None

        with patch("ui.favorites.ask_favorite_name", side_effect=ask):
            self.action_button("first", "rename").invoke()
            self.assert_open(popup)
            popup.winfo_children()[0].winfo_children()[0].invoke()
            self.assert_open(popup)
        self.assertEqual(self.path.read_bytes(), original)

    def test_rename_delete_and_cancellation_keep_popup(self):
        popup = self.open_popup()
        with patch("ui.favorites.ask_favorite_name", return_value="renamed"):
            self.action_button("first", "rename").invoke()
        self.assert_open(popup)
        self.assertEqual(self.favorite.favorites()[0]["name"], "renamed")
        original = self.path.read_bytes()
        with patch("ui.favorites.safe_ask_yes_no", return_value=False):
            self.action_button("second", "delete").invoke()
        self.assert_open(popup)
        self.assertEqual(self.path.read_bytes(), original)
        with patch("ui.favorites.safe_ask_yes_no", return_value=True):
            self.action_button("second", "delete").invoke()
        self.assert_open(popup)
        self.assertEqual(
            [entry["name"] for entry in self.favorite.favorites()],
            ["renamed", "third"],
        )

    def test_add_keeps_popup_and_original_add_button(self):
        popup = self.open_popup()
        add = popup.winfo_children()[0].winfo_children()[0]
        with patch("ui.favorites.ask_favorite_name", return_value="new"):
            add.invoke()
        self.assert_open(popup)
        self.assertIs(popup.winfo_children()[0].winfo_children()[0], add)
        self.assertEqual(
            self.favorite.favorites()[-1], {"name": "new", "content": "DRAFT"},
        )

    def test_save_failure_shows_accepted_memory_and_retry_does_not_toggle(self):
        popup = self.open_popup()
        original = self.path.read_bytes()
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error") as report,
        ):
            self.action_button("third", "pin").invoke()
        report.assert_called_once()
        self.assert_open(popup)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(self.manager.needs_save)
        self.assertTrue(self.action_button("third", "pin")._favorite_pinned)
        self.assertTrue(self.commits.finish(self.owner).persisted)
        self.assert_open(popup)
        self.assertTrue(self.favorite.favorites()[0]["pinned"])

    def test_backfill_closes_and_next_click_immediately_opens(self):
        popup = self.open_popup()
        self.name_button(self.rows()[1]).invoke()
        self.root.update()
        self.assertIsNone(self.favorite.popup)
        self.assertFalse(popup.winfo_exists())
        self.assertEqual(self.text.get("1.0", "end-1c"), "SECOND")
        self.assertIs(self.root.focus_get(), self.text)
        reopened = self.open_popup()
        self.assertIsNot(reopened, popup)

    def test_direct_destroy_and_registered_close_clear_reference(self):
        for route in ("destroy", "registered"):
            with self.subTest(route=route):
                popup = self.open_popup()
                if route == "destroy":
                    popup.destroy()
                else:
                    self.assertTrue(close_registered_popup(
                        popup, restore_focus=False,
                    ))
                self.root.update()
                self.assertIsNone(self.favorite.popup)
                reopened = self.open_popup()
                self.assertIsNot(reopened, popup)
                self.favorite.button.invoke()
                self.root.update()
                self.assertIsNone(self.favorite.popup)

    def test_external_focus_dismisses_without_stealing_new_focus(self):
        popup = self.open_popup()
        other = tk.Toplevel(self.root)
        entry = tk.Entry(other)
        entry.pack()

        # 原编辑窗口持有 grab；先让新窗口实际接管模态控制。
        # 转移期间暂缓自动关闭，确保测试观察到真实的焦点交接。
        with self.commits.operation():
            take_modal_control(other)
            entry.focus_force()
            self.root.update()
            self.assertIs(self.root.grab_current(), other)
            self.assertIs(self.root.focus_get(), entry)
            self.assertIs(self.favorite.popup, popup)

        validate_temporary_popups(self.root)
        self.assertIsNone(self.favorite.popup)
        self.assertFalse(popup.winfo_exists())
        self.assertIs(self.root.focus_get(), entry)
        other.destroy()

    def test_disabled_and_active_business_preserve_lock(self):
        popup = self.open_popup()
        original = self.path.read_bytes()
        with self.commits.operation():
            self.favorite.toggle_favorite_pin({"name": "third"})
            self.favorite.toggle_popup()
        self.text.configure(state="disabled")
        self.favorite.toggle_favorite_pin({"name": "third"})
        self.assertEqual(str(self.text.cget("state")), "disabled")
        self.assertEqual(self.path.read_bytes(), original)
        self.text.configure(state="normal")
        self.assert_open(popup)


if __name__ == "__main__":
    unittest.main()