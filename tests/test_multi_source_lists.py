# -*- coding: utf-8 -*-

"""隔离临时目录的小型多来源检查；不读取或修改真实项目数据。"""

import tempfile
import unittest
from pathlib import Path
from threading import Event

from core.exclusion_rules import prepare_exclusions
from core.merged_format import parse_file_blocks
from services.demand_rules import (
    build_demand_selection, inspect_demand_rules,
)
from services.merge_service import merge_files


class MultiSourceListsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ZipatchMultiSource_")
        self.root = Path(self.temporary.name)
        self.a = self.root / "project_a"
        self.b = self.root / "project_b"
        self.a.mkdir()
        self.b.mkdir()
        (self.a / "a.py").write_text("A\n", encoding="utf-8")
        (self.b / "b.py").write_text("B\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def select(self, lines, object_kind, base=""):
        jobs = tuple(enumerate(lines, 1))
        matches = inspect_demand_rules(
            jobs, base, Event(), object_kind=object_kind,
        )
        return build_demand_selection(tuple(matches[number] for number, _ in jobs))

    def merge(self, mode, directories, files="", base="", exclusions=None):
        output = self.root / "merged.txt"
        result = merge_files(
            mode=mode, relative_base_folder=base,
            directory_list_text=directories, list_text=files,
            output_file=str(output),
            exclusions=exclusions or prepare_exclusions("", ""),
            preamble_text="", ending_text="",
            code_header_line="---源起---", code_footer_line="---源终---",
            record_file_mtime=False,
        )
        return result, parse_file_blocks(output.read_text(encoding="utf-8"))

    def test_directory_rules_exact_wildcard_duplicate_and_disabled(self):
        selection = self.select(
            (
                str(self.a), str(self.root / "project_*"),
                "|not-a-path", "", "   ",
            ),
            "directory",
        )
        self.assertFalse(selection.has_errors)
        self.assertEqual(selection.files, (str(self.a), str(self.b)))
        self.assertEqual(selection.duplicates[1].duplicates, 1)

    def test_relative_rules_require_explicit_base(self):
        valid = self.select(("project_a",), "directory", str(self.root))
        self.assertEqual(valid.files, (str(self.a),))
        invalid = self.select(("project_a",), "directory")
        self.assertTrue(invalid.has_errors)
        wrong_kind = self.select((str(self.a / "a.py"),), "directory")
        self.assertTrue(wrong_kind.has_errors)

    def test_multiple_directory_order_and_rooted_exclusions(self):
        (self.a / "skip.py").write_text("skip A", encoding="utf-8")
        (self.b / "skip.py").write_text("skip B", encoding="utf-8")
        result, blocks = self.merge(
            "directory", str(self.b) + "\n" + str(self.a),
            exclusions=prepare_exclusions("", r"\skip.py"),
        )
        self.assertEqual(result.total_files, 2)
        self.assertEqual(
            [path for path, _ in blocks],
            [str(self.b / "b.py"), str(self.a / "a.py")],
        )
        self.assertEqual([body for _, body in blocks], ["B\n", "A\n"])

    def test_combined_explicit_include_and_file_only_independence(self):
        result, blocks = self.merge(
            "combined", str(self.a) + "\n" + str(self.b),
            files=str(self.a / "a.py"),
            exclusions=prepare_exclusions("", r"\a.py"),
        )
        self.assertEqual(result.total_files, 2)
        self.assertEqual(
            [path for path, _ in blocks],
            [str(self.a / "a.py"), str(self.b / "b.py")],
        )
        result, blocks = self.merge(
            "list", "an unused invalid directory",
            files="project_b/b.py", base=str(self.root),
        )
        self.assertEqual(result.total_files, 1)
        self.assertEqual(blocks, [(str(self.b / "b.py"), "B\n")])


if __name__ == "__main__":
    unittest.main(verbosity=2)