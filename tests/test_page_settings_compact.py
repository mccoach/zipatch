# -*- coding: utf-8 -*-

import tkinter as tk
import unittest
from unittest.mock import Mock

from ui.page_settings import create_page_settings


class CompactPageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("1100x220")
        self.commits = Mock()

    def tearDown(self):
        self.root.destroy()

    def common_widgets(self, row):
        checks = next(
            child for child in row.winfo_children()
            if child.grid_info().get("column") == 0
        )
        return sorted(
            checks.winfo_children(),
            key=lambda child: int(child.grid_info()["column"]),
        )

    def test_scan_missing_newline_option_leaves_no_empty_column(self):
        config = {
            "open_after_done": True,
            "show_actual_path": True,
            "force_overwrite": True,
        }
        row, specific, variables = create_page_settings(
            self.root, self.commits, config,
            (
                ("open_after_done", "完成后打开", "打开扫描成果"),
                ("show_actual_path", "显示实际路径", "显示实际路径"),
                ("force_overwrite", "成果强制覆盖", "直接覆盖"),
            ),
        )
        self.root.update()
        widgets = self.common_widgets(row)
        self.assertEqual(
            [int(widget.grid_info()["column"]) for widget in widgets],
            [0, 1],
        )
        self.assertEqual(
            [widget.cget("text") for widget in widgets],
            ["完成后打开成果", "显示实际路径"],
        )
        gap = widgets[1].winfo_x() - (
            widgets[0].winfo_x() + widgets[0].winfo_width()
        )
        self.assertGreaterEqual(gap, 0)
        self.assertLessEqual(gap, 10)
        self.assertEqual(len(specific.winfo_children()), 1)
        self.commits.manager.accept.assert_not_called()

    def test_common_order_is_stable_even_when_input_order_changes(self):
        config = {
            "show_actual_path": True,
            "normalize_text_newlines": False,
            "open_after_done": True,
        }
        row, _, _ = create_page_settings(
            self.root, self.commits, config,
            (
                ("show_actual_path", "显示实际路径", ""),
                ("normalize_text_newlines", "标准化换行", ""),
                ("open_after_done", "完成后打开", ""),
            ),
        )
        self.root.update()
        self.assertEqual(
            [widget.cget("text") for widget in self.common_widgets(row)],
            ["完成后打开成果", "标准化换行", "显示实际路径"],
        )

    def test_backup_open_label_is_explicit(self):
        row, _, variables = create_page_settings(
            self.root, self.commits,
            {"open_backup_after_done": True},
            (("open_backup_after_done", "完成后打开", "打开本次备份"),),
        )
        self.root.update()
        widgets = self.common_widgets(row)
        self.assertEqual(widgets[0].cget("text"), "完成后打开备份")
        self.assertTrue(variables["open_backup_after_done"].get())


if __name__ == "__main__":
    unittest.main(verbosity=2)