# -*- coding: utf-8 -*-

"""共同授权列表滚轮、分类分控和固定跳过的真实Tk回归。"""

import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from ui.config_editing import ConfigCommitController
from ui.work_scope import WorkScopeChoice, select_work_scope


class WorkScopeControlsTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.temporary = TemporaryDirectory()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(
            self.config,
            path=Path(self.temporary.name) / "config.json",
            needs_write=True,
        )
        self.commits = ConfigCommitController(self.root, self.manager)
        self.errors = []
        self.root.report_callback_exception = (
            lambda error_type, error, traceback: self.errors.append(error)
        )

    def tearDown(self):
        self.root.destroy()
        self.temporary.cleanup()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def run_window(self, choices, perform):
        original_wait = self.root.wait_window
        before = deepcopy(self.config)

        def wait(window):
            def action():
                try:
                    window.update_idletasks()
                    perform(window)
                finally:
                    if window.winfo_exists():
                        window.tk.call(window.protocol("WM_DELETE_WINDOW"))

            window.after_idle(action)
            return original_wait(window)

        with patch.object(self.root, "wait_window", side_effect=wait):
            result = select_work_scope(
                self.root, self.commits,
                title="共同授权控件测试",
                introduction="测试固定工作项。",
                choices=choices, details="固定详情",
            )
        self.assertEqual(self.errors, [])
        self.assertEqual(self.config, before)
        self.assertFalse(self.manager.path.exists())
        self.assertIsNone(self.root.grab_current())
        return result

    def test_group_controls_change_only_their_group_and_never_select_fixed_skip(self):
        choices = (
            WorkScopeChoice("文件链接一", "文件软链接"),
            WorkScopeChoice("文件链接二", "文件软链接"),
            WorkScopeChoice("固定跳过", "文件软链接", True),
            WorkScopeChoice("目录链接", "目录软链接"),
        )

        def perform(window):
            buttons = tuple(
                widget for widget in self.descendants(window)
                if isinstance(widget, tk.Button)
            )
            group_buttons = {
                (button._scope_group, button._scope_group_value): button
                for button in buttons if hasattr(button, "_scope_group")
            }
            self.assertEqual(len(group_buttons), 4)
            group_buttons[("文件软链接", False)].invoke()
            self.assertEqual(
                tuple(variable.get() for variable in window._scope_variables),
                (False, False, False, True),
            )
            group_buttons[("文件软链接", True)].invoke()
            self.assertEqual(
                tuple(variable.get() for variable in window._scope_variables),
                (True, True, False, True),
            )
            group_buttons[("目录软链接", False)].invoke()
            next(
                button for button in buttons
                if button.cget("text") == "按所选继续"
            ).invoke()

        self.assertEqual(
            self.run_window(choices, perform),
            ("authorize", "authorize", "skip", "skip"),
        )

    def test_group_with_only_fixed_skips_has_disabled_controls(self):
        choices = (
            WorkScopeChoice("固定跳过", "文件软链接", True),
        )

        def perform(window):
            buttons = tuple(
                widget for widget in self.descendants(window)
                if isinstance(widget, tk.Button)
                and hasattr(widget, "_scope_group")
            )
            self.assertEqual(len(buttons), 2)
            self.assertTrue(all(
                str(button.cget("state")) == "disabled"
                for button in buttons
            ))
            self.assertFalse(window._scope_variables[0].get())

        self.assertIsNone(self.run_window(choices, perform))

    def test_mousewheel_from_checkbox_heading_canvas_and_scrollbar_scrolls_list(self):
        choices = tuple(
            WorkScopeChoice(f"文件链接 {number}", "文件软链接")
            for number in range(80)
        )

        def perform(window):
            widgets = tuple(self.descendants(window))
            canvas = next(widget for widget in widgets if isinstance(widget, tk.Canvas))
            viewport = canvas.master
            checkbox = next(
                widget for widget in widgets if isinstance(widget, tk.Checkbutton)
            )
            heading = next(
                widget for widget in widgets
                if isinstance(widget, tk.Label)
                and str(widget.cget("text")).startswith("文件软链接（")
            )
            scrollbar = next(
                widget for widget in viewport.winfo_children()
                if isinstance(widget, tk.Scrollbar)
            )
            canvas.configure(scrollregion=(0, 0, 1000, 6000))
            binding = viewport.bind("<MouseWheel>")
            self.assertTrue(binding)
            for widget in (checkbox, heading, canvas, scrollbar):
                self.assertEqual(widget.bindtags()[0], viewport._w)
                canvas.yview_moveto(0)
                before = canvas.yview()[0]
                widget.event_generate("<MouseWheel>", delta=-120)
                self.assertGreater(canvas.yview()[0], before)
                widget.event_generate("<MouseWheel>", delta=120)
                self.assertAlmostEqual(canvas.yview()[0], before)
            self.assertNotIn(viewport._w, window.bindtags())
            for sequence in ("<Button-4>", "<Button-5>"):
                self.assertTrue(viewport.bind(sequence))

        self.assertIsNone(self.run_window(choices, perform))


if __name__ == "__main__":
    unittest.main()