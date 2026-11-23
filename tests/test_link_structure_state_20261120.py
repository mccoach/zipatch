# -*- coding: utf-8 -*-

"""恢复结构状态的完整定义、摘要对应和未接通保护边界。"""

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file, observe_tree
from core.link_paths import (
    LinkSpecification, inspect_link_path, link_definition_digest,
    link_specification_digest,
)
from core.protection_projection import (
    FixedEffect, FixedUnit, absent_state, build_protection_projection,
    observation_state, state_from_bytes, state_from_link_specification,
    state_from_observation, validate_content_state,
)


@unittest.skipUnless(os.name == "nt", "链接结构状态使用Windows路径契约")
class LinkStructureStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_structure_state_",
        )
        self.root = Path(self.temporary.name)
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    if directory:
                        os.rmdir(entry)
                    else:
                        os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def make_link(self, entry, raw_target, *, directory=False):
        try:
            os.symlink(raw_target, entry, target_is_directory=directory)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("当前账户没有符号链接创建权限")
            raise
        self.links.append((entry, directory))

    def test_entry_state_keeps_definition_without_reading_target(self):
        entry = self.root / "alias.txt"
        self.make_link(entry, "missing.txt")
        observed = inspect_link_path(entry, follow_final_link=False)
        with patch(
            "pathlib.Path.open",
            side_effect=AssertionError("入口结构状态不得读取目标正文"),
        ):
            state, times = observation_state(observed)
            validate_content_state(state, target=entry)
        self.assertEqual(state.kind, "file_symlink")
        self.assertEqual(times, ())
        self.assertEqual(
            state.link_definitions,
            (("", LinkSpecification("file_symlink", "missing.txt", True)),),
        )
        self.assertEqual(
            state.records[0][2],
            link_definition_digest(observed.entry_link),
        )
        self.assertFalse((self.root / "missing.txt").exists())

    def test_structure_tree_keeps_directory_link_as_leaf(self):
        scope = self.root / "scope"
        scope.mkdir()
        target = self.root / "outside"
        target.mkdir()
        (target / "keep.txt").write_bytes(b"keep")
        entry = scope / "alias"
        self.make_link(entry, target, directory=True)
        observed = observe_tree(
            scope, include_links=True, include_bytes=True, include_mtime=True,
        )
        with patch(
            "pathlib.Path.open",
            side_effect=AssertionError("状态形成不得重新读取正文"),
        ):
            state = state_from_observation(observed)
        self.assertEqual(
            tuple(record[:2] for record in state.records),
            (("", "directory"), ("alias", "directory_symlink")),
        )
        self.assertEqual(state.link_definitions[0][0], "alias")
        specification = state.link_definitions[0][1]
        observed_entry = observed.links[0][1].entry_link
        # Windows读取绝对软链接时可以返回扩展路径前缀。
        # 原定义按系统实际返回值保存，不能以创建参数代替或删除前缀。
        self.assertEqual(specification.raw_target, os.readlink(entry))
        self.assertEqual(specification.raw_target, observed_entry.raw_target)
        self.assertFalse(specification.relative)
        self.assertEqual(observed_entry.direct_target, target)
        from core.link_paths import validate_link_specification
        self.assertEqual(
            validate_link_specification(specification, entry), target,
        )
        validate_content_state(state, target=scope)

    def test_missing_or_mismatched_definition_is_rejected(self):
        entry = self.root / "alias.txt"
        state = state_from_link_specification(
            LinkSpecification("file_symlink", "first.txt", True),
            target=entry,
        )
        altered = LinkSpecification("file_symlink", "second.txt", True)
        for candidate in (
            replace(state, link_definitions=()),
            replace(state, link_definitions=(("", altered),)),
            replace(
                state,
                link_definitions=(
                    ("", state.link_definitions[0][1]),
                    ("", state.link_definitions[0][1]),
                ),
            ),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    validate_content_state(candidate, target=entry)

    def test_definition_position_and_raw_target_are_checked(self):
        entry = self.root / "alias.txt"
        specification = LinkSpecification(
            "file_symlink", "first.txt", False,
        )
        # 摘要可以表达字段，但具体位置验证拒绝矛盾的相对标志。
        link_specification_digest(specification)
        with self.assertRaises(ValueError):
            state_from_link_specification(specification, target=entry)

        ordinary = state_from_bytes(b"ordinary")
        with self.assertRaises(ValueError):
            validate_content_state(replace(
                ordinary,
                link_definitions=((
                    "", LinkSpecification("file_symlink", "first.txt", True),
                ),),
            ), target=entry)

    def test_link_projection_records_only_fixed_entry_without_mutation(self):
        entry = self.root / "alias.txt"
        self.make_link(entry, "missing.txt")
        observed = inspect_link_path(entry, follow_final_link=False)
        original = state_from_observation(observed)
        projection = build_protection_projection(
            (FixedUnit(
                ("remove001",),
                (FixedEffect(observed, absent_state()),),
            ),),
            forbidden_roots=(),
        )
        self.assertEqual(len(projection.objects), 1)
        self.assertEqual(projection.objects[0].target, entry)
        self.assertEqual(projection.objects[0].original, original)
        self.assertEqual(projection.objects[0].file_times, ())
        self.assertEqual(
            inspect_link_path(entry, follow_final_link=False), observed,
        )

        new_entry = self.root / "new_alias.txt"
        desired = state_from_link_specification(
            LinkSpecification("file_symlink", "missing.txt", True),
            target=new_entry,
        )
        creation = build_protection_projection(
            (FixedUnit(
                ("create001",),
                (FixedEffect(observe_file(new_entry), desired),),
            ),),
            forbidden_roots=(),
        )
        self.assertEqual(creation.objects[0].original, absent_state())
        self.assertEqual(creation.units[0].effects[0].after, desired)
        # 纯投影不等于业务授权，也不创建链接或备份材料。
        self.assertFalse(os.path.lexists(new_entry))
        self.assertFalse((self.root / "missing.txt").exists())

    def test_link_parent_cannot_become_structure_protection(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.make_link(self.root / "parent", outside, directory=True)
        self.make_link(outside / "alias.txt", "missing.txt")
        observed = inspect_link_path(
            self.root / "parent" / "alias.txt", follow_final_link=False,
        )
        with self.assertRaises(ValueError):
            build_protection_projection(
                (FixedUnit(
                    ("remove001",),
                    (FixedEffect(observed, absent_state()),),
                ),),
                forbidden_roots=(),
            )
        self.assertTrue(os.path.lexists(outside / "alias.txt"))


if __name__ == "__main__":
    unittest.main()
