# -*- coding: utf-8 -*-

"""扫描目标元信息的输出对象隔离及完整名义路线依赖。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from services.scan_metadata import prepare_target_metadata
from services.scan_scope import prepare_scan_scope
from services.scan_service import panoramic_scan


@unittest.skipUnless(os.name == "nt", "Windows扫描元信息安全边界")
class ScanMetadataIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.actual = self.root / "actual"
        self.actual.mkdir()
        self.body = self.actual / "body.bin"
        self.body.write_bytes(b"original-target")
        self.leaf = self.source / "leaf"
        os.symlink(self.body, self.leaf)
        self.output = self.root / "scan.txt"
        self.directory_links = []

    def tearDown(self):
        try:
            for path in reversed(self.directory_links):
                if os.path.lexists(path):
                    os.rmdir(path)
            if os.path.lexists(self.leaf):
                os.unlink(self.leaf)
        finally:
            self.temporary.cleanup()

    def scan(self, *, source=None, output=None, include_size=True, include_date=True):
        return panoramic_scan(
            source or self.source, output or self.output,
            prepare_exclusions("", ""), "", "",
            include_size=include_size, include_date=include_date,
            authorize_scan_scope=lambda title, description: True,
        )

    def directory_leaf(self):
        os.unlink(self.leaf)
        os.symlink(self.actual, self.leaf, target_is_directory=True)
        self.directory_links.append(self.leaf)

    def test_output_hardlink_to_link_target_is_rejected_without_body_change(self):
        os.link(self.body, self.output)
        before = observe_file(self.body)
        with self.assertRaisesRegex(ValueError, "共享硬链接对象"):
            self.scan()
        current = observe_file(self.body)
        self.assertEqual(current.object_identity, before.object_identity)
        self.assertEqual(current.link_count, 2)
        self.assertEqual(self.body.read_bytes(), b"original-target")
        self.assertEqual(self.output.read_bytes(), b"original-target")

    def test_new_output_inside_observed_directory_is_rejected_before_creation(self):
        self.directory_leaf()
        output = self.actual / "new" / "scan.txt"
        original_scandir = os.scandir

        def scandir(path):
            if Path(path) == self.actual:
                raise AssertionError("元信息隔离检查不得枚举目标目录")
            return original_scandir(path)

        with patch("os.scandir", new=scandir):
            with self.assertRaisesRegex(ValueError, "目标目录内"):
                self.scan(output=output)
        self.assertFalse((self.actual / "new").exists())
        self.assertEqual(self.body.read_bytes(), b"original-target")

    def test_existing_output_inside_observed_directory_remains_unchanged(self):
        self.directory_leaf()
        output = self.actual / "scan.txt"
        output.write_bytes(b"old-output")
        with self.assertRaisesRegex(ValueError, "目标目录内"):
            self.scan(output=output)
        self.assertEqual(output.read_bytes(), b"old-output")
        self.assertEqual(self.body.read_bytes(), b"original-target")

    def test_without_target_metadata_output_inside_leaf_target_remains_allowed(self):
        self.directory_leaf()
        output = self.actual / "scan.txt"
        result = self.scan(
            output=output, include_size=False, include_date=False,
        )
        self.assertEqual(result.link_count, 1)
        self.assertTrue(output.is_file())
        self.assertEqual(self.body.read_bytes(), b"original-target")

    def test_same_name_different_object_is_not_rejected_as_hardlink(self):
        self.output.write_bytes(b"old-output")
        result = self.scan()
        self.assertEqual(result.file_symlink_count, 1)
        self.assertIn("大小=15 bytes", self.output.read_text(encoding="utf-8"))
        self.assertEqual(self.body.read_bytes(), b"original-target")

    def test_metadata_observation_keeps_linked_scan_root_and_leaf(self):
        root_alias = self.root / "source-alias"
        os.symlink(self.source, root_alias, target_is_directory=True)
        self.directory_links.append(root_alias)
        expected = observe_file(self.output, include_digest=True)
        scope = prepare_scan_scope(
            root_alias, prepare_exclusions("", ""), expected,
            authorize=lambda title, description: True,
        )
        members = prepare_target_metadata(
            scope, expected, authorize=lambda title, description: True,
        )
        self.assertEqual(len(members), 1)
        access = members[0].access
        self.assertEqual(access.nominal_path, root_alias / "leaf")
        self.assertEqual(access.actual_path, self.body)
        self.assertEqual(
            tuple(definition.entry_path for definition in access.followed_links),
            (root_alias, self.leaf),
        )

    def test_root_retarget_during_metadata_authorization_stops_before_acquisition(self):
        root_alias = self.root / "source-alias"
        os.symlink(self.source, root_alias, target_is_directory=True)
        self.directory_links.append(root_alias)
        other = self.root / "other-source"
        other.mkdir()
        expected = observe_file(self.output, include_digest=True)
        scope = prepare_scan_scope(
            root_alias, prepare_exclusions("", ""), expected,
            authorize=lambda title, description: True,
        )
        original_observe = observe_file

        def observe(path, **keywords):
            if Path(path) == self.body:
                raise AssertionError("名义扫描根改指向后不得获取旧目标元信息")
            return original_observe(path, **keywords)

        def authorize(title, description):
            self.assertIn(str(root_alias), description)
            os.rmdir(root_alias)
            os.symlink(other, root_alias, target_is_directory=True)
            return True

        with patch("services.scan_metadata.observe_file", side_effect=observe):
            with self.assertRaises(ValueError):
                prepare_target_metadata(scope, expected, authorize=authorize)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.body.read_bytes(), b"original-target")


if __name__ == "__main__":
    unittest.main()