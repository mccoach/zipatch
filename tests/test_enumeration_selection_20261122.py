# -*- coding: utf-8 -*-

"""首次实际枚举前集中选择、未选不进入、取消与固定范围变化。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.merged_format import parse_merge_blocks
from services.merge_service import merge_files
from services.scan_service import panoramic_scan


@unittest.skipUnless(os.name == "nt", "Windows实际目录选择")
class EnumerationSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.first = self.root / "first"
        self.second = self.root / "second"
        self.first.mkdir()
        self.second.mkdir()
        (self.first / "a.txt").write_bytes(b"first")
        (self.second / "b.txt").write_bytes(b"second")
        self.one = self.source / "one"
        self.two = self.source / "two"
        os.symlink(self.first, self.one, target_is_directory=True)
        os.symlink(self.second, self.two, target_is_directory=True)
        self.output = self.root / "output.txt"

    def tearDown(self):
        try:
            for path in (self.one, self.two):
                if os.path.lexists(path):
                    os.rmdir(path)
        finally:
            self.temporary.cleanup()

    def merge(self, select, *, mode="list", source=None, text=None):
        return merge_files(
            mode, str(source or self.source),
            "one/*.txt\ntwo/*.txt" if text is None else text,
            str(self.output), prepare_exclusions("", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
            select_merge_enumeration=select,
            select_merge_scope=lambda prepared, accesses, output:
            tuple("authorize" for _ in prepared),
        )

    def blocks(self):
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            return parse_merge_blocks(stream.read())

    def test_all_linked_prefixes_displayed_before_first_enumeration(self):
        original = os.scandir
        entered = []
        approved = False

        def choose(accesses):
            nonlocal approved
            self.assertEqual(entered, [])
            self.assertEqual(
                tuple(access.actual_path for access in accesses),
                (self.first, self.second),
            )
            approved = True
            return ("authorize", "skip")

        def scandir(path):
            path = Path(path)
            if path == self.second:
                raise AssertionError("未选实际目录不得进入")
            if path == self.first:
                self.assertTrue(approved)
                entered.append(path)
            return original(path)

        with patch("os.scandir", new=scandir):
            result = self.merge(choose)
        self.assertEqual(entered, [self.first])
        self.assertEqual(result.total_files, 1)
        self.assertEqual(self.blocks()[0].body, "first")

    def test_cancel_before_any_linked_directory_is_entered(self):
        original = os.scandir

        def scandir(path):
            if Path(path) in (self.first, self.second):
                raise AssertionError("取消不得进入链接实际目录")
            return original(path)

        with patch("os.scandir", new=scandir):
            with self.assertRaisesRegex(ValueError, "已取消"):
                self.merge(lambda accesses: None)
        self.assertFalse(self.output.exists())

    def test_skip_all_prefixes_produces_empty_container_without_enumeration(self):
        original = os.scandir

        def scandir(path):
            if Path(path) in (self.first, self.second):
                raise AssertionError("全不选不得进入链接实际目录")
            return original(path)

        with patch("os.scandir", new=scandir):
            result = self.merge(lambda accesses: ("skip", "skip"))
        self.assertEqual(result.total_files, 0)
        self.assertEqual(self.blocks(), [])

    def test_combined_same_root_and_list_prefix_are_one_choice(self):
        calls = []

        def choose(accesses):
            calls.append(accesses)
            self.assertEqual(len(accesses), 1)
            self.assertEqual(accesses[0].nominal_path, self.one)
            return ("authorize",)

        result = self.merge(
            choose, mode="combined", source=self.one, text="*.txt",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.total_files, 1)
        self.assertEqual(self.blocks()[0].body, "first")

    def test_combined_skipped_root_and_list_never_enter_target(self):
        original = os.scandir

        def scandir(path):
            if Path(path) == self.first:
                raise AssertionError("目录和列表共用未选范围，不得枚举")
            return original(path)

        with patch("os.scandir", new=scandir):
            result = self.merge(
                lambda accesses: ("skip",),
                mode="combined", source=self.one, text="*.txt",
            )
        self.assertEqual(result.total_files, 0)

    def test_invalid_rule_cannot_be_hidden_by_prefix_selection(self):
        with self.assertRaisesRegex(ValueError, "检查未通过"):
            self.merge(
                lambda accesses: ("skip",),
                text="one/*.txt\nbad**name",
            )
        self.assertFalse(self.output.exists())

    def test_selected_prefix_retarget_stops_before_enumeration(self):
        original = os.scandir

        def choose(accesses):
            os.rmdir(self.one)
            os.symlink(self.second, self.one, target_is_directory=True)
            return ("authorize", "skip")

        def scandir(path):
            if Path(path) in (self.first, self.second):
                raise AssertionError("改指向后不得进入原目标或新目标")
            return original(path)

        with patch("os.scandir", new=scandir):
            with self.assertRaises(ValueError):
                self.merge(choose)
        self.assertFalse(self.output.exists())

    def test_unselected_changed_prefix_is_not_later_dependency(self):
        def choose(accesses):
            os.rmdir(self.two)
            os.symlink(self.first, self.two, target_is_directory=True)
            return ("authorize", "skip")

        result = self.merge(choose)
        self.assertEqual(result.total_files, 1)
        self.assertEqual(self.blocks()[0].body, "first")

    def test_scan_skipped_linked_root_publishes_empty_without_entry(self):
        original = os.scandir

        def scandir(path):
            if Path(path) == self.first:
                raise AssertionError("未选扫描根不得进入")
            return original(path)

        with patch("os.scandir", new=scandir):
            result = panoramic_scan(
                self.one, self.output, prepare_exclusions("", ""), "", "",
                select_scan_enumeration=lambda accesses: ("skip",),
            )
        self.assertEqual((result.file_count, result.dir_count, result.link_count), (0, 0, 0))
        self.assertIn("未进入链接目标目录", self.output.read_text(encoding="utf-8"))

    def test_scan_cancelled_root_selection_does_not_publish(self):
        with self.assertRaisesRegex(ValueError, "已取消"):
            panoramic_scan(
                self.one, self.output, prepare_exclusions("", ""), "", "",
                select_scan_enumeration=lambda accesses: None,
            )
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()