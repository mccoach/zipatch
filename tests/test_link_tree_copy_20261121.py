# -*- coding: utf-8 -*-

"""含链接树复制的真实Windows对象、固定范围及失败前缀验证。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file, observe_tree, recheck_tree
from core.filesystem_changes import (
    TreeCopyError, cancel_tree_copy, execute_filesystem_change,
    execute_tree_copy, prepare_link_creation, prepare_tree_copy,
)
from core.link_paths import LinkSpecification, inspect_link_path
from core.protection_projection import state_from_observation


@unittest.skipUnless(os.name == "nt", "链接树复制使用Windows")
class LinkTreeCopyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_link_tree_copy_",
        )
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.target = self.root / "copy"
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "private.bin").write_bytes(b"outside")
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def symlink(self, entry, raw, directory=False):
        os.symlink(raw, entry, target_is_directory=directory)
        self.links.append((entry, directory))
        return inspect_link_path(entry, follow_final_link=False)

    def observe_source(self):
        return observe_tree(
            self.source, include_bytes=True, include_mtime=True,
            include_links=True,
        )

    def test_mixed_tree_copies_definitions_without_reading_external_targets(self):
        nested = self.source / "nested"
        nested.mkdir()
        (self.source / "empty").mkdir()
        body = nested / "body.bin"
        body.write_bytes(b"\x00body")
        modified = 1700000000000000000
        os.utime(body, ns=(modified, modified))
        self.symlink(self.source / "middle", self.outside / "private.bin")
        self.symlink(self.source / "first", "middle")
        self.symlink(nested / "dangling", "missing")
        self.symlink(self.source / "folder", self.outside, True)
        junction = self.source / "joined"
        result = execute_filesystem_change(prepare_link_creation(
            observe_file(junction),
            LinkSpecification(
                "junction", "\\\\?\\" + str(self.outside), False,
            ),
        ))
        self.assertTrue(result.succeeded)
        self.links.append((junction, True))
        observed = self.observe_source()
        copy = prepare_tree_copy(observed, observe_file(self.target))
        original_open = Path.open

        def guarded_open(path, *args, **keywords):
            if path == self.outside or self.outside in path.parents:
                raise AssertionError("复制不得读取链接的根外目标")
            return original_open(path, *args, **keywords)

        # 失败时同样拥有测试清理责任，不能依赖完整成功才登记入口。
        for relative, member in observed.links:
            self.links.append((
                self.target / relative, member.kind != "file_symlink",
            ))
        with patch("pathlib.Path.open", new=guarded_open):
            receipt = execute_tree_copy(copy)
        self.assertTrue(receipt.succeeded)
        actual = copy.final_observation
        self.assertIsNotNone(actual)
        from core.fixed_unit_execution import verify_fixed_effects
        from core.protection_projection import FixedEffect

        # 来源具有最初完整bytes，可构建固定预期；
        # 成果观察只交付身份、摘要、结构和时间，不冒充备份来源。
        # 正式成果契约同时核验全部普通内容、链接原定义及对象前提。
        desired = state_from_observation(observed)
        verify_fixed_effects(
            (FixedEffect(copy.expected, desired),), (actual,),
        )
        self.assertEqual(actual.content_records, desired.records)
        self.assertEqual(actual.file_times, observed.file_times)
        self.assertEqual((self.target / "nested" / "body.bin").read_bytes(), b"\x00body")
        self.assertEqual(
            dict(actual.links)["first"].entry_link.raw_target, "middle",
        )
        self.assertEqual(
            dict(actual.links)["first"].entry_link.direct_target,
            self.target / "middle",
        )
        self.assertFalse((self.target / "nested" / "missing").exists())
        self.assertEqual(
            sum(member.kind == "create_link" for member in receipt.results),
            len(observed.links),
        )
        self.assertEqual((self.outside / "private.bin").read_bytes(), b"outside")
        recheck_tree(observed, check_mtime=True)
        with self.assertRaises(ValueError):
            execute_tree_copy(copy)
        with self.assertRaises(ValueError):
            cancel_tree_copy(copy)

    def test_external_member_after_link_creation_stops_and_preserves_prefix(self):
        self.symlink(self.source / "alias", "missing")
        observed = self.observe_source()
        copy = prepare_tree_copy(observed, observe_file(self.target))
        from core import filesystem_changes
        original_execute = filesystem_changes.execute_filesystem_change
        external = self.target / "external.bin"
        self.links.append((self.target / "alias", False))

        def execute_and_inject(step):
            receipt = original_execute(step)
            if receipt.kind == "create_link":
                external.write_bytes(b"external")
            return receipt

        with patch(
            "core.filesystem_changes.execute_filesystem_change",
            side_effect=execute_and_inject,
        ):
            with self.assertRaises(TreeCopyError) as caught:
                execute_tree_copy(copy)
        self.assertEqual(
            tuple(member.kind for member in caught.exception.result.results),
            ("create_directory", "create_link"),
        )
        self.assertTrue(all(
            member.succeeded for member in caught.exception.result.results
        ))
        self.assertEqual(external.read_bytes(), b"external")
        self.assertTrue(os.path.lexists(self.target / "alias"))
        self.assertIsNone(copy.final_observation)
        recheck_tree(observed, check_mtime=True)
        with self.assertRaises(ValueError):
            execute_tree_copy(copy)
        with self.assertRaises(ValueError):
            cancel_tree_copy(copy)

    def test_retargeted_source_blocks_copy_before_any_target_change(self):
        entry = self.source / "alias"
        self.symlink(entry, "first")
        observed = self.observe_source()
        copy = prepare_tree_copy(observed, observe_file(self.target))
        entry.unlink()
        os.symlink("second", entry)
        with self.assertRaises(TreeCopyError) as caught:
            execute_tree_copy(copy)
        self.assertEqual(caught.exception.result.results, ())
        self.assertFalse(os.path.lexists(self.target))
        self.assertEqual(os.readlink(entry), "second")
        with self.assertRaises(ValueError):
            execute_tree_copy(copy)


if __name__ == "__main__":
    unittest.main()
