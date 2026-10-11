# -*- coding: utf-8 -*-

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG, THEME
from ui.demand_editor import DEMAND_HIGHLIGHT_STYLES


class DemandLegendGuiTests(unittest.TestCase):
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

    def test_legend_has_five_blocks_and_shares_text_highlight_colors(self):
        original_disk = self.path.read_bytes()
        before = dict(self.app.config_manager.metrics)
        self.session = self.app.current_panel.open_demand_merge_dialog()
        self.root.update()

        legend = next(
            child for child in self.session.dialog.winfo_children()
            if isinstance(child, tk.Frame)
            and any(
                isinstance(label, tk.Label)
                and label.cget("text") == "色块图例："
                for label in child.winfo_children()
            )
        )
        labels = [
            child for child in legend.winfo_children()
            if isinstance(child, tk.Label)
        ]
        blocks = [label for label in labels if label.cget("text") == "  "]
        expected_colors = [
            background for _, _, background, _ in DEMAND_HIGHLIGHT_STYLES
        ] + [THEME["bg_input"]]
        self.assertEqual(
            [block.cget("background") for block in blocks], expected_colors,
        )
        self.assertTrue(all(
            str(block.cget("relief")) == "solid"
            and int(block.cget("borderwidth")) == 1
            for block in blocks
        ))
        descriptions = {label.cget("text") for label in labels}
        for tag, foreground, background, description in DEMAND_HIGHLIGHT_STYLES:
            self.assertIn(description, descriptions)
            self.assertEqual(
                self.session.rule_text.tag_cget(tag, "background"), background,
            )
            self.assertEqual(
                self.session.rule_text.tag_cget(tag, "foreground"), foreground,
            )
        self.assertIn("无高亮：待检查或重复关系未确定", descriptions)
        self.assertEqual(self.app.config_manager.metrics, before)
        self.assertEqual(self.path.read_bytes(), original_disk)


if __name__ == "__main__":
    unittest.main()