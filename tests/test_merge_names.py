# -*- coding: utf-8 -*-

"""名称统一不改变目录选择、名单顺序或正式成果生成。"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG
from core.exclusion_rules import prepare_exclusions
from core.text_io import read_text_content_for_merge
from services.merge_service import merge_demand_files, merge_project_files


class MergeNameServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.source = self.folder / "source"
        self.source.mkdir()
        self.first = self.source / "a.txt"
        self.second = self.source / "b.txt"
        self.first.write_text("A", encoding="utf-8")
        self.second.write_text("B", encoding="utf-8")
        self.output = self.folder / "merged.txt"

    def test_directory_merge_logs_use_selected_name_and_content_is_unchanged(self):
        logs = []
        result = merge_project_files(
            self.source, self.output, prepare_exclusions("", ""),
            "", "", "HEADER", "FOOTER", log_func=logs.append,
        )
        self.assertEqual(result.total_files, 2)
        self.assertIn("开始按目录合并", logs)
        self.assertIn("按目录合并完成：共 2 个文件", logs)
        self.assertFalse(any("常规" in message for message in logs))
        text = self.output.read_text(encoding="utf-8")
        self.assertLess(text.index(str(self.first)), text.index(str(self.second)))
        self.assertIn("HEADER\nA\nFOOTER", text)
        self.assertIn("HEADER\nB\nFOOTER", text)

    def test_list_merge_logs_use_selected_name_and_selection_order_is_unchanged(self):
        logs = []
        result = merge_demand_files(
            (str(self.second), str(self.first)), self.output,
            "", "", "HEADER", "FOOTER", log_func=logs.append,
        )
        self.assertEqual(result.total_files, 2)
        self.assertIn("开始按名单合并", logs)
        self.assertIn("按名单合并完成：共 2 个文件", logs)
        self.assertFalse(any("按需" in message for message in logs))
        text = self.output.read_text(encoding="utf-8")
        self.assertLess(text.index(str(self.second)), text.index(str(self.first)))

    def test_selected_output_rejection_uses_list_merge_name(self):
        self.output.write_bytes(b"OLD")
        with self.assertRaisesRegex(ValueError, "不能执行按名单合并"):
            merge_demand_files(
                (str(self.output),), self.output,
                "", "", "HEADER", "FOOTER",
            )
        self.assertEqual(self.output.read_bytes(), b"OLD")

    def test_source_read_error_uses_list_merge_name(self):
        with patch(
            "core.text_io.Path.read_bytes",
            side_effect=PermissionError("source denied"),
        ):
            with self.assertRaisesRegex(OSError, "无法读取按名单合并源文件"):
                read_text_content_for_merge(
                    self.first, "READ_ERROR", "CANNOT_READ", strict=True,
                )


class MergeNameGuiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.errors = []
        self.root.report_callback_exception = (
            lambda kind, error, traceback: self.errors.append(error)
        )
        self.addCleanup(self.cleanup_root)
        self.path = Path(self.directory.name) / "config.json"
        config = deep_merge_config(DEFAULT_CONFIG, {})
        config["merge"]["demand_file_list_text"] = "|unused"
        manager = ConfigSaveManager(config, self.path, needs_write=True)
        self.assertTrue(manager.save().persisted)
        with patch("app.raise_and_focus"):
            self.app = ZipatchApp(self.root, config_path=self.path)

    def cleanup_root(self):
        if hasattr(self, "session"):
            self.session.destroy()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def button_texts(self, widget):
        texts = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Button):
                texts.append(child.cget("text"))
            texts.extend(self.button_texts(child))
        return texts

    def test_buttons_and_list_window_use_selected_names_without_config_write(self):
        original_disk = self.path.read_bytes()
        before = dict(self.app.config_manager.metrics)
        panel = self.app.current_panel
        texts = self.button_texts(panel)
        self.assertIn("按目录合并", texts)
        self.assertIn("按名单合并", texts)
        self.assertNotIn("常规合并", texts)
        self.assertNotIn("按需合并", texts)

        self.session = panel.open_demand_merge_dialog()
        self.root.update()
        self.assertEqual(self.session.dialog.title(), "按名单合并")
        self.assertIn("执行按名单合并", self.button_texts(self.session.dialog))
        self.assertEqual(self.app.config_manager.metrics, before)
        self.assertEqual(self.path.read_bytes(), original_disk)


if __name__ == "__main__":
    unittest.main()
