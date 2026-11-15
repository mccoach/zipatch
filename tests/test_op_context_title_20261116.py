# -*- coding: utf-8 -*-

"""当前光标／选区契约及真实Tk标题栏集成，不写用户配置或业务文件。"""

import inspect
import tkinter as tk
import unittest
from copy import deepcopy

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from services.patch_parser import parse_patch_v3
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.patch_op_selection import (
    PatchOpSelection, _source_index, locate_op_selection,
)


BOUNDARY = "ZIPATCH_BOUNDARY_TEST_20261116033200_A19C7E50D234"
SOURCE = (
    f'<<ZIPATCH_V3 boundary="{BOUNDARY}">>\n'
    '---OP write_file id="first" if_exists="overwrite"\n'
    "---PATH\n"
    "first.txt\n"
    f"{BOUNDARY}\n"
    "---CONTENT\n"
    "😀正文\n"
    '---OP delete_file id="not_structure"\n'
    f"{BOUNDARY}\n"
    "---END_OP\n"
    "\n"
    '---OP write_file id="second" if_exists="overwrite"\n'
    "---PATH\n"
    "second.txt\n"
    f"{BOUNDARY}\n"
    "---CONTENT\n"
    "第二个正文\n"
    f"{BOUNDARY}\n"
    "---END_OP\n"
    "<</ZIPATCH_V3>>\n"
)


def op_ranges():
    return parse_patch_v3(SOURCE)["op_ranges"]


class ContextLocationTests(unittest.TestCase):
    def test_cursor_selects_whole_op(self):
        _, left, right = op_ranges()[0]
        position = SOURCE.index("😀正文")
        self.assertEqual(
            locate_op_selection(SOURCE, start=position), (left, right),
        )

    def test_partial_selection_expands_to_whole_op(self):
        _, left, right = op_ranges()[0]
        position = SOURCE.index("😀正文")
        self.assertEqual(
            locate_op_selection(SOURCE, start=position, end=position + 2),
            (left, right),
        )

    def test_selection_can_cover_multiple_ops(self):
        ranges = op_ranges()
        self.assertEqual(
            locate_op_selection(
                SOURCE,
                start=SOURCE.index("😀正文"),
                end=SOURCE.index("第二个正文") + 2,
            ),
            (ranges[0][1], ranges[1][2]),
        )

    def test_selection_ending_at_next_op_excludes_it(self):
        ranges = op_ranges()
        self.assertEqual(
            locate_op_selection(
                SOURCE, start=ranges[0][1], end=ranges[1][1],
            ),
            (ranges[0][1], ranges[0][2]),
        )

    def test_fake_op_in_body_is_not_an_independent_op(self):
        ranges = op_ranges()
        self.assertEqual(len(ranges), 2)
        position = SOURCE.index('---OP delete_file id="not_structure"')
        self.assertEqual(
            locate_op_selection(SOURCE, start=position),
            (ranges[0][1], ranges[0][2]),
        )

    def test_header_and_footer_do_not_guess_nearest_op(self):
        for position in (0, SOURCE.index("<</ZIPATCH_V3>>")):
            with self.subTest(position=position):
                with self.assertRaisesRegex(ValueError, "没有 OP"):
                    locate_op_selection(SOURCE, start=position)

    def test_invalid_package_does_not_return_partial_range(self):
        with self.assertRaises(ValueError):
            locate_op_selection(
                SOURCE.replace("<</ZIPATCH_V3>>", ""),
                start=SOURCE.index("😀正文"),
            )

    def test_invalid_positions_are_rejected(self):
        for start, end in ((-1, 0), (2, 1), (0, len(SOURCE) + 1)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    locate_op_selection(SOURCE, start=start, end=end)

    def test_id_selection_parameter_has_been_removed(self):
        self.assertEqual(
            tuple(inspect.signature(locate_op_selection).parameters),
            ("source", "start", "end"),
        )


class ContextTitleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("1000x600")
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(self.config)
        self.commits = ConfigCommitController(self.root, self.manager)
        self.host = tk.Frame(self.root)
        self.host.pack(fill="both", expand=True)
        self.text = create_managed_text_box(
            self.host, "修改包源码", SOURCE,
            height=12, commits=self.commits,
            enable_favorites=False,
            preserve_trailing_newlines=True,
            show_statistics=True,
            title_action_factory=lambda text, title_row: PatchOpSelection(
                text, title_row, self.commits,
            ),
        )
        self.selector = self.text._patch_op_selection
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def selected_text(self):
        ranges = self.text.tag_ranges("sel")
        return "" if not ranges else self.text.get(ranges[0], ranges[1])

    def test_single_title_button_is_left_of_clear(self):
        button = self.selector.button
        title_row = button.master
        clear = next(
            child for child in title_row.winfo_children()
            if isinstance(child, tk.Button) and child.cget("text") == "清空"
        )
        self.assertEqual(button.cget("text"), "选中OP")
        self.assertEqual(button.winfo_manager(), "pack")
        self.assertLessEqual(
            button.winfo_x() + button.winfo_width(), clear.winfo_x(),
        )
        self.assertFalse(hasattr(self.selector, "op_id"))
        self.assertEqual(
            self.text._text_editor_controller.search_bar.grid_slaves(row=1),
            [],
        )
        self.assertIsNotNone(self.text._text_statistics)

    def test_cursor_button_preserves_text_and_does_not_save(self):
        position = SOURCE.index("😀正文")
        self.text.mark_set("insert", _source_index(self.text, SOURCE, position))
        snapshot = deepcopy(self.config)
        saves = self.manager.metrics["save_requests"]
        self.selector.button.invoke()
        _, left, right = op_ranges()[0]
        self.assertEqual(self.selected_text(), SOURCE[left:right])
        self.assertEqual(self.text.get("1.0", "end-1c"), SOURCE)
        self.assertEqual(self.config, snapshot)
        self.assertEqual(self.manager.metrics["save_requests"], saves)

    def test_partial_unicode_selection_expands(self):
        position = SOURCE.index("😀正文")
        self.text.tag_add(
            "sel",
            _source_index(self.text, SOURCE, position),
            _source_index(self.text, SOURCE, position + 2),
        )
        self.selector.button.invoke()
        _, left, right = op_ranges()[0]
        self.assertEqual(self.selected_text(), SOURCE[left:right])

    def test_existing_find_then_title_button_selects_op(self):
        editor = self.text._text_editor_controller
        self.text.mark_set("insert", "1.0")
        editor.find_var.set('id="second"')
        editor.find_next()
        self.selector.button.invoke()
        _, left, right = op_ranges()[1]
        self.assertEqual(self.selected_text(), SOURCE[left:right])

    def test_busy_or_disabled_source_does_not_select(self):
        with self.commits.operation():
            self.selector.button.invoke()
        self.assertEqual(self.selected_text(), "")
        self.text.configure(state="disabled")
        self.selector.button.invoke()
        self.assertEqual(self.selected_text(), "")

    def test_other_text_boxes_do_not_install_op_action(self):
        host = tk.Frame(self.root)
        host.pack(fill="both", expand=True)
        ordinary = create_managed_text_box(
            host, "普通正文", "普通文本",
            height=2, commits=self.commits, enable_favorites=False,
        )
        self.assertFalse(hasattr(ordinary, "_patch_op_selection"))


if __name__ == "__main__":
    unittest.main()
