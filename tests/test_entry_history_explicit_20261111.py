# -*- coding: utf-8 -*-

"""真实Tk路径历史交互回归，不读写用户配置。"""

import tkinter as tk
import unittest
from types import SimpleNamespace

from ui.entry_history import EntryHistoryPlugin


class TestCommits:
    def __init__(self, values):
        self.operation_depth = 0
        self.manager = SimpleNamespace(
            config_data={"entry_history": {"test.path": list(values)}},
            accept=self.accept,
        )
        self.submitted = 0

    def accept(self, target, values):
        target.update(values)
        return True

    def submit_editors(self, editors, parent=None):
        self.submitted += 1

    def select(self, page, fields):
        return ()

    def accept_editors(self, editors):
        return False

    def finish(self, parent=None):
        return None


class EntryHistoryInteractionTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("600x250")
        self.frame = tk.Frame(self.root)
        self.frame.pack(fill="x", padx=10, pady=10)
        self.variable = tk.StringVar(master=self.root)
        self.entry = tk.Entry(self.frame, textvariable=self.variable, width=1)
        self.entry.pack(side="left", fill="x", expand=True)
        self.binding = SimpleNamespace(
            page="patch", field="project_root", dirty=False,
        )
        self.commits = TestCommits([
            "E:/project/src", "E:/project/docs", "D:/archive",
            "E:/one", "E:/two", "E:/three", "E:/four",
        ])
        self.plugin = EntryHistoryPlugin(
            self.root, self.entry, self.variable,
            self.binding, self.commits, "test.path",
        )
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_focus_does_not_open_history(self):
        self.entry.focus_force()
        self.entry.event_generate("<FocusIn>")
        self.root.update()
        self.assertIsNone(self.plugin.popup)

    def test_explicit_open_is_limited_to_five_rows(self):
        self.plugin.show_popup()
        self.root.update()
        self.assertIsNotNone(self.plugin.popup)
        self.assertEqual(int(self.plugin.listbox.cget("height")), 5)
        self.assertEqual(len(self.plugin.visible_values), 7)

    def test_typing_filters_without_accepting_path(self):
        self.plugin.show_popup()
        self.variable.set("project")
        self.root.update()
        self.assertEqual(
            self.plugin.visible_values,
            ("E:/project/src", "E:/project/docs"),
        )
        self.assertEqual(self.variable.get(), "project")
        self.assertEqual(self.commits.submitted, 0)

    def test_selection_accepts_once_and_closes(self):
        self.plugin.show_popup()
        self.plugin.listbox.selection_set(1)
        self.plugin.accept_selection()
        self.assertEqual(self.variable.get(), "E:/project/docs")
        self.assertIsNone(self.plugin.popup)
        self.assertEqual(self.commits.submitted, 1)

    def test_escape_keeps_current_input(self):
        self.variable.set("unfinished")
        self.plugin.show_popup()
        self.plugin.escape()
        self.assertEqual(self.variable.get(), "unfinished")
        self.assertIsNone(self.plugin.popup)
        self.assertEqual(self.commits.submitted, 0)

    def test_no_match_does_not_offer_a_fake_path(self):
        self.plugin.show_popup()
        self.variable.set("does-not-exist-in-history")
        self.root.update()
        self.assertEqual(self.plugin.visible_values, ())
        self.plugin.accept_selection()
        self.assertEqual(self.commits.submitted, 0)

    def test_delete_changes_history_not_current_input(self):
        self.variable.set("current-input")
        self.plugin.show_popup()
        self.plugin.listbox.selection_set(0)
        self.plugin.delete_selected_history()
        self.assertNotIn(
            "E:/project/src",
            self.commits.manager.config_data["entry_history"]["test.path"],
        )
        self.assertEqual(self.variable.get(), "current-input")

    def test_input_destruction_closes_popup(self):
        self.plugin.show_popup()
        callback_ids = tuple(self.plugin._key_callbacks.values())
        tag = self.plugin.key_tag
        self.entry.destroy()
        self.root.update()
        self.assertIsNone(self.plugin.popup)
        self.assertEqual(self.plugin._key_callbacks, {})
        for callback_id in callback_ids:
            self.assertFalse(self.root.tk.call("info", "commands", callback_id))
        for sequence in ("<Down>", "<Alt-Down>", "<Escape>", "<Return>"):
            self.assertEqual(self.root.bind_class(tag, sequence), "")

    def test_custom_tag_is_not_a_window_path(self):
        self.assertFalse(self.plugin.key_tag.startswith("."))
        self.assertEqual(self.entry.bindtags()[0], self.plugin.key_tag)

    def test_down_event_opens_history(self):
        self.entry.focus_force()
        self.root.update()
        self.entry.event_generate("<Down>")
        self.root.update()
        self.assertIsNotNone(self.plugin.popup)
        self.assertEqual(self.plugin.listbox.curselection(), (0,))
        self.assertEqual(self.commits.submitted, 0)

    def test_alt_down_event_opens_history(self):
        self.entry.focus_force()
        self.root.update()
        self.entry.event_generate("<Alt-Down>")
        self.root.update()
        self.assertIsNotNone(self.plugin.popup)
        self.assertEqual(self.commits.submitted, 0)

    def test_enter_selection_stops_original_entry_submit(self):
        original_calls = []
        self.entry.bind("<Return>", lambda event: original_calls.append(True))
        self.plugin.show_popup()
        self.plugin.listbox.selection_set(1)
        self.entry.focus_force()
        self.root.update()
        self.entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.variable.get(), "E:/project/docs")
        self.assertEqual(self.commits.submitted, 1)
        self.assertEqual(original_calls, [])
        self.assertIsNone(self.plugin.popup)

    def test_enter_without_history_keeps_original_entry_binding(self):
        original_calls = []
        self.entry.bind("<Return>", lambda event: original_calls.append(True))
        self.entry.focus_force()
        self.root.update()
        self.entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(original_calls, [True])
        self.assertEqual(self.commits.submitted, 0)

    def test_listbox_escape_event_closes_without_accepting(self):
        self.variable.set("unfinished")
        self.entry.focus_force()
        self.root.update()
        self.entry.event_generate("<Down>")
        self.root.update()
        self.plugin.listbox.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.plugin.popup)
        self.assertEqual(self.variable.get(), "unfinished")
        self.assertEqual(self.commits.submitted, 0)


if __name__ == "__main__":
    unittest.main()
