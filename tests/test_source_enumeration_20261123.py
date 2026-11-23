# -*- coding: utf-8 -*-

"""共享名称枚举、可靠身份、缓存漂移、扫描排除及调用次数回归。"""

import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.merged_format import parse_merge_blocks
from services.demand_rules import (
    prepare_demand_inspection, inspect_prepared_demand_rules,
)
from services.merge_service import merge_files
from services.scan_service import panoramic_scan
from services.source_enumeration import SourceEnumeration
from ui.source_scope import make_scan_selector


@unittest.skipUnless(os.name == "nt", "Windows真实来源准备")
class SourceEnumerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "a.py").write_bytes(b"python")
        (self.source / "b.txt").write_bytes(b"text")
        self.output = self.root / "merged.txt"
        self.links = []

    def tearDown(self):
        try:
            for path in reversed(self.links):
                if os.path.lexists(path):
                    os.rmdir(path)
        finally:
            self.temporary.cleanup()

    def test_multiple_rules_read_same_directory_once(self):
        cancel = Event()
        prepared = prepare_demand_inspection(
            ((1, "*.py"), (2, "*.txt"), (3, "*")),
            str(self.source), cancel,
        )
        shared = SourceEnumeration()
        results = inspect_prepared_demand_rules(
            prepared, cancel, source_enumeration=shared,
        )
        self.assertTrue(all(result.completed for result in results.values()))
        self.assertEqual(shared.directory_reads, 1)
        self.assertEqual(shared.entry_attribute_reads, 2)
        self.assertEqual(len(results[3].files), 2)

    def test_directory_replacement_invalidates_cache_even_with_same_names(self):
        shared = SourceEnumeration()
        shared.entries(self.source)
        saved = self.root / "saved-source"
        self.source.rename(saved)
        self.source.mkdir()
        (self.source / "a.py").write_bytes(b"python")
        (self.source / "b.txt").write_bytes(b"text")
        with self.assertRaisesRegex(ValueError, "身份或结构前提变化"):
            shared.entries(self.source)
        self.assertEqual(shared.directory_reads, 1)

    def test_new_member_invalidates_cache_without_second_enumeration(self):
        shared = SourceEnumeration()
        shared.entries(self.source)
        (self.source / "new.py").write_bytes(b"external")
        attributes = self.source.stat()
        os.utime(
            self.source,
            ns=(attributes.st_atime_ns, attributes.st_mtime_ns + 2_000_000_000),
        )
        with patch(
            "services.source_enumeration.os.scandir",
            side_effect=AssertionError("不得重新枚举接受新成员"),
        ):
            with self.assertRaises(ValueError):
                shared.entries(self.source)

    def test_combined_list_and_directory_share_one_name_enumeration(self):
        original = os.scandir
        entered = []

        def scandir(path):
            if Path(path) == self.source:
                entered.append(Path(path))
            return original(path)

        with patch("os.scandir", new=scandir):
            result = merge_files(
                "combined", str(self.source), "*.py\n*.txt",
                str(self.output), prepare_exclusions("", ""), "", "",
                CODE_HEADER_LINE, CODE_FOOTER_LINE,
                record_file_mtime=False,
            )
        self.assertEqual(entered, [self.source])
        self.assertEqual(result.total_files, 2)
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            blocks = parse_merge_blocks(stream.read())
        self.assertEqual(tuple(block.body for block in blocks), ("python", "text"))

    def test_attributes_are_reliable_path_values_not_direntry_zero_identity(self):
        shared = SourceEnumeration()
        members = shared.entries(self.source)
        for number, (name, _, _) in enumerate(members):
            observed = shared.attributes(members, number)
            actual = (self.source / name).lstat()
            self.assertEqual(
                (observed.st_dev, observed.st_ino),
                (actual.st_dev, actual.st_ino),
            )
            self.assertGreater(observed.st_ino, 0)
            self.assertIs(shared.attributes(members, number), observed)
        self.assertEqual(shared.entry_attribute_reads, 2)

    def test_scan_excluded_link_does_not_read_definition_and_plain_is_silent(self):
        actual = self.root / "actual"
        actual.mkdir()
        alias = self.source / "excluded"
        os.symlink(actual, alias, target_is_directory=True)
        self.links.append(alias)
        original_readlink = os.readlink

        def readlink(path, *arguments, **keywords):
            if Path(path) == alias:
                raise AssertionError("排除链接不得读取定义")
            return original_readlink(path, *arguments, **keywords)

        with patch("os.readlink", new=readlink), patch(
            "ui.source_scope.select_work_scope",
            side_effect=AssertionError("排除后纯普通范围不应弹窗"),
        ):
            result = panoramic_scan(
                self.source, self.root / "scan.txt",
                prepare_exclusions("excluded", ""), "", "",
                select_scan_scope=make_scan_selector(None, None),
            )
        self.assertEqual(result.file_count, 2)
        self.assertEqual(result.link_count, 0)

    def test_cancel_check_stops_before_directory_read(self):
        shared = SourceEnumeration()

        def cancelled():
            raise ValueError("cancelled")

        with patch(
            "services.source_enumeration.os.scandir",
            side_effect=AssertionError("取消不得枚举"),
        ):
            with self.assertRaisesRegex(ValueError, "cancelled"):
                shared.entries(self.source, check_cancel=cancelled)
        self.assertEqual(shared.directory_reads, 0)


if __name__ == "__main__":
    unittest.main()