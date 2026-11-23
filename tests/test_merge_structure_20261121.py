# -*- coding: utf-8 -*-

"""合并正式入口、结构解析、叶子安全及普通正文回归。"""

import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.filesystem_changes import execute_filesystem_change, prepare_link_creation
from core.link_paths import LinkSpecification, inspect_link_path, specification_from_link
from core.merged_format import (
    parse_merge_blocks, parse_file_blocks, decode_structure_line,
)
from services.demand_rules import inspect_demand_rules
from services.merge_service import merge_files


@unittest.skipUnless(os.name == "nt", "合并结构正式业务使用Windows")
class MergeStructureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.private = self.outside / "private"
        self.private.write_bytes(b"private")
        self.output = self.root / "merged.txt"
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, name, target, kind):
        entry = self.source / name
        if kind == "junction":
            receipt = execute_filesystem_change(prepare_link_creation(
                observe_file(entry),
                LinkSpecification("junction", "\\\\?\\" + str(target), False),
            ))
            self.assertTrue(receipt.succeeded)
        else:
            os.symlink(
                target, entry, target_is_directory=kind == "directory_symlink",
            )
        self.links.append((entry, kind != "file_symlink"))
        return inspect_link_path(entry, follow_final_link=False)

    def merge(self, mode="directory", list_text="", exclusions=None, authorize=None):
        return merge_files(
            mode, str(self.source), list_text, str(self.output),
            exclusions or prepare_exclusions("", ""),
            "", "", CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
            authorize_merge_scope=authorize,
        )

    def blocks(self):
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
        return text, parse_merge_blocks(text)

    def test_directory_preserves_empty_directory_and_three_original_definitions(self):
        (self.source / "empty").mkdir()
        body = "正文\r\n尾行\n\n"
        (self.source / "body").write_bytes(body.encode("utf-8"))
        originals = {}
        for name, target, kind in (
            ("file", self.private, "file_symlink"),
            ("directory", self.outside, "directory_symlink"),
            ("junction", self.outside, "junction"),
        ):
            observed = self.link(name, target, kind)
            originals[str(observed.path)] = specification_from_link(observed.entry_link)
        original_open = Path.open

        def guard(path, *args, **keywords):
            if path == self.private:
                raise AssertionError("入口合并不得读取引用目标正文")
            return original_open(path, *args, **keywords)

        with patch("pathlib.Path.open", new=guard):
            self.merge(authorize=lambda title, description: True)
        text, blocks = self.blocks()
        by_path = {block.path: block for block in blocks}
        self.assertEqual(by_path[str(self.source / "empty")].kind, "directory")
        self.assertEqual(by_path[str(self.source / "body")].body, body)
        for path, specification in originals.items():
            self.assertEqual(by_path[path].specification, specification)
            self.assertEqual(by_path[path].body, "")
        with self.assertRaises(ValueError):
            parse_file_blocks(text)
        self.assertEqual(self.private.read_bytes(), b"private")

    def test_list_exact_selects_three_link_entry_kinds(self):
        names = []
        for name, target, kind in (
            ("file", "missing", "file_symlink"),
            ("directory", self.outside, "directory_symlink"),
            ("junction", self.outside, "junction"),
        ):
            self.link(name, target, kind)
            names.append(name)
        matches = inspect_demand_rules(
            tuple(enumerate(names)), str(self.source), Event(),
        )
        self.assertTrue(all(match.completed for match in matches.values()))
        self.merge(
            "list", "\n".join(names),
            authorize=lambda title, description: True,
        )
        _, blocks = self.blocks()
        self.assertEqual(len(blocks), 3)
        self.assertEqual(tuple(block.kind for block in blocks), (
            "file_symlink", "directory_symlink", "junction",
        ))
        self.assertFalse((self.source / "missing").exists())

    def test_wildcard_selects_link_leaf_without_entering_directory_target(self):
        self.link("folder", self.outside, "directory_symlink")
        self.link("file", self.private, "file_symlink")
        original_scandir = os.scandir

        def guard(path):
            if Path(path) == self.outside:
                raise AssertionError("通配符不得递归进入链接叶子")
            return original_scandir(path)

        with patch("os.scandir", new=guard):
            matches = inspect_demand_rules(
                ((1, "**/*"),), str(self.source), Event(),
            )
        self.assertTrue(matches[1].completed, matches[1].issue)
        self.assertEqual(set(matches[1].files), {
            str(self.source / "folder"), str(self.source / "file"),
        })

    def test_exact_parent_link_file_reads_only_after_actual_range_authorization(self):
        self.link("folder", self.outside, "directory_symlink")
        authorized = False
        original_open = Path.open

        def guard(path, *args, **keywords):
            if path == self.private:
                self.assertTrue(authorized)
            return original_open(path, *args, **keywords)

        def decide(title, description):
            nonlocal authorized
            self.assertIn(str(self.private), description)
            authorized = True
            return True

        with patch("pathlib.Path.open", new=guard):
            self.merge("list", "folder/private", authorize=decide)
        _, blocks = self.blocks()
        self.assertEqual(blocks[0].body, "private")
        self.assertEqual(blocks[0].kind, "file")
        self.assertEqual(self.private.read_bytes(), b"private")

    def test_unapproved_link_scope_reads_no_plain_body_and_publishes_nothing(self):
        body = self.source / "body"
        body.write_bytes(b"body")
        self.link("link", "missing", "file_symlink")
        original_open = Path.open

        def guard(path, *args, **keywords):
            if path == body:
                raise AssertionError("固定链接范围决定前不得读取正文")
            return original_open(path, *args, **keywords)

        with patch("pathlib.Path.open", new=guard):
            with self.assertRaises(ValueError):
                self.merge()
        self.assertFalse(self.output.exists())

    def test_combined_explicit_list_overrides_file_exclusion_and_preserves_order(self):
        first = self.source / "a"
        second = self.source / "b"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.merge("combined", "a\na", exclusions=prepare_exclusions("", "a"))
        _, blocks = self.blocks()
        self.assertEqual(tuple(block.path for block in blocks), (str(first), str(second)))
        self.assertEqual(blocks[0].body, "first")

    def test_plain_file_only_container_still_parses_exact_original_body(self):
        body = " \r\n源码\n尾\r"
        (self.source / "body").write_bytes(body.encode("utf-8"))
        self.merge("list", "body")
        text, _ = self.blocks()
        self.assertEqual(
            parse_file_blocks(text),
            [(str(self.source / "body"), body)],
        )

    def test_shared_output_object_is_rejected_before_source_read_or_write(self):
        body = self.source / "body"
        body.write_bytes(b"keep")
        os.link(body, self.output)
        with self.assertRaises(ValueError):
            self.merge()
        self.assertEqual(body.read_bytes(), b"keep")
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_structural_definition_unknown_duplicate_and_wrong_type_are_rejected(self):
        path = str(self.source / "link")
        for line in (
            'ZIPATCH_ENTRY:{"kind":"directory","extra":1}',
            'ZIPATCH_ENTRY:{"kind":"directory","kind":"directory"}',
            'ZIPATCH_ENTRY:{"kind":"file_symlink","raw_target":"missing","relative":0}',
        ):
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    decode_structure_line(line, entry_path=path)


if __name__ == "__main__":
    unittest.main()
