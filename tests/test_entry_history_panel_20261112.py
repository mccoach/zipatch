# -*- coding: utf-8 -*-

"""历史下拉必须在同一顶层窗口内实际显示，并保留编辑与关闭交互。"""

import tkinter as tk
import unittest
from types import SimpleNamespace

from ui.entry_history import EntryHistoryPlugin


class EntryHistoryPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.geometry("700x350")
        self.field = tk.Frame(self.root)
        self.field.pack(fill="x", padx=10, pady=10)
        self.variable = tk.StringVar(master=self.root, value="draft")
        self.entry = tk.Entry(self.field, textvariable=self.variable, width=1)
        self.entry.pack(side="left", fill="x", expand=True)
        self.other = tk.Entry(self.root)
        self.other.pack(fill="x", padx=10, pady=10)
        self.submissions = []
        self.commits = SimpleNamespace(
            operation_depth=0,
            manager=SimpleNamespace(config_data={
                "entry_history": {
                    "test.path": [
                        f"E:/folder{number:02d}" for number in range(20)
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

    def assert_visible_panel(self):
        panel = self.plugin.popup
        self.assertIsNotNone(panel)
        self.assertIsInstance(panel, tk.Frame)
        self.assertIs(panel.winfo_toplevel(), self.root)
        self.assertTrue(panel.winfo_ismapped())
        self.assertGreater(panel.winfo_width(), 1)
        self.assertGreater(panel.winfo_height(), 1)
        self.assertTrue(self.plugin.listbox.winfo_ismapped())

    def test_button_opens_mapped_panel_in_same_toplevel(self):
        self.plugin.button.invoke()
        self.root.update()
        self.assert_visible_panel()
        self.assertIs(self.root.focus_displayof(), self.entry)

    def test_keyboard_opens_visible_panel_and_escape_returns_editing(self):
        self.entry.event_generate("<Down>")
        self.root.update()
        self.assert_visible_panel()
        self.entry.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.plugin.popup)
        self.entry.event_generate("<KeyPress-x>")
        self.root.update()
        self.assertIn("x", self.variable.get())

    def test_typing_filters_while_panel_remains_visible(self):
        self.plugin.button.invoke()
        self.root.update()
        self.variable.set("folder01")
        self.root.update()
        self.assert_visible_panel()
        self.assertEqual(self.plugin.visible_values, ("E:/folder01",))
        self.assertEqual(self.submissions, [])

    def test_clicking_other_widget_closes_without_forcing_focus_back(self):
        self.plugin.show_popup()
        self.root.update()
        self.other.event_generate("<ButtonPress-1>", x=5, y=5)
        self.root.update()
        self.assertIsNone(self.plugin.popup)

    def test_hiding_entry_closes_panel(self):
        self.plugin.show_popup()
        self.root.update()
        self.field.pack_forget()
        self.root.update()
        self.assertIsNone(self.plugin.popup)

    def test_destroy_releases_only_plugin_owner_callbacks(self):
        retained = self.root.bind(
            "<ButtonPress-1>", lambda event: None, add="+",
        )
        owned = tuple(self.plugin._owner_bindings.values())
        self.entry.destroy()
        self.root.update()
        script = self.root.bind("<ButtonPress-1>")
        self.assertIn(retained, script)
        for callback_id in owned:
            self.assertFalse(self.root.tk.call("info", "commands", callback_id))


if __name__ == "__main__":
    unittest.main()
