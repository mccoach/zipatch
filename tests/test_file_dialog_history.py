# -*- coding: utf-8 -*-

"""选择器返回值模拟；正文、撤销、配置保存及重载使用真实实现。"""

import tempfile
import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core.config import (
    ConfigSaveManager, load_user_config, remove_retired_output_config,
)
from core.constants import DEFAULT_CONFIG
from core.paths import get_app_dir
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.file_dialog_history import file_dialog_initial_directory
from ui.list_text_editing import ListTextEditing
from ui.widgets import browse_folder


class HistoryMigrationTests(unittest.TestCase):
    def test_scoped_imports_migrate_without_creating_browse_records(self):
        source = {
            "import_directories": {
                "merge.directory_list": "E:\\lists",
                "merge.demand_file_list": "E:\\files",
            },
        }
        result = remove_retired_output_config(source)
        self.assertNotIn("import_directories", result)
        self.assertEqual(result["file_dialog_directories"], {
            "text.merge.directory_list.import": "E:\\lists",
            "text.merge.demand_file_list.import": "E:\\files",
        })
        self.assertIn("import_directories", source)
        self.assertEqual(remove_retired_output_config(result), result)


class FileDialogHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ZipatchDialogHistory_")
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(
            self.config, path=self.folder / "config.json", needs_write=True,
        )
        self.commits = ConfigCommitController(self.root, self.manager)
        self.notifications = []
        self.left = self.make_text(
            "directory_list_text", "merge.directory_list", "directory",
        )
        self.right = self.make_text(
            "demand_file_list_text", "merge.demand_file_list", "file",
        )

    def tearDown(self):
        self.root.destroy()
        self.temporary.cleanup()

    def make_text(self, field, key, kind):
        area = tk.Frame(self.root)
        area.pack(fill="both", expand=True)
        text = create_managed_text_box(
            area, field, "", commits=self.commits,
            config=self.config["merge"], field=field, page="merge",
            wrap_config_key=key, browse_kind=kind,
            enable_import_text=True, enable_favorites=False,
            enable_copy=True, preserve_trailing_newlines=True,
        )
        ListTextEditing(text, self.commits, self.notifications.append)
        return text

    def test_append_at_end_preserves_content_and_can_undo_as_one_action(self):
        directory = self.folder / "chosen"
        directory.mkdir()
        self.left.insert("1.0", "|old\nlast")
        self.left._list_text_editing.complete()
        self.left.edit_reset()
        self.left.mark_set("insert", "1.0")
        with patch(
            "ui.dialogs.filedialog.askdirectory", return_value=str(directory),
        ):
            self.left._browse_button.invoke()
        expected = "|old\nlast\n" + str(directory).replace("/", "\\") + "\n"
        self.assertEqual(self.left.get("1.0", "end-1c"), expected)
        self.assertEqual(self.config["merge"]["directory_list_text"], expected)
        self.assertEqual(self.notifications[-1], expected)
        self.left._text_editor_controller.undo()
        self.assertEqual(self.left.get("1.0", "end-1c"), "|old\nlast")
        self.assertEqual(self.config["merge"]["directory_list_text"], "|old\nlast")

    def test_each_text_and_operation_has_independent_persistent_directory(self):
        directories = [self.folder / name for name in ("browse_a", "browse_b", "imports")]
        for directory in directories:
            directory.mkdir()
        business_file = directories[1] / "business.py"
        business_file.write_bytes(b"business")
        imported = directories[2] / "list.txt"
        imported.write_bytes(b"|imported list")

        with patch(
            "ui.dialogs.filedialog.askdirectory", return_value=str(directories[0]),
        ):
            self.left._browse_button.invoke()
        with patch(
            "ui.dialogs.filedialog.askopenfilenames",
            return_value=(str(business_file),),
        ):
            self.right._browse_button.invoke()
        with (
            patch("ui.dialogs.filedialog.askopenfilename", return_value=str(imported)),
            patch("ui.dialogs.safe_show_info"),
        ):
            self.left._import_button.invoke()

        stored, _ = load_user_config(self.manager.path)
        self.assertEqual(stored["file_dialog_directories"], {
            "text.merge.directory_list.browse": str(directories[0]),
            "text.merge.demand_file_list.browse": str(directories[1]),
            "text.merge.directory_list.import": str(directories[2]),
        })
        for text, dialog_name, key in (
            (self.left, "askdirectory", "text.merge.directory_list.browse"),
            (self.right, "askopenfilenames", "text.merge.demand_file_list.browse"),
            (self.left, "askopenfilename", "text.merge.directory_list.import"),
            (self.right, "askopenfilename", "text.merge.demand_file_list.import"),
        ):
            with patch(
                "ui.dialogs.filedialog." + dialog_name,
                return_value=() if dialog_name == "askopenfilenames" else "",
            ) as selector:
                (
                    text._browse_button
                    if dialog_name != "askopenfilename" else text._import_button
                ).invoke()
            self.assertEqual(
                selector.call_args.kwargs["initialdir"],
                stored["file_dialog_directories"].get(key, str(get_app_dir())),
            )

    def test_multiselect_cancel_and_import_failure(self):
        first = self.folder / "a.txt"
        second = self.folder / "b.txt"
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        with patch(
            "ui.dialogs.filedialog.askopenfilenames",
            return_value=(str(first), str(second)),
        ):
            self.right._browse_button.invoke()
        original = self.right.get("1.0", "end-1c")
        self.assertEqual(
            original,
            str(first).replace("/", "\\") + "\n"
            + str(second).replace("/", "\\") + "\n",
        )
        histories = dict(self.config["file_dialog_directories"])
        with patch("ui.dialogs.filedialog.askopenfilenames", return_value=()):
            self.right._browse_button.invoke()
        with (
            patch(
                "ui.dialogs.filedialog.askopenfilename",
                return_value=str(self.folder / "missing.txt"),
            ),
            patch("ui.dialogs.safe_show_error") as error,
        ):
            self.right._import_button.invoke()
        error.assert_called_once()
        self.assertEqual(self.right.get("1.0", "end-1c"), original)
        self.assertEqual(self.config["file_dialog_directories"], histories)

    def test_stale_directory_falls_back_without_borrowing_other_history(self):
        self.manager.accept(self.config["file_dialog_directories"], {
            "text.merge.directory_list.browse": str(self.folder / "missing"),
            "text.merge.demand_file_list.browse": str(self.folder),
        })
        self.assertEqual(
            file_dialog_initial_directory(
                self.commits, "text.merge.directory_list.browse",
            ),
            str(get_app_dir()),
        )

    def test_toolbar_order_and_browser_is_opt_in(self):
        self.assertEqual(
            [widget.cget("text") for widget in self.left._title_tools.pack_slaves()],
            ["自动换行", "浏览", "导入", "复制", "清空"],
        )
        area = tk.Frame(self.root)
        area.pack()
        ordinary = create_managed_text_box(
            area, "ordinary", "", commits=self.commits, enable_favorites=False,
        )
        self.assertFalse(hasattr(ordinary, "_browse_button"))

    def test_single_line_browse_has_its_own_record(self):
        directory = self.folder / "entry_choice"
        directory.mkdir()
        variable = tk.StringVar(master=self.root)
        with patch(
            "ui.widgets.filedialog.askdirectory", return_value=str(directory),
        ):
            selected = browse_folder(
                variable, parent=self.root, commits=self.commits,
                history_key="scan.source_folder",
            )
        self.assertEqual(selected, str(directory))
        self.commits.finish()
        with patch(
            "ui.widgets.filedialog.askdirectory", return_value="",
        ) as selector:
            browse_folder(
                variable, parent=self.root, commits=self.commits,
                history_key="scan.source_folder",
            )
        self.assertEqual(selector.call_args.kwargs["initialdir"], str(directory))
        self.assertNotIn(
            "entry.scan.output_folder.browse",
            self.config["file_dialog_directories"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)