# -*- coding: utf-8 -*-

"""真实Tk历史窗口的编辑焦点、按键分发和非抢焦点关闭回归。"""

import tkinter as tk
import unittest
from types import SimpleNamespace

from core.constants import DEFAULT_CONFIG
from ui.entry_history import EntryHistoryPlugin
from ui.window_manager import validate_temporary_popups


class EntryHistoryFocusTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.geometry("700x300")
        self.frame = tk.Frame(self.root)
        self.frame.pack(fill="x", padx=10, pady=10)
        self.variable = tk.StringVar(master=self.root, value="editing")
        self.entry = tk.Entry(self.frame, textvariable=self.variable, width=1)
        self.entry.pack(side="left", fill="x", expand=True)
        self.other = tk.Entry(self.root)
        self.other.pack(fill="x", padx=10)
        self.submissions = []
        self.commits = SimpleNamespace(
            operation_depth=0,
            manager=SimpleNamespace(config_data={
                "entry_history": {
                    "test.path": [
                        f"E:/project/folder{number:02d}"
                        for number in range(20)
                    ],
                },
            }),
            submit_editors=lambda editors, parent=None:
            self.submissions.append(tuple(editors)),
        )
        self.binding = SimpleNamespace(
            page="patch", field="project_root", dirty=False,
        )
        self.plugin = EntryHistoryPlugin(
            self.root, self.entry, self.variable,
            self.binding, self.commits, "test.path",
        )
        self.root.update()
        self.entry.focus_force()
        self.root.update()

    def assert_entry_has_focus(self):
        self.assertIs(self.root.focus_displayof(), self.entry)

    def test_mouse_button_open_keeps_entry_keyboard_focus(self):
        self.plugin.button.invoke()
        self.root.update()
        self.assertIsNotNone(self.plugin.popup)
        self.assert_entry_has_focus()
        self.entry.event_generate("<KeyPress-x>")
        self.root.update()
        self.assertIn("x", self.variable.get())

    def test_mouse_button_open_can_escape_then_type(self):
        self.plugin.button.invoke()
        self.root.update()
        self.entry.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.plugin.popup)
        self.assert_entry_has_focus()
        self.entry.event_generate("<KeyPress-x>")
        self.root.update()
        self.assertIn("x", self.variable.get())

    def test_keyboard_navigation_does_not_transfer_focus_to_list(self):
        self.entry.event_generate("<Down>")
        self.root.update()
        self.assert_entry_has_focus()
        self.assertEqual(self.plugin.listbox.curselection(), (0,))
        self.entry.event_generate("<Down>")
        self.root.update()
        self.assertEqual(self.plugin.listbox.curselection(), (1,))
        self.entry.event_generate("<Up>")
        self.root.update()
        self.assertEqual(self.plugin.listbox.curselection(), (0,))
        self.assert_entry_has_focus()

    def test_enter_accepts_then_allows_typing_and_reopening(self):
        self.entry.event_generate("<Down>")
        self.root.update()
        self.entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.variable.get(), "E:/project/folder00")
        self.assertEqual(len(self.submissions), 1)
        self.assertIsNone(self.plugin.popup)
        self.assert_entry_has_focus()
        self.entry.event_generate("<KeyPress-x>")
        self.root.update()
        self.assertEqual(self.variable.get(), "E:/project/folder00x")
        self.entry.event_generate("<Down>")
        self.root.update()
        self.assertIsNotNone(self.plugin.popup)
        self.assert_entry_has_focus()

    def test_clicking_entry_returns_from_list_focus(self):
        self.plugin.show_popup()
        self.plugin.listbox.focus_force()
        self.root.update()
        self.entry.event_generate("<ButtonRelease-1>", x=10, y=10)
        self.root.update()
        self.assert_entry_has_focus()

    def test_automatic_close_does_not_steal_other_editor_focus(self):
        self.plugin.show_popup()
        self.other.focus_force()
        self.root.update()
        validate_temporary_popups(self.root)
        self.root.update()
        self.assertIsNone(self.plugin.popup)
        self.assertIs(self.root.focus_displayof(), self.other)

    def test_twenty_items_remain_scrollable_with_five_visible_rows(self):
        self.plugin.show_popup()
        self.root.update()
        self.assertEqual(len(self.plugin.visible_values), 20)
        self.assertEqual(int(self.plugin.listbox.cget("height")), 5)
        for _ in range(20):
            self.plugin.move_selection(1)
        self.assertEqual(self.plugin.listbox.curselection(), (19,))
        self.assertGreater(self.plugin.listbox.yview()[0], 0)

    def test_up_binding_callback_is_released_on_destroy(self):
        tag = self.plugin.key_tag
        callback_ids = tuple(self.plugin._key_callbacks.values())
        self.assertIn("<Up>", self.plugin._key_callbacks)
        self.entry.destroy()
        self.root.update()
        self.assertEqual(self.root.bind_class(tag, "<Up>"), "")
        for callback_id in callback_ids:
            self.assertFalse(
                self.root.tk.call("info", "commands", callback_id),
            )


class HistoryDefaultsTests(unittest.TestCase):
    def test_history_default_limit_is_twenty(self):
        self.assertEqual(
            DEFAULT_CONFIG["settings"]["entry_history_max_items"], 20,
        )


if __name__ == "__main__":
    unittest.main()
