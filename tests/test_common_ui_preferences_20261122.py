# -*- coding: utf-8 -*-

"""默认成果扩展名、完整复制及分用途导入目录持久化。"""

import os
import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.config import ConfigSaveManager, load_user_config
from core.constants import DEFAULT_CONFIG
from core.paths import build_output_path
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box


class OutputExtensionTests(unittest.TestCase):
    def test_missing_extension_and_existing_extension(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for requested, expected in (
                ("scan", "scan.txt"),
                ("merged.txt", "merged.txt"),
                ("merged.TXT", "merged.TXT"),
                ("archive.tar.gz", "archive.tar.gz"),
            ):
                with self.subTest(requested=requested):
                    self.assertEqual(
                        Path(build_output_path(root, requested)), root / expected,
                    )

    @unittest.skipUnless(os.name == "nt", "Windows名称校验")
    def test_invalid_trailing_dot_is_not_repaired_by_extension(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_output_path(directory, "invalid.")


class CommonUiPreferencesTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"没有可用Tk显示环境：{error}")
        self.root.withdraw()
        self.directory = TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(
            self.config, path=self.base / "config.json", needs_write=True,
        )
        self.commits = ConfigCommitController(self.root, self.manager)

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "directory"):
            self.directory.cleanup()

    def box(self, key, *, readonly=False, copy=False, imported=False):
        frame = tk.Frame(self.root)
        frame.pack()
        text = create_managed_text_box(
            frame, key, "正文\n\n", commits=self.commits,
            readonly=readonly, enable_favorites=False,
            enable_copy=copy, enable_import_text=imported,
            wrap_config_key=key, preserve_trailing_newlines=True,
            preserve_import_newlines=True,
        )
        return frame, text

    def button(self, frame, label):
        title = frame.winfo_children()[0]
        return next(
            child for child in title.winfo_children()
            if isinstance(child, tk.Button) and str(child.cget("text")) == label
        )

    def test_copy_all_preserves_body_selection_and_preferences(self):
        frame, text = self.box("restore.result_text", readonly=True, copy=True)
        text.tag_add("sel", "1.0", "1.1")
        before_selection = text.tag_ranges("sel")
        before = deepcopy(self.config)
        self.button(frame, "复制").invoke()
        self.assertEqual(self.root.clipboard_get(), "正文\n\n")
        self.assertEqual(text.get("1.0", "end-1c"), "正文\n\n")
        self.assertEqual(text.tag_ranges("sel"), before_selection)
        self.assertEqual(str(text.cget("state")), "disabled")
        self.assertEqual(self.config, before)
        title = frame.winfo_children()[0]
        packed = title.pack_slaves()
        clear = next(child for child in packed if isinstance(child, tk.Button) and child.cget("text") == "清空")
        self.assertLess(
            packed.index(clear), packed.index(text._copy_all_button),
        )
        self.assertEqual(
            text._copy_all_button.cget("width"), clear.cget("width"),
        )
        # 销毁时取消轻量反馈，不留下访问已销毁按钮的回调。
        frame.destroy()
        self.root.update_idletasks()

    def test_copy_is_opt_in(self):
        _, text = self.box("restore.source_text")
        self.assertFalse(hasattr(text, "_copy_all_button"))

    def test_import_directory_persists_and_cancel_does_not_replace_it(self):
        imported_directory = self.base / "imports"
        imported_directory.mkdir()
        source = imported_directory / "source.txt"
        source.write_bytes("中文\r\n尾\n\n".encode("utf-8"))
        frame, text = self.box("restore.source_text", imported=True)
        with patch(
            "ui.dialogs.filedialog.askopenfilename", return_value=str(source),
        ), patch("ui.dialogs.safe_show_info"):
            self.button(frame, "导入").invoke()
        self.assertEqual(text.get("1.0", "end-1c"), "中文\r\n尾\n\n")
        self.assertEqual(
            self.config["import_directories"]["restore.source_text"],
            str(imported_directory),
        )
        loaded, _ = load_user_config(self.manager.path)
        self.assertEqual(
            loaded["import_directories"]["restore.source_text"],
            str(imported_directory),
        )
        with patch(
            "ui.dialogs.filedialog.askopenfilename", return_value="",
        ) as selector:
            self.button(frame, "导入").invoke()
        self.assertEqual(selector.call_args.kwargs["initialdir"], str(imported_directory))
        self.assertEqual(text.get("1.0", "end-1c"), "中文\r\n尾\n\n")
        self.assertEqual(
            self.config["import_directories"]["restore.source_text"],
            str(imported_directory),
        )
        second_frame, _ = self.box("patch.patch_text", imported=True)
        with patch(
            "ui.dialogs.filedialog.askopenfilename", return_value="",
        ) as selector:
            self.button(second_frame, "导入").invoke()
        self.assertIsNone(selector.call_args.kwargs["initialdir"])
        self.assertNotIn("patch.patch_text", self.config["import_directories"])

    def test_failed_import_does_not_update_directory_or_text(self):
        frame, text = self.box("restore.source_text", imported=True)
        with patch(
            "ui.dialogs.filedialog.askopenfilename",
            return_value=str(self.base / "missing.txt"),
        ), patch("ui.dialogs.safe_show_error") as error:
            self.button(frame, "导入").invoke()
        error.assert_called_once()
        self.assertEqual(text.get("1.0", "end-1c"), "正文\n\n")
        self.assertEqual(self.config["import_directories"], {})


if __name__ == "__main__":
    unittest.main()