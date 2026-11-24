# -*- coding: utf-8 -*-

import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from core.config import remove_retired_output_config
from core.paths import resolve_output_path
from ui.page_layout import protect_page_controls


class OutputPathTests(unittest.TestCase):
    def test_migration_preserves_old_selection_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = {"merge": {
                "output_folder": temporary,
                "output_filename": "chosen.txt",
            }}
            result = remove_retired_output_config(source)
            expected = str(Path(temporary) / "chosen.txt").replace("/", "\\")
            self.assertEqual(result["merge"]["output_path"], expected)
            self.assertNotIn("output_folder", result["merge"])
            self.assertNotIn("output_filename", result["merge"])
            self.assertIn("output_folder", source["merge"])
            self.assertEqual(remove_retired_output_config(result), result)

    def test_new_path_has_priority_and_incomplete_old_selection_stays_empty(self):
        result = remove_retired_output_config({
            "scan": {"output_filename": "scan.txt"},
            "merge": {
                "output_path": "E:\\new\\chosen.txt",
                "output_folder": "E:\\old",
                "output_filename": "old.txt",
            },
        })
        self.assertEqual(result["scan"]["output_path"], "")
        self.assertEqual(result["merge"]["output_path"], "E:\\new\\chosen.txt")

    def test_absolute_path_extension_and_relative_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result"
            self.assertEqual(resolve_output_path(str(path)), str(path) + ".txt")
            self.assertEqual(
                resolve_output_path(str(path) + ".md"), str(path) + ".md",
            )
            with self.assertRaises(ValueError):
                resolve_output_path("result.txt")
            with self.assertRaises(ValueError):
                resolve_output_path(temporary + os.sep)

    def test_invalid_config_type_is_not_silently_defaulted(self):
        with self.assertRaises(ValueError):
            remove_retired_output_config({"scan": {"output_path": 123}})


class RigidPageLayoutTests(unittest.TestCase):
    def test_execution_and_summary_are_allocated_before_text(self):
        root = tk.Tk()
        try:
            root.geometry("700x300")
            body = tk.Frame(root)
            body.pack(fill="both", expand=True)
            title = tk.Label(body, text="源码")
            title.pack(fill="x")
            area = tk.Frame(body)
            area.pack(fill="both", expand=True)
            text = tk.Text(area, height=22)
            text.pack(fill="both", expand=True)
            summary = tk.Label(body, text="统计")
            summary.pack(fill="x")
            actions = tk.Frame(body)
            actions.pack(fill="x")
            button = tk.Button(actions, text="执行")
            button.pack(side="right")
            protect_page_controls(body)
            # 完成窗口映射后检查真实坐标，不只检查请求尺寸。
            root.update()
            root.update_idletasks()
            self.assertTrue(actions.winfo_ismapped())
            self.assertTrue(summary.winfo_ismapped())
            self.assertGreater(actions.winfo_height(), 1)
            self.assertGreater(summary.winfo_height(), 1)
            self.assertEqual(int(text.cget("height")), 3)
            self.assertEqual(actions.pack_info()["side"], "bottom")
            self.assertEqual(summary.pack_info()["side"], "bottom")
            self.assertLess(summary.winfo_y(), actions.winfo_y())
            self.assertLessEqual(
                actions.winfo_y() + actions.winfo_height(), body.winfo_height(),
            )
            self.assertTrue(button.winfo_ismapped())
            self.assertLess(
                body.pack_slaves().index(actions),
                body.pack_slaves().index(area),
            )
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)

class LayoutProtectionCallbackTests(unittest.TestCase):
    def test_unchanged_limits_do_not_issue_geometry_writes(self):
        from unittest.mock import Mock
        from app import ZipatchApp

        panel = Mock()
        panel.winfo_reqheight.return_value = 200
        panel.winfo_reqwidth.return_value = 800
        log_frame = Mock()
        log_frame.winfo_reqheight.return_value = 100
        root = Mock()
        root.winfo_height.return_value = 800
        root.minsize.return_value = (1080, 433)
        workspace = Mock()
        workspace.winfo_height.return_value = 700
        workspace.panecget.side_effect = (
            lambda pane, option: 226 if pane is content_frame else 100
        )
        workspace.sash_coord.return_value = (0, 400)
        content_frame = object()
        application = Mock()
        application._layout_after_id = None
        application.current_panel = panel
        application.log_frame = log_frame
        application.content_frame = content_frame
        application.root = root
        application.workspace = workspace

        ZipatchApp.refresh_layout_protection(application)

        workspace.paneconfigure.assert_not_called()
        workspace.sash_place.assert_not_called()
        root.minsize.assert_called_once_with()