# -*- coding: utf-8 -*-

"""正式扫描的三类链接、剪枝、授权边界和来源输出隔离。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.filesystem_changes import execute_filesystem_change, prepare_link_creation
from core.link_paths import LinkSpecification
from services.scan_service import panoramic_scan


@unittest.skipUnless(os.name == "nt", "正式扫描链接业务使用Windows")
class ScanLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.private = self.outside / "private"
        self.private.write_bytes(b"private")
        self.output = self.root / "scan.txt"
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, kind):
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

    def scan(self, *, source=None, exclusions=None, authorize=None, output=None):
        return panoramic_scan(
            source_folder=source or self.source,
            output_file=output or self.output,
            exclusions=exclusions or prepare_exclusions("", ""),
            preamble_text="", ending_text="",
            include_size=True, include_date=True,
            authorize_scan_scope=authorize,
        )

    def test_three_link_kinds_are_leaves_and_never_read_target_body(self):
        (self.source / "ordinary").write_bytes(b"ordinary")
        self.link(self.source / "file", self.private, "file_symlink")
        self.link(self.source / "directory", self.outside, "directory_symlink")
        self.link(self.source / "junction", self.outside, "junction")
        original_open = Path.open
        original_scandir = os.scandir
        decisions = []

        def guarded_open(path, *args, **keywords):
            if path == self.private or self.source in path.parents:
                raise AssertionError("扫描不得读取来源或链接目标正文")
            return original_open(path, *args, **keywords)

        def guarded_scandir(path):
            if Path(path) == self.outside:
                raise AssertionError("扫描不得展开目录链接叶子")
            return original_scandir(path)

        def authorize(title, description):
            decisions.append((title, description))
            self.assertIn("file_symlink", description)
            self.assertIn("directory_symlink", description)
            self.assertIn("junction", description)
            return True

        with patch("pathlib.Path.open", new=guarded_open), patch(
            "os.scandir", new=guarded_scandir,
        ):
            result = self.scan(authorize=authorize)
        # 入口定义授权与目标元信息授权不是同一个范围。
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[1][0], "扫描链接目标大小与日期授权")
        self.assertEqual(result.file_count, 2)
        self.assertEqual(result.dir_count, 2)
        self.assertEqual(result.link_count, 3)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("实际原定义", text)
        self.assertIn("引用目标未展开", text)
        self.assertEqual(self.private.read_bytes(), b"private")

    def test_dangling_file_link_is_recorded_without_creating_target(self):
        self.link(self.source / "dangling", "missing", "file_symlink")
        result = self.scan(authorize=lambda title, description: True)
        self.assertEqual(result.file_count, 1)
        self.assertEqual(result.link_count, 1)
        self.assertIn("'missing'", self.output.read_text(encoding="utf-8"))
        self.assertFalse((self.source / "missing").exists())

    def test_no_authorization_does_not_publish_output(self):
        self.link(self.source / "file", self.private, "file_symlink")
        with self.assertRaises(ValueError):
            self.scan()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.private.read_bytes(), b"private")

    def test_cancelled_link_scope_does_not_publish_output(self):
        self.link(self.source / "file", self.private, "file_symlink")
        with self.assertRaises(ValueError):
            self.scan(authorize=lambda title, description: False)
        self.assertFalse(self.output.exists())

    def test_linked_source_root_requires_authorization_before_enumeration(self):
        self.link(self.root / "alias", self.outside, "directory_symlink")
        authorized = False
        original_scandir = os.scandir

        def guard(path):
            if Path(path) == self.outside:
                self.assertTrue(authorized)
            return original_scandir(path)

        def decide(title, description):
            nonlocal authorized
            self.assertIn(str(self.outside), description)
            authorized = True
            return True

        with patch("os.scandir", new=guard):
            result = self.scan(
                source=self.root / "alias", authorize=decide,
            )
        self.assertEqual(result.file_count, 1)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("名义扫描根", text)
        self.assertIn(str(self.root / "alias" / "private"), text)

    def test_unapproved_linked_root_is_not_enumerated(self):
        self.link(self.root / "alias", self.outside, "directory_symlink")
        original_scandir = os.scandir

        def guard(path):
            if Path(path) == self.outside:
                raise AssertionError("未授权实际扫描根不得枚举")
            return original_scandir(path)

        with patch("os.scandir", new=guard):
            with self.assertRaises(ValueError):
                self.scan(source=self.root / "alias")
        self.assertFalse(self.output.exists())

    def test_exclusions_prune_directories_and_link_entries(self):
        blocked = self.source / "blocked"
        blocked.mkdir()
        (blocked / "body").write_bytes(b"blocked")
        self.link(self.source / "excluded-dir", self.outside, "directory_symlink")
        self.link(self.source / "excluded-file", self.private, "file_symlink")
        (self.source / "keep").write_bytes(b"keep")
        original_scandir = os.scandir

        def guard(path):
            if Path(path) in (blocked, self.outside):
                raise AssertionError("排除目录或目录链接不得展开")
            return original_scandir(path)

        with patch("os.scandir", new=guard):
            result = self.scan(exclusions=prepare_exclusions(
                "blocked\nexcluded-dir", "excluded-file",
            ))
        self.assertEqual(result.file_count, 1)
        self.assertEqual(result.dir_count, 0)
        self.assertEqual(result.link_count, 0)

    def test_output_hardlink_to_source_is_rejected_before_write(self):
        body = self.source / "body"
        body.write_bytes(b"keep")
        os.link(body, self.output)
        with self.assertRaises(ValueError):
            self.scan()
        self.assertEqual(body.read_bytes(), b"keep")
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_retarget_during_authorization_stops_before_publication(self):
        alias = self.source / "alias"
        self.link(alias, "first", "file_symlink")

        def decide(title, description):
            alias.unlink()
            os.symlink("second", alias)
            return True

        with self.assertRaises(ValueError):
            self.scan(authorize=decide)
        self.assertFalse(self.output.exists())
        self.assertEqual(os.readlink(alias), "second")

    def test_output_inside_source_is_not_a_source_member(self):
        body = self.source / "body"
        body.write_bytes(b"keep")
        output = self.source / "scan.txt"
        output.write_bytes(b"old-output")
        result = self.scan(output=output)
        self.assertEqual(result.file_count, 1)
        text = output.read_text(encoding="utf-8")
        self.assertIn(str(body), text)
        self.assertNotIn("old-output", text)
        self.assertEqual(body.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
