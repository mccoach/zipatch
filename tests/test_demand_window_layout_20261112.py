# -*- coding: utf-8 -*-

"""真实Tk合并列表窗口的屏幕高度及底部动作可见性回归。"""

import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from ui.config_editing import ConfigCommitController
from ui.demand_editor import open_demand_editor


class DemandWindowLayoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.geometry("800x400")
        configuration = deepcopy(DEFAULT_CONFIG)
        manager = ConfigSaveManager(
            configuration,
            path=Path(self.directory.name) / "configuration.json",
        )
        self.panel = tk.Frame(self.root)
        self.panel.pack(fill="both", expand=True)
        self.panel.root = self.root
        self.panel.cfg = configuration["merge"]
        self.panel.commits = ConfigCommitController(self.root, manager)
        self.root.update()

    def test_window_and_all_actions_remain_visible(self):
        session = open_demand_editor(self.panel)
        self.addCleanup(session.cancel)
        self.root.update()

        dialog = session.dialog
        screen_height = dialog.winfo_screenheight()
        self.assertLessEqual(
            dialog.winfo_height(), min(700, screen_height - 100),
        )
        self.assertGreaterEqual(dialog.winfo_rooty(), 0)
        self.assertLessEqual(
            dialog.winfo_rooty() + dialog.winfo_height(), screen_height,
        )

        bottom = session.cancel_button.master
        buttons = {
            widget.cget("text"): widget
            for widget in bottom.winfo_children()
            if isinstance(widget, tk.Button)
        }
        self.assertEqual(set(buttons), {"应用", "保存", "取消"})
        for caption, button in buttons.items():
            with self.subTest(button=caption):
                self.assertTrue(button.winfo_ismapped())
                self.assertGreater(button.winfo_width(), 0)
                self.assertGreaterEqual(
                    button.winfo_rooty(), dialog.winfo_rooty(),
                )
                self.assertLessEqual(
                    button.winfo_rooty() + button.winfo_height(),
                    dialog.winfo_rooty() + dialog.winfo_height(),
                )

        self.assertLess(
            buttons["应用"].winfo_rootx(), buttons["保存"].winfo_rootx(),
        )
        self.assertLess(
            buttons["保存"].winfo_rootx(), buttons["取消"].winfo_rootx(),
        )


if __name__ == "__main__":
    unittest.main()
