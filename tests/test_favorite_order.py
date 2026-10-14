# -*- coding: utf-8 -*-

"""持续置顶、共享图标、稳定顺序及正式持久化回归。"""

import json
import tempfile
import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG
from core.favorite_order import order_favorites
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.favorites import (
    FavoriteTextBoxController, favorite_action_button, title_bar_button,
)
from ui.window_manager import register_popup, take_modal_control


class FavoriteOrderPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "config.json"

    def save_source(self, entries, key="patch_text"):
        config = deep_merge_config(DEFAULT_CONFIG, {})
        config["favorites"][key] = entries
        self.path.write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8",
        )
        return self.path.read_bytes()

    def test_stable_partition_has_no_second_order_source(self):
        entries = [
            {"name": "ordinary-a", "content": "A"},
            {"name": "pin-a", "content": "B", "pinned": True},
            {"name": "ordinary-b", "content": "C"},
            {"name": "pin-b", "content": "D", "pinned": True},
        ]
        original = deepcopy(entries)
        ordered = order_favorites(entries)
        self.assertEqual(
            [entry["name"] for entry in ordered],
            ["pin-a", "pin-b", "ordinary-a", "ordinary-b"],
        )
        self.assertEqual(entries, original)
        self.assertEqual(order_favorites(ordered), ordered)

    def test_load_preserves_pin_and_repairs_partition_once(self):
        entries = [
            {"name": "ordinary", "content": "O"},
            {"name": "pinned", "content": "P", "pinned": True},
        ]
        self.save_source(entries)
        config, needs_write = load_user_config(self.path)
        self.assertTrue(needs_write)
        expected = [entries[1], entries[0]]
        self.assertEqual(config["favorites"]["patch_text"], expected)
        manager = ConfigSaveManager(config, self.path, needs_write=needs_write)
        self.assertTrue(manager.save().persisted)
        disk = self.path.read_bytes()
        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(loaded["favorites"]["patch_text"], expected)
        self.assertEqual(self.path.read_bytes(), disk)

    def test_invalid_pin_type_is_rejected_without_modifying_file(self):
        for key in DEFAULT_CONFIG["favorites"]:
            for invalid in (None, 0, 1, "true", [], {}):
                with self.subTest(category=key, pinned=invalid):
                    disk = self.save_source([
                        {"name": "sample", "content": "BODY", "pinned": invalid},
                    ], key)
                    with self.assertRaisesRegex(ValueError, "pinned 必须是布尔值"):
                        load_user_config(self.path)
                    self.assertEqual(self.path.read_bytes(), disk)

    def test_optional_false_pin_is_normalized_without_losing_content(self):
        self.save_source([
            {"name": "sample", "content": "RAW\n\n", "pinned": False},
        ])
        loaded, needs_write = load_user_config(self.path)
        self.assertTrue(needs_write)
        self.assertEqual(
            loaded["favorites"]["patch_text"],
            [{"name": "sample", "content": "RAW\n\n"}],
        )


class FavoriteOrderTests(unittest.TestCase):
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
            self.owner, "正文", "UNCHANGED", commits=self.commits,
            enable_favorites=False,
        )
        row = tk.Frame(self.owner)
        row.pack(fill="x")
        self.favorite = FavoriteTextBoxController(
            self.owner, row, self.widget, self.commits,
            "merge_regular_preamble",
            self.widget._content_operation_completed,
        )
        self.widget.focus_force()
        self.root.update()

    def cleanup_root(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def entries(self):
        return [
            {"name": "first", "content": "FIRST"},
            {"name": "second", "content": "SECOND"},
            {"name": "third", "content": "THIRD"},
        ]

    def set_category(self, key):
        self.favorite.close_popup()
        self.favorite.favorite_key = key
        self.favorite.submit(self.entries())

    def popup_rows(self):
        return [
            child for child in self.favorite.popup.winfo_children()[0].winfo_children()
            if isinstance(child, tk.Frame)
        ]

    def displayed_names(self):
        return [
            next(
                child.cget("text") for child in row.winfo_children()
                if isinstance(child, tk.Button)
                and child.pack_info()["side"] == "left"
            )
            for row in self.popup_rows()
        ]

    def assert_interaction(self):
        self.assertEqual(self.commits.operation_depth, 0)
        self.assertIs(self.root.grab_current(), self.owner)
        self.assertIs(self.root.focus_get(), self.widget)
        self.assertEqual(self.widget.get("1.0", "end-1c"), "UNCHANGED")
        self.assertEqual(str(self.widget.cget("state")), "normal")
        self.assertEqual(self.errors, [])

    def names(self):
        return [entry["name"] for entry in self.favorite.favorites()]

    def pin(self, name):
        self.favorite.toggle_favorite_pin({"name": name, "content": "STALE"})

    def test_real_icons_position_size_refresh_and_persistence(self):
        self.set_category("merge_regular_preamble")
        self.favorite.show_popup()
        self.root.update()
        third_row = self.popup_rows()[2]
        buttons = {
            child._favorite_action: child
            for child in third_row.winfo_children()
            if hasattr(child, "_favorite_action")
        }
        self.assertGreater(
            buttons["pin"].winfo_rootx(), buttons["delete"].winfo_rootx(),
        )
        self.assertGreater(
            buttons["delete"].winfo_rootx(), buttons["rename"].winfo_rootx(),
        )
        baseline_frame = tk.Frame(self.owner)
        baseline_frame.pack()
        baselines = {
            "rename": title_bar_button(baseline_frame, "改名", lambda: None, width=5),
            "pin": title_bar_button(baseline_frame, "置顶", lambda: None, width=5),
            "delete": title_bar_button(
                baseline_frame, "✖", lambda: None,
                width=3, danger=True, bold=True,
            ),
        }
        self.root.update_idletasks()
        for action, button in buttons.items():
            with self.subTest(action=action):
                self.assertTrue(button.cget("image"))
                self.assertEqual(button.cget("text"), "")
                self.assertLessEqual(
                    button.winfo_reqwidth(),
                    baselines[action].winfo_reqwidth() * 0.7,
                    "实际按钮宽度必须至少缩减 30%",
                )
                self.assertTrue(button._tooltip.text)
        baseline_frame.destroy()
        before = dict(self.manager.metrics)
        buttons["pin"].invoke()
        self.root.update()
        self.assertEqual(self.displayed_names(), ["third", "first", "second"])
        expected = [
            {**self.entries()[2], "pinned": True},
            self.entries()[0], self.entries()[1],
        ]
        for metric in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.manager.metrics[metric] - before[metric], 1)
        loaded, changed = load_user_config(self.path)
        self.assertFalse(changed)
        self.assertEqual(
            loaded["favorites"]["merge_regular_preamble"], expected,
        )
        active = next(
            child for child in self.popup_rows()[0].winfo_children()
            if getattr(child, "_favorite_action", None) == "pin"
        )
        self.assertTrue(active._favorite_pinned)
        self.assertEqual(active._tooltip.text, "取消置顶")
        self.assert_interaction()

    def test_all_categories_share_pin_order_and_preserve_other_data(self):
        for key in tuple(self.config["favorites"]):
            with self.subTest(category=key):
                self.set_category(key)
                others = deepcopy({
                    category: entries
                    for category, entries in self.config["favorites"].items()
                    if category != key
                })
                self.pin("second")
                self.pin("third")
                self.assertEqual(self.names(), ["third", "second", "first"])
                expected = [
                    {**self.entries()[2], "pinned": True},
                    {**self.entries()[1], "pinned": True},
                    self.entries()[0],
                ]
                self.assertEqual(self.favorite.favorites(), expected)
                self.assertEqual(
                    {
                        category: entries
                        for category, entries in self.config["favorites"].items()
                        if category != key
                    }, others,
                )
                loaded, changed = load_user_config(self.path)
                self.assertFalse(changed)
                self.assertEqual(loaded["favorites"][key], expected)
                self.assert_interaction()

    def test_first_entry_can_pin_and_missing_or_empty_does_not_write(self):
        self.set_category("patch_text")
        self.pin("first")
        self.assertTrue(self.favorite.favorites()[0]["pinned"])
        before = dict(self.manager.metrics)
        self.pin("missing")
        self.assertEqual(self.manager.metrics, before)
        self.favorite.submit([])
        before = dict(self.manager.metrics)
        self.pin("missing")
        self.assertEqual(self.manager.metrics, before)
        self.assert_interaction()

    def test_pin_chronology_unpin_and_repin_are_stable(self):
        self.set_category("patch_text")
        self.pin("third")
        self.assertEqual(self.names(), ["third", "first", "second"])
        self.pin("second")
        self.assertEqual(self.names(), ["second", "third", "first"])
        self.pin("first")
        self.assertEqual(self.names(), ["first", "second", "third"])
        self.pin("second")
        self.assertEqual(self.names(), ["first", "third", "second"])
        self.assertNotIn("pinned", self.favorite.favorites()[2])
        self.pin("second")
        self.assertEqual(self.names(), ["second", "first", "third"])
        self.assertTrue(all(
            entry["pinned"] for entry in self.favorite.favorites()
        ))
        self.assert_interaction()

    def test_add_overwrite_rename_delete_keep_pin_and_position(self):
        self.set_category("merge_regular_preamble")
        self.pin("second")
        self.pin("third")
        with patch("ui.favorites.ask_favorite_name", return_value="new"):
            self.favorite.save_current_text_as_favorite()
        self.assertEqual(self.names(), ["third", "second", "first", "new"])
        with (
            patch("ui.favorites.ask_favorite_name", return_value="second"),
            patch("ui.favorites.safe_ask_yes_no", return_value=True),
        ):
            self.favorite.save_current_text_as_favorite()
        self.assertEqual(self.names(), ["third", "second", "first", "new"])
        self.assertEqual(self.favorite.favorites()[1], {
            "name": "second", "content": "UNCHANGED", "pinned": True,
        })
        with patch("ui.favorites.ask_favorite_name", return_value="renamed"):
            self.favorite.rename_favorite({
                "name": "second", "content": "STALE",
            })
        self.assertEqual(self.names(), ["third", "renamed", "first", "new"])
        self.assertEqual(self.favorite.favorites()[1], {
            "name": "renamed", "content": "UNCHANGED", "pinned": True,
        })
        with patch("ui.favorites.safe_ask_yes_no", return_value=True):
            self.favorite.delete_favorite({"name": "third"})
        self.assertEqual(self.names(), ["renamed", "first", "new"])
        self.assertTrue(self.favorite.favorites()[0]["pinned"])
        loaded, changed = load_user_config(self.path)
        self.assertFalse(changed)
        self.assertEqual(
            loaded["favorites"]["merge_regular_preamble"],
            self.favorite.favorites(),
        )
        self.assert_interaction()

    def test_rename_collision_retains_source_pin_and_position(self):
        self.set_category("patch_text")
        self.pin("second")
        with patch("ui.favorites.ask_favorite_name", return_value="first"):
            self.favorite.rename_favorite({"name": "second", "content": "STALE"})
        self.assertEqual(self.names(), ["first", "third"])
        self.assertEqual(self.favorite.favorites()[0], {
            "name": "first", "content": "SECOND", "pinned": True,
        })

    def test_locked_and_active_operation_do_not_change_pin(self):
        self.set_category("patch_text")
        disk = self.path.read_bytes()
        before = dict(self.manager.metrics)
        with self.commits.operation():
            self.pin("third")
        self.widget.configure(state="disabled")
        try:
            self.pin("third")
            self.assertEqual(str(self.widget.cget("state")), "disabled")
        finally:
            self.widget.configure(state="normal")
        self.assertEqual(self.favorite.favorites(), self.entries())
        self.assertEqual(self.manager.metrics, before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assert_interaction()

    def test_failed_save_keeps_disk_and_retries_without_second_toggle(self):
        self.set_category("patch_text")
        disk = self.path.read_bytes()
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error") as report,
        ):
            self.pin("third")
        report.assert_called_once()
        self.assertEqual(self.path.read_bytes(), disk)
        expected = [
            {**self.entries()[2], "pinned": True},
            self.entries()[0], self.entries()[1],
        ]
        self.assertEqual(self.favorite.favorites(), expected)
        self.assertTrue(self.manager.needs_save)
        self.assert_interaction()
        self.assertTrue(self.commits.finish(self.owner).persisted)
        loaded, changed = load_user_config(self.path)
        self.assertFalse(changed)
        self.assertEqual(loaded["favorites"]["patch_text"], expected)

    def test_name_dialog_reopen_and_reload_consume_same_order(self):
        self.set_category("merge_regular_preamble")
        self.pin("third")
        self.pin("second")
        self.favorite.show_popup()
        self.root.update()
        self.assertEqual(self.displayed_names(), ["second", "third", "first"])
        self.favorite.close_popup()
        self.favorite.show_popup()
        self.root.update()
        self.assertEqual(self.displayed_names(), ["second", "third", "first"])
        self.favorite.close_popup()
        disk = self.path.read_bytes()
        with patch("ui.favorites.ask_favorite_name", return_value=None) as ask:
            self.favorite.save_current_text_as_favorite()
        ask.assert_called_once_with(
            self.owner, existing_names=("second", "third", "first"),
        )
        loaded, changed = load_user_config(self.path)
        self.assertFalse(changed)
        reloaded_manager = ConfigSaveManager(loaded, self.path)
        reloaded_commits = ConfigCommitController(self.root, reloaded_manager)
        reloaded = FavoriteTextBoxController(
            self.owner, tk.Frame(self.owner), self.widget, reloaded_commits,
            "merge_regular_preamble", self.widget._content_operation_completed,
        )
        reloaded.show_popup()
        self.root.update()
        names = [
            child.cget("text")
            for row in reloaded.popup.winfo_children()[0].winfo_children()
            if isinstance(row, tk.Frame)
            for child in row.winfo_children()
            if isinstance(child, tk.Button) and child.pack_info()["side"] == "left"
        ]
        self.assertEqual(names, ["second", "third", "first"])
        reloaded.close_popup()
        self.assertEqual(self.path.read_bytes(), disk)
        self.assert_interaction()

    def test_icon_creation_does_not_submit_configuration(self):
        before = dict(self.manager.metrics)
        frame = tk.Frame(self.owner)
        for action in ("rename", "pin", "delete"):
            button = favorite_action_button(frame, action, lambda: None)
            self.assertEqual(button._favorite_action, action)
            self.assertTrue(button.cget("image"))
        frame.destroy()
        self.assertEqual(self.manager.metrics, before)


if __name__ == "__main__":
    unittest.main()