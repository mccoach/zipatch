# -*- coding: utf-8 -*-

"""正式组合排序、懒属性父链拒绝及直接界面模块语法回归。"""

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.merged_format import parse_merge_blocks
from services.merge_service import merge_files
from services.source_enumeration import SourceEnumeration


class OutputSettingsSyntaxTests(unittest.TestCase):
    def test_direct_output_settings_module_has_valid_syntax(self):
        project = Path(__file__).absolute().parents[1]
        source = project / "ui" / "text_output_settings.py"
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


@unittest.skipUnless(os.name == "nt", "Windows正式组合来源")
class EnumerationRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "merged.txt"
        self.links = []

    def tearDown(self):
        try:
            for path in reversed(self.links):
                if os.path.lexists(path):
                    os.rmdir(path)
        finally:
            self.temporary.cleanup()

    def merge_paths(self, source, text, *, directories="", files=""):
        merge_files(
            "combined", str(source), text, str(self.output),
            prepare_exclusions(directories, files), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
            authorize_merge_scope=lambda title, description: True,
        )
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            return tuple(
                block.path for block in parse_merge_blocks(stream.read())
                if block.kind == "file"
            )

    def test_plain_root_excluded_file_returns_to_directory_order(self):
        first = self.source / "a.txt"
        second = self.source / "z.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.assertEqual(
            self.merge_paths(self.source, "a.txt", files="a.txt"),
            (str(first), str(second)),
        )

    def test_linked_root_excluded_file_returns_to_directory_order(self):
        first = self.source / "a.txt"
        second = self.source / "z.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        alias = self.root / "alias"
        os.symlink(self.source, alias, target_is_directory=True)
        self.links.append(alias)
        self.assertEqual(
            self.merge_paths(alias, "a.txt", files="a.txt"),
            (str(alias / "a.txt"), str(alias / "z.txt")),
        )

    def test_excluded_directory_supplement_keeps_list_order_after_directory(self):
        ordinary = self.source / "z.txt"
        ordinary.write_bytes(b"ordinary")
        blocked = self.source / "blocked"
        blocked.mkdir()
        first = blocked / "a.txt"
        second = blocked / "b.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.assertEqual(
            self.merge_paths(
                self.source, "blocked/b.txt\nblocked/a.txt",
                directories="blocked",
            ),
            (str(ordinary), str(second), str(first)),
        )

    def test_external_supplements_keep_list_order(self):
        ordinary = self.source / "ordinary.txt"
        ordinary.write_bytes(b"ordinary")
        first = self.root / "a.txt"
        second = self.root / "b.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.assertEqual(
            self.merge_paths(self.source, str(second) + "\n" + str(first)),
            (str(ordinary), str(second), str(first)),
        )

    def test_lazy_attributes_refuse_replaced_link_parent_before_child_query(self):
        shared = SourceEnumeration()
        members = shared.entries(self.source)
        # 创建素材后重新建立本次名称快照，避免测试自身污染前提。
        child = self.source / "body.txt"
        child.write_bytes(b"original")
        shared = SourceEnumeration()
        members = shared.entries(self.source)
        number = next(
            number for number, member in enumerate(members)
            if member[0] == "body.txt"
        )
        saved = self.root / "saved-source"
        self.source.rename(saved)
        outside = self.root / "outside"
        outside.mkdir()
        external = outside / "body.txt"
        external.write_bytes(b"external")
        os.symlink(outside, self.source, target_is_directory=True)
        self.links.append(self.source)
        original_lstat = Path.lstat

        def lstat(path, *arguments, **keywords):
            if path == child:
                raise AssertionError("链接父链拒绝应先于子项属性查询")
            return original_lstat(path, *arguments, **keywords)

        with patch("pathlib.Path.lstat", new=lstat):
            with self.assertRaises(ValueError):
                shared.attributes(members, number)
        self.assertEqual(shared.entry_attribute_reads, 0)
        self.assertEqual(external.read_bytes(), b"external")
        self.assertEqual((saved / "body.txt").read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()