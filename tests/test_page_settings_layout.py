# -*- coding: utf-8 -*-

"""共同控件位置及设置按钮关联顺序；不启动任何业务任务。"""

import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from ui.config_editing import ConfigCommitController
from ui.page_settings import create_page_settings


class PageSettingsLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="ZipatchPageSettings_")
        self.root = tk.Tk()
        self.root.withdraw()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.commits = ConfigCommitController(
            self.root,
            ConfigSaveManager(
                self.config, path=Path(self.temporary.name) / "config.json",
                needs_write=True,
            ),
        )

    def tearDown(self):
        self.root.destroy()
        self.temporary.cleanup()

    def test_common_columns_do_not_shift_when_middle_option_is_missing(self):
        rows = []
        for page, fields in (
            ("merge", ("open_after_done", "normalize_text_newlines", "show_actual_path")),
            ("scan", ("open_after_done", "show_actual_path")),
            ("patch", ("open_backup_after_done", "normalize_text_newlines")),
        ):
            row, _, variables = create_page_settings(
                self.root, self.commits, self.config[page],
                tuple((field, field, None) for field in fields),
            )
            checks = row.grid_slaves(row=0, column=0)[0]
            columns = {
                str(widget.cget("variable")): int(widget.grid_info()["column"])
                for widget in checks.winfo_children()
            }
            rows.append({
                field: columns[str(variable)]
                for field, variable in variables.items()
            })
        self.assertEqual(rows[0], {
            "open_after_done": 0,
            "normalize_text_newlines": 1,
            "show_actual_path": 2,
        })
        self.assertEqual(rows[1]["show_actual_path"], 2)
        self.assertEqual(rows[2]["normalize_text_newlines"], 1)
        self.assertEqual(rows[2]["open_backup_after_done"], 0)

    def test_newline_is_right_anchored_after_business_settings(self):
        row, _, _ = create_page_settings(
            self.root, self.commits, self.config["merge"], (),
            (
                ("newline", "换行设置", lambda: None),
                ("exclude", "排除名单", lambda: None),
                ("extra", "附加文本", lambda: None),
            ),
        )
        buttons = row.grid_slaves(row=0, column=2)[0]
        self.assertEqual(
            [widget._page_setting_key for widget in buttons.pack_slaves()],
            ["exclude", "extra", "newline"],
        )
        self.assertEqual(buttons.grid_info()["sticky"], "e")

    def test_specific_options_do_not_occupy_common_columns(self):
        row, specific, variables = create_page_settings(
            self.root, self.commits, self.config["merge"],
            (
                ("record_file_mtime", "记录修改时间", None),
                ("open_after_done", "完成后打开", None),
                ("force_overwrite", "成果强制覆盖", None),
            ),
        )
        self.assertEqual(len(specific.pack_slaves()), 2)
        checks = row.grid_slaves(row=0, column=0)[0]
        self.assertEqual(len(checks.winfo_children()), 1)
        self.assertEqual(set(variables), {
            "record_file_mtime", "open_after_done", "force_overwrite",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)