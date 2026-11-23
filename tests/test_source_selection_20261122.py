# -*- coding: utf-8 -*-

"""扫描和三种合并所选来源：正文边界、依赖裁剪和取消。"""

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


@unittest.skipUnless(os.name == "nt", "正式链接来源规划使用Windows")
class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.first = self.source / "a"
        self.second = self.source / "b"
        self.first.write_bytes(b"first")
        self.second.write_bytes(b"second")
        self.output = self.root / "output.txt"
        self.links = []

    def tearDown(self):
        try:
            for link in self.links:
                if os.path.lexists(link):
                    os.unlink(link)
        finally:
            self.temporary.cleanup()

    def merge(self, mode, selector, *, text="a\nb"):
        return merge_files(
            mode, str(self.source), text, str(self.output),
            prepare_exclusions("", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False, select_merge_scope=selector,
        )

    def blocks(self):
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            return parse_merge_blocks(stream.read())

    def test_three_modes_do_not_acquire_unselected_body(self):
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == self.second:
                raise AssertionError("未选合并来源不得读取正文")
            return original_open(path, *arguments, **keywords)

        def select(prepared, accesses, output):
            self.assertEqual(len(prepared), 2)
            return ("authorize", "skip")

        with patch("pathlib.Path.open", new=opened):
            for mode in ("directory", "list", "combined"):
                with self.subTest(mode=mode):
                    result = self.merge(mode, select)
                    self.assertEqual(result.total_files, 1)
                    blocks = self.blocks()
                    self.assertEqual(len(blocks), 1)
                    self.assertEqual(blocks[0].body, "first")
        self.assertEqual(self.second.read_bytes(), b"second")

    def test_unselected_link_changes_do_not_remain_publication_dependency(self):
        alias = self.source / "alias"
        os.symlink("missing-first", alias)
        self.links.append(alias)

        def select(prepared, accesses, output):
            result = tuple(
                "skip" if access.entry_link is not None else "authorize"
                for entry, access in prepared
            )
            os.unlink(alias)
            os.symlink("missing-second", alias)
            return result

        result = self.merge("directory", select)
        self.assertEqual(result.total_files, 2)
        self.assertTrue(all(block.kind == "file" for block in self.blocks()))
        self.assertEqual(os.readlink(alias), "missing-second")

    def test_merge_cancelled_selection_reads_no_body_or_publishes(self):
        with patch(
            "services.merge_service.read_text_content_for_merge",
            side_effect=AssertionError("取消不得获取普通正文"),
        ):
            with self.assertRaisesRegex(ValueError, "已取消"):
                self.merge("combined", lambda prepared, accesses, output: None)
        self.assertFalse(self.output.exists())

    def test_scan_only_outputs_selected_file(self):
        def select(scope, members, output):
            self.assertEqual(len(members), 2)
            return ("authorize", "skip")

        result = panoramic_scan(
            self.source, self.output, prepare_exclusions("", ""),
            "", "", select_scan_scope=select,
        )
        self.assertEqual(result.file_count, 1)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn(str(self.first), text)
        self.assertNotIn(str(self.second), text)

    def test_scan_unselected_link_dependency_is_pruned(self):
        alias = self.source / "alias"
        os.symlink("missing-first", alias)
        self.links.append(alias)

        def select(scope, members, output):
            decision = tuple(
                "skip" if hasattr(member, "entry_link") else "authorize"
                for member in members
            )
            os.unlink(alias)
            os.symlink("missing-second", alias)
            return decision

        result = panoramic_scan(
            self.source, self.output, prepare_exclusions("", ""),
            "", "", select_scan_scope=select,
        )
        self.assertEqual(result.link_count, 0)
        self.assertNotIn(str(alias), self.output.read_text(encoding="utf-8"))
        self.assertEqual(os.readlink(alias), "missing-second")

    def test_scan_cancelled_selection_publishes_nothing(self):
        with self.assertRaisesRegex(ValueError, "已取消"):
            panoramic_scan(
                self.source, self.output, prepare_exclusions("", ""),
                "", "", select_scan_scope=lambda scope, members, output: None,
            )
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()