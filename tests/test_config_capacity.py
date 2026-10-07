# -*- coding: utf-8 -*-

import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from time import perf_counter
from tkinter import TclError
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG


class ConfigCapacityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.root = tk.Tk()
        except TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.callback_errors = []
        self.root.report_callback_exception = self.report_callback_error

    def report_callback_error(self, error_type, error, traceback):
        self.callback_errors.append(str(error))

    def destroy_root(self):
        self.root.destroy()

    def tearDown(self):
        if hasattr(self, "callback_errors"):
            self.assertEqual(self.callback_errors, [])

    def verify_capacity(self, character_count):
        path = Path(self.directory.name) / "config.json"
        config = deep_merge_config(DEFAULT_CONFIG, {})
        config["patch"]["patch_text"] = "x" * character_count
        manager = ConfigSaveManager(config, path, needs_write=True)
        self.assertTrue(manager.save().persisted)

        with patch("app.raise_and_focus"):
            app = ZipatchApp(self.root, config_path=path)

        self.assertEqual(app.config_manager.metrics["write_attempts"], 0)
        self.assertEqual(set(app.panels), {"merge"})

        # 创建页面与缓存切换分别测量，避免把首次建页混入缓存性能。
        start = perf_counter()
        for mode in ("scan", "patch", "restore", "merge"):
            app.show_panel(mode)
        first_page_seconds = perf_counter() - start
        self.root.update_idletasks()

        panels = dict(app.panels)
        buttons = dict(app.tabs.buttons)
        save_before = dict(app.config_manager.metrics)
        edit_before = dict(app.commits.metrics)

        start = perf_counter()
        for _ in range(10):
            for mode in ("scan", "patch", "restore", "merge"):
                app.show_panel(mode)
        cached_page_seconds = perf_counter() - start

        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(
                app.config_manager.metrics[key], save_before[key],
            )
        self.assertEqual(
            app.commits.metrics["text_reads"], edit_before["text_reads"],
        )
        for mode, panel in panels.items():
            self.assertIs(app.panels[mode], panel)
        for mode, button in buttons.items():
            self.assertIs(app.tabs.buttons[mode], button)

        app.show_panel("patch")
        text = app.current_panel.patch_text
        binding = text._config_binding
        text.insert("end-1c", "y")

        save_before = dict(app.config_manager.metrics)
        edit_before = dict(app.commits.metrics)
        start = perf_counter()
        result = app.commits.submit_editors([binding], self.root)
        commit_seconds = perf_counter() - start
        self.assertTrue(result.persisted)
        self.assertEqual(
            app.config_data["patch"]["patch_text"],
            "x" * character_count + "y",
        )
        self.assertEqual(
            app.commits.metrics["text_reads"] - edit_before["text_reads"], 1,
        )
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(
                app.config_manager.metrics[key] - save_before[key], 1,
            )

        save_after = dict(app.config_manager.metrics)
        edit_after = dict(app.commits.metrics)
        for _ in range(5):
            self.assertTrue(
                app.commits.submit_editors([binding], self.root).persisted,
            )
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(app.config_manager.metrics[key], save_after[key])
        self.assertEqual(
            app.commits.metrics["text_reads"], edit_after["text_reads"],
        )
        self.assertEqual(app.commits.operation_depth, 0)
        self.assertEqual(app.config_manager._batch_depth, 0)
        self.assertFalse(app.config_manager._batch_aborted)

        disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(disk["patch"]["patch_text"], "x" * character_count + "y")

        report = {
            "initial_text_characters": character_count,
            "first_page_creation_seconds": first_page_seconds,
            "cached_page_switch_count": 40,
            "cached_page_switch_total_seconds": cached_page_seconds,
            "cached_page_switch_average_seconds": cached_page_seconds / 40,
            "text_commit_total_seconds": commit_seconds,
            "text_read_seconds": (
                edit_after["text_read_seconds"] - edit_before["text_read_seconds"]
            ),
            "json_seconds": save_after["json_seconds"] - save_before["json_seconds"],
            "compare_seconds": (
                save_after["compare_seconds"] - save_before["compare_seconds"]
            ),
            "write_seconds": save_after["write_seconds"] - save_before["write_seconds"],
            "fsync_seconds": save_after["fsync_seconds"] - save_before["fsync_seconds"],
            "replace_seconds": (
                save_after["replace_seconds"] - save_before["replace_seconds"]
            ),
            "text_reads": edit_after["text_reads"] - edit_before["text_reads"],
            "json_generations": (
                save_after["json_generations"] - save_before["json_generations"]
            ),
            "write_attempts": (
                save_after["write_attempts"] - save_before["write_attempts"]
            ),
            "successful_replaces": (
                save_after["successful_replaces"] - save_before["successful_replaces"]
            ),
            "operation_depth": app.commits.operation_depth,
            "batch_depth": app.config_manager._batch_depth,
        }
        print("CAPACITY_RESULT=" + json.dumps(report, ensure_ascii=False))

    def test_empty_text_capacity(self):
        self.verify_capacity(0)

    def test_500000_character_capacity(self):
        self.verify_capacity(500_000)

    def test_5000000_character_capacity(self):
        self.verify_capacity(5_000_000)


if __name__ == "__main__":
    unittest.main()