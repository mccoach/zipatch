# -*- coding: utf-8 -*-

"""目标元信息、拒绝授权、无正文访问及固定目标漂移回归。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from services.scan_service import panoramic_scan
from services.merge_service import merge_files


@unittest.skipUnless(os.name == "nt", "Windows链接目标元信息")
class ScanTargetMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.actual = self.root / "actual"
        self.actual.mkdir()
        self.body = self.actual / "body"
        self.body.write_bytes(b"1234567")
        self.alias = self.source / "alias"
        os.symlink(self.body, self.alias)
        self.output = self.root / "scan.txt"

    def tearDown(self):
        try:
            if os.path.lexists(self.alias):
                os.unlink(self.alias)
        finally:
            self.temporary.cleanup()

    def scan(self, authorize):
        return panoramic_scan(
            self.source, self.output, prepare_exclusions("", ""), "", "",
            include_size=True, include_date=True,
            authorize_scan_scope=authorize,
        )

    def test_file_metadata_uses_actual_size_without_opening_body(self):
        original_open = Path.open
        original_scandir = os.scandir
        calls = []

        def opened(path, *arguments, **keywords):
            if path == self.body:
                raise AssertionError("元信息扫描不得打开目标正文")
            return original_open(path, *arguments, **keywords)

        def scandir(path):
            if Path(path) == self.actual:
                raise AssertionError("元信息扫描不得枚举链接目标目录")
            return original_scandir(path)

        def authorize(title, description):
            calls.append(title)
            return True

        with patch("pathlib.Path.open", new=opened), patch("os.scandir", new=scandir):
            result = self.scan(authorize)
        self.assertEqual(result.file_symlink_count, 1)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("大小=7 bytes", text)
        self.assertIn(str(self.body), text)
        self.assertEqual(calls[-1], "扫描链接目标大小与日期授权")
        self.assertEqual(self.body.read_bytes(), b"1234567")

    def test_cancel_target_metadata_does_not_publish(self):
        with self.assertRaisesRegex(ValueError, "取消"):
            self.scan(
                lambda title, description:
                title != "扫描链接目标大小与日期授权"
            )
        self.assertFalse(self.output.exists())
        self.assertEqual(self.body.read_bytes(), b"1234567")

    def test_retarget_during_metadata_authorization_stops(self):
        other = self.actual / "other"
        other.write_bytes(b"other")

        def authorize(title, description):
            if title == "扫描链接目标大小与日期授权":
                os.unlink(self.alias)
                os.symlink(other, self.alias)
            return True

        with self.assertRaises(ValueError):
            self.scan(authorize)
        self.assertFalse(self.output.exists())
        self.assertEqual(other.read_bytes(), b"other")

    def test_directory_metadata_does_not_sum_or_enumerate_contents(self):
        os.unlink(self.alias)
        os.symlink(self.actual, self.alias, target_is_directory=True)
        original_scandir = os.scandir

        def scandir(path):
            if Path(path) == self.actual:
                raise AssertionError("目录目标大小不得隐式枚举求和")
            return original_scandir(path)

        try:
            with patch("os.scandir", new=scandir):
                result = self.scan(lambda title, description: True)
            self.assertEqual(result.directory_symlink_count, 1)
            text = self.output.read_text(encoding="utf-8")
            self.assertIn("大小不累计：目标为目录", text)
            self.assertIn("日期=", text)
        finally:
            if os.path.lexists(self.alias):
                os.rmdir(self.alias)

    def test_merge_result_counts_actual_selected_block_types(self):
        result = merge_files(
            "directory", str(self.source), "", str(self.output),
            prepare_exclusions("", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
            authorize_merge_scope=lambda title, description: True,
        )
        self.assertEqual(result.total_files, 1)
        self.assertEqual(dict(result.entry_counts)["file_symlink"], 1)
        self.assertEqual(dict(result.entry_counts)["file"], 0)


if __name__ == "__main__":
    unittest.main()