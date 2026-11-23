# -*- coding: utf-8 -*-

"""链接路径元信息观察的Windows真实对象契约测试。

所有对象位于独立临时目录；不访问用户项目或修改真实链接。
符号链接创建权限不足时只跳过对应测试，目录联接测试仍执行。
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.link_paths import (
    inspect_link_path, recheck_link_path,
)
from core.recovery_paths import UnsafePathError, checked_chain, identity


@unittest.skipUnless(os.name == "nt", "本项目链接路径能力只支持Windows")
class LinkPathObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_link_paths_",
        )
        self.root = Path(self.temporary.name)
        self.owned_links = []

    def tearDown(self):
        try:
            # 先移除自己创建的入口，不沿目录链接清理目标。
            for path, directory in reversed(self.owned_links):
                if not os.path.lexists(path):
                    continue
                if directory:
                    os.rmdir(path)
                else:
                    os.unlink(path)
        finally:
            self.temporary.cleanup()

    def make_symlink(self, entry, target, *, directory=False):
        try:
            os.symlink(
                target, entry, target_is_directory=directory,
            )
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("当前Windows账户没有创建符号链接权限")
            raise
        self.owned_links.append((entry, directory))

    def make_junction(self, entry, target):
        # 两端均来自本测试临时目录，不接收用户输入。
        # CMD不采用C运行库的参数转义规则。传递完整命令行，
        # /s /c只剥去命令的最外层引号，保留两个路径的原生引号。
        command_processor = os.environ.get("COMSPEC", "cmd.exe")
        command = (
            f'"{command_processor}" /d /v:off /s /c '
            f'"mklink /J "{entry}" "{target}""'
        )
        completed = subprocess.run(
            command,
            executable=command_processor,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            self.fail(
                f"临时目录联接创建失败；退出码={completed.returncode}。\n"
                f"入口：{entry}\n目标：{target}\n"
                + completed.stdout.decode("oem", errors="backslashreplace")
                + completed.stderr.decode("oem", errors="backslashreplace")
            )
        # 创建正常返回后先登记清理责任，再核验是否真的生成目录联接。
        self.owned_links.append((entry, True))
        observed = inspect_link_path(entry, follow_final_link=False)
        self.assertEqual(observed.kind, "junction")
        self.assertEqual(
            identity(observed.entry_link.direct_target), identity(target),
        )

    def test_regular_file_has_no_link_dependencies(self):
        source = self.root / "source.txt"
        source.write_bytes(b"fixed")
        observed = inspect_link_path(source, follow_final_link=True)
        self.assertEqual(observed.nominal_path, source)
        self.assertEqual(observed.actual_path, source)
        self.assertEqual(observed.kind, "file")
        self.assertFalse(observed.has_links)
        self.assertIsNone(observed.first_missing)
        self.assertEqual(recheck_link_path(observed), observed)

    def test_missing_parent_stops_at_first_missing_position(self):
        missing = self.root / "missing"
        target = missing / "child" / "source.txt"
        observed = inspect_link_path(target, follow_final_link=True)
        self.assertEqual(observed.kind, "absent")
        self.assertEqual(observed.actual_path, target)
        self.assertEqual(observed.first_missing, missing)
        self.assertFalse(observed.target_unavailable)

    def test_file_link_entry_and_content_location_are_distinct(self):
        source = self.root / "source.txt"
        source.write_bytes(b"fixed")
        link = self.root / "alias.txt"
        self.make_symlink(link, source)

        entry = inspect_link_path(link, follow_final_link=False)
        content = inspect_link_path(link, follow_final_link=True)
        self.assertEqual(entry.kind, "file_symlink")
        self.assertEqual(entry.actual_path, link)
        self.assertIsNotNone(entry.entry_link)
        self.assertEqual(entry.followed_links, ())
        self.assertEqual(content.kind, "file")
        self.assertEqual(content.actual_path, source)
        self.assertEqual(content.nominal_path, link)
        self.assertIsNone(content.entry_link)
        self.assertEqual(len(content.followed_links), 1)

    def test_relative_definition_keeps_direct_intermediate_target(self):
        source = self.root / "source.txt"
        source.write_bytes(b"fixed")
        middle = self.root / "middle.txt"
        first = self.root / "first.txt"
        self.make_symlink(middle, "source.txt")
        self.make_symlink(first, "middle.txt")

        observed = inspect_link_path(first, follow_final_link=True)
        self.assertEqual(observed.actual_path, source)
        self.assertEqual(len(observed.followed_links), 2)
        definition = observed.followed_links[0]
        self.assertTrue(definition.relative)
        self.assertEqual(definition.raw_target, "middle.txt")
        self.assertEqual(definition.direct_target, middle)

    def test_junction_child_records_nominal_and_actual_paths(self):
        target = self.root / "target"
        target.mkdir()
        source = target / "source.txt"
        source.write_bytes(b"fixed")
        link = self.root / "joined"
        self.make_junction(link, target)

        observed = inspect_link_path(
            link / "source.txt", follow_final_link=True,
        )
        self.assertEqual(observed.nominal_path, link / "source.txt")
        self.assertEqual(observed.actual_path, source)
        self.assertEqual(observed.kind, "file")
        self.assertEqual(len(observed.followed_links), 1)
        self.assertEqual(observed.followed_links[0].kind, "junction")
        self.assertEqual(
            identity(observed.followed_links[0].direct_target),
            identity(target),
        )

    def test_junction_paths_with_chinese_and_spaces_are_preserved(self):
        parent = self.root / "中文 空格目录"
        parent.mkdir()
        target = parent / "实际 目标"
        target.mkdir()
        source = target / "正文 文件.txt"
        source.write_bytes(b"fixed")
        link = parent / "联接 入口"
        self.make_junction(link, target)

        observed = inspect_link_path(
            link / source.name, follow_final_link=True,
        )
        self.assertEqual(observed.nominal_path, link / source.name)
        self.assertEqual(observed.actual_path, source)
        self.assertEqual(observed.kind, "file")
        self.assertEqual(len(observed.followed_links), 1)
        self.assertEqual(observed.followed_links[0].kind, "junction")
        self.assertEqual(recheck_link_path(observed), observed)
        self.assertEqual(source.read_bytes(), b"fixed")

    def test_directory_symlink_child_is_resolved(self):
        target = self.root / "target"
        target.mkdir()
        source = target / "source.txt"
        source.write_bytes(b"fixed")
        link = self.root / "directory_alias"
        self.make_symlink(link, target, directory=True)

        observed = inspect_link_path(
            link / "source.txt", follow_final_link=True,
        )
        self.assertEqual(observed.actual_path, source)
        self.assertEqual(
            observed.followed_links[0].kind, "directory_symlink",
        )

    def test_dangling_link_is_not_an_absent_link_entry(self):
        target = self.root / "missing.txt"
        link = self.root / "dangling.txt"
        self.make_symlink(link, target)

        entry = inspect_link_path(link, follow_final_link=False)
        content = inspect_link_path(link, follow_final_link=True)
        self.assertEqual(entry.kind, "file_symlink")
        self.assertIsNotNone(entry.object_identity)
        self.assertEqual(content.kind, "absent")
        self.assertTrue(content.target_unavailable)
        self.assertEqual(content.first_missing, target)

    def test_link_cycle_is_rejected_without_reading_content(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        self.make_symlink(first, "second.txt")
        self.make_symlink(second, "first.txt")
        with self.assertRaises(UnsafePathError) as captured:
            inspect_link_path(first, follow_final_link=True)
        self.assertEqual(captured.exception.kind, "link_cycle")

    def test_same_directory_link_can_be_used_twice_without_false_cycle(self):
        link = self.root / "again"
        source = self.root / "source.txt"
        source.write_bytes(b"fixed")
        self.make_symlink(link, self.root, directory=True)
        observed = inspect_link_path(
            link / "again" / "source.txt", follow_final_link=True,
        )
        self.assertEqual(observed.actual_path, source)
        self.assertEqual(len(observed.followed_links), 2)

    def test_retargeted_entry_invalidates_fixed_observation(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"same")
        second.write_bytes(b"same")
        link = self.root / "alias.txt"
        self.make_symlink(link, first)
        observed = inspect_link_path(link, follow_final_link=True)
        os.unlink(link)
        os.symlink(second, link)
        with self.assertRaises(ValueError):
            recheck_link_path(observed)

    def test_replaced_actual_object_invalidates_fixed_observation(self):
        source = self.root / "source.txt"
        source.write_bytes(b"same")
        link = self.root / "alias.txt"
        self.make_symlink(link, source)
        observed = inspect_link_path(link, follow_final_link=True)

        # 保留旧对象名称，确保测试不依赖文件编号删除后的复用政策。
        source.rename(self.root / "original_saved.txt")
        source.write_bytes(b"same")
        with self.assertRaises(ValueError):
            recheck_link_path(observed)

    def test_discovery_does_not_open_content_or_enumerate_directory(self):
        target = self.root / "target"
        target.mkdir()
        source = target / "source.txt"
        source.write_bytes(b"fixed")
        link = self.root / "joined"
        self.make_junction(link, target)
        with (
            patch(
                "pathlib.Path.open",
                side_effect=AssertionError("探查不得打开文件正文"),
            ),
            patch(
                "os.scandir",
                side_effect=AssertionError("探查不得枚举目录"),
            ),
        ):
            observed = inspect_link_path(
                link / "source.txt", follow_final_link=True,
            )
        self.assertEqual(observed.actual_path, source)

    def test_existing_strict_path_policy_is_not_relaxed(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "joined"
        self.make_junction(link, target)
        with self.assertRaises(UnsafePathError):
            checked_chain(link / "source.txt")

    def test_nominal_parent_navigation_is_rejected(self):
        with self.assertRaises(UnsafePathError) as captured:
            inspect_link_path(
                self.root / "child" / ".." / "source.txt",
                follow_final_link=True,
            )
        self.assertEqual(captured.exception.kind, "parent_navigation")

    def test_accessed_file_observation_preserves_actual_and_nominal_facts(self):
        from core.file_observation import observe_accessed_file, recheck_file

        source = self.root / "source.txt"
        source.write_bytes(b"fixed")
        link = self.root / "alias.txt"
        self.make_symlink(link, source)
        access = inspect_link_path(link, follow_final_link=True)
        observed = observe_accessed_file(
            access, include_bytes=True, include_digest=True,
            include_mtime=True,
        )
        self.assertEqual(observed.path, source)
        self.assertIs(observed.access, access)
        self.assertEqual(observed.access.nominal_path, link)
        self.assertEqual(observed.source_bytes, b"fixed")
        self.assertIs(recheck_file(observed).access, access)

    def test_content_publication_preserves_link_entry_and_new_baseline(self):
        from core.content_publication import (
            cleanup_content_artifact, prepare_content_artifact, publish_content,
        )
        from core.file_observation import observe_accessed_file, recheck_file

        source = self.root / "source.txt"
        source.write_bytes(b"before")
        saved_original = self.root / "saved_original.txt"
        link = self.root / "alias.txt"
        self.make_symlink(link, source)
        access = inspect_link_path(link, follow_final_link=True)
        observed = observe_accessed_file(access, include_digest=True)
        original_definition = os.readlink(link)
        artifact = prepare_content_artifact(observed, b"after")
        try:
            result = publish_content(artifact)
            self.assertTrue(result.succeeded)
            self.assertEqual(result.mechanism, "atomic_replace")
            self.assertIs(result.observation.access, access)
            self.assertEqual(os.readlink(link), original_definition)
            self.assertEqual(link.read_bytes(), b"after")
            recheck_file(result.observation)

            # 同字节的外部替代文件不取得正常成果身份。
            source.rename(saved_original)
            source.write_bytes(b"after")
            with self.assertRaises(ValueError):
                recheck_file(result.observation)
        finally:
            cleanup_content_artifact(artifact)

    def test_accessed_hardlink_content_uses_existing_in_place_mechanism(self):
        from core.content_publication import (
            cleanup_content_artifact, prepare_content_artifact, publish_content,
        )
        from core.file_observation import observe_accessed_file, recheck_file

        source = self.root / "source.txt"
        source.write_bytes(b"before")
        shared = self.root / "shared.txt"
        os.link(source, shared)
        link = self.root / "alias.txt"
        self.make_symlink(link, source)
        access = inspect_link_path(link, follow_final_link=True)
        observed = observe_accessed_file(access, include_digest=True)
        artifact = prepare_content_artifact(observed, b"after")
        try:
            result = publish_content(artifact)
            self.assertTrue(result.succeeded)
            self.assertEqual(result.mechanism, "in_place")
            self.assertEqual(
                result.observation.object_identity, observed.object_identity,
            )
            self.assertIs(result.observation.access, access)
            self.assertEqual(source.read_bytes(), b"after")
            self.assertEqual(shared.read_bytes(), b"after")
            self.assertEqual(link.read_bytes(), b"after")
            recheck_file(result.observation)
        finally:
            cleanup_content_artifact(artifact)

    def test_retarget_after_content_preparation_prevents_publication(self):
        from core.content_publication import (
            ContentPublicationError, cleanup_content_artifact,
            prepare_content_artifact, publish_content,
        )
        from core.file_observation import observe_accessed_file

        source = self.root / "source.txt"
        other = self.root / "other.txt"
        source.write_bytes(b"before")
        other.write_bytes(b"other")
        link = self.root / "alias.txt"
        self.make_symlink(link, source)
        observed = observe_accessed_file(
            inspect_link_path(link, follow_final_link=True),
            include_digest=True,
        )
        artifact = prepare_content_artifact(observed, b"after")
        try:
            os.unlink(link)
            os.symlink(other, link)
            with self.assertRaises(ContentPublicationError) as captured:
                publish_content(artifact)
            self.assertFalse(captured.exception.result.mutation_attempted)
            self.assertEqual(source.read_bytes(), b"before")
            self.assertEqual(other.read_bytes(), b"other")
        finally:
            cleanup_content_artifact(artifact)

    def test_protection_projection_protects_content_not_access_link(self):
        from core.file_observation import observe_accessed_file
        from core.protection_projection import (
            FixedEffect, FixedUnit, build_protection_projection,
            state_from_bytes,
        )

        source = self.root / "source.txt"
        source.write_bytes(b"before")
        link = self.root / "alias.txt"
        self.make_symlink(link, source)
        observed = observe_accessed_file(
            inspect_link_path(link, follow_final_link=True),
            include_bytes=True, include_digest=True, include_mtime=True,
        )
        projection = build_protection_projection(
            (FixedUnit(
                ("content001",),
                (FixedEffect(observed, state_from_bytes(b"after")),),
            ),),
            forbidden_roots=(),
        )
        self.assertEqual(len(projection.objects), 1)
        self.assertEqual(projection.objects[0].target, source)
        self.assertIs(projection.objects[0].before.access, observed.access)
        self.assertNotEqual(projection.objects[0].target, link)

    def test_entry_observation_cannot_be_used_for_content_reading(self):
        from core.file_observation import observe_accessed_file

        source = self.root / "source.txt"
        source.write_bytes(b"fixed")
        link = self.root / "alias.txt"
        self.make_symlink(link, source)
        entry = inspect_link_path(link, follow_final_link=False)
        with patch(
            "pathlib.Path.open",
            side_effect=AssertionError("入口观察不得读取目标正文"),
        ):
            with self.assertRaises(ValueError):
                observe_accessed_file(entry, include_bytes=True)

    def test_tree_records_junction_without_expanding_its_target(self):
        from core.file_observation import observe_tree, recheck_tree

        scope = self.root / "scope"
        scope.mkdir()
        target = self.root / "outside"
        target.mkdir()
        protected_file = target / "keep.txt"
        protected_file.write_bytes(b"keep")
        link = scope / "joined"
        self.make_junction(link, target)
        observed = observe_tree(scope, include_links=True)
        self.assertEqual(
            tuple(relative for relative, _ in observed.directories), ("",),
        )
        self.assertEqual(observed.files, ())
        self.assertEqual(
            tuple(relative for relative, _ in observed.links), ("joined",),
        )
        self.assertEqual(observed.links[0][1].kind, "junction")
        self.assertEqual(
            tuple(record[:2] for record in observed.content_records),
            (("", "directory"), ("joined", "junction")),
        )
        recheck_tree(observed)
        self.assertEqual(protected_file.read_bytes(), b"keep")
        with self.assertRaises(UnsafePathError):
            observe_tree(scope)

    def test_tree_records_dangling_file_link_without_reading_target(self):
        from core.file_observation import observe_tree

        scope = self.root / "scope"
        scope.mkdir()
        link = scope / "missing_alias.txt"
        self.make_symlink(link, self.root / "missing.txt")
        with patch(
            "pathlib.Path.open",
            side_effect=AssertionError("结构树不得读取链接目标正文"),
        ):
            observed = observe_tree(
                scope, include_links=True, include_bytes=True,
                include_mtime=True,
            )
        self.assertEqual(observed.files, ())
        self.assertEqual(observed.file_times, ())
        self.assertEqual(len(observed.links), 1)
        self.assertEqual(observed.links[0][1].kind, "file_symlink")

    def test_tree_recheck_rejects_link_retargeting(self):
        from core.file_observation import observe_tree, recheck_tree

        scope = self.root / "scope"
        scope.mkdir()
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"same")
        second.write_bytes(b"same")
        link = scope / "alias.txt"
        self.make_symlink(link, first)
        observed = observe_tree(scope, include_links=True)
        os.unlink(link)
        os.symlink(second, link)
        with self.assertRaises(ValueError):
            recheck_tree(observed)

    def test_junction_removal_changes_only_entry_and_consumes_qualification(self):
        from core.filesystem_changes import (
            execute_filesystem_change, prepare_link_removal,
        )

        target = self.root / "target"
        target.mkdir()
        protected_file = target / "keep.txt"
        protected_file.write_bytes(b"keep")
        link = self.root / "joined"
        self.make_junction(link, target)
        change = prepare_link_removal(
            inspect_link_path(link, follow_final_link=False),
        )
        result = execute_filesystem_change(change)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.kind, "remove_link")
        self.assertEqual(result.target, link)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(target.is_dir())
        self.assertEqual(protected_file.read_bytes(), b"keep")
        with self.assertRaises(ValueError):
            execute_filesystem_change(change)

    def test_directory_symlink_removal_does_not_delete_target_members(self):
        from core.filesystem_changes import (
            execute_filesystem_change, prepare_link_removal,
        )

        target = self.root / "target"
        target.mkdir()
        protected_file = target / "keep.txt"
        protected_file.write_bytes(b"keep")
        link = self.root / "directory_alias"
        self.make_symlink(link, target, directory=True)
        result = execute_filesystem_change(prepare_link_removal(
            inspect_link_path(link, follow_final_link=False),
        ))
        self.assertTrue(result.succeeded)
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(protected_file.read_bytes(), b"keep")

    def test_dangling_file_link_can_be_removed_as_an_entry(self):
        from core.filesystem_changes import (
            execute_filesystem_change, prepare_link_removal,
        )

        target = self.root / "missing.txt"
        link = self.root / "alias.txt"
        self.make_symlink(link, target)
        result = execute_filesystem_change(prepare_link_removal(
            inspect_link_path(link, follow_final_link=False),
        ))
        self.assertTrue(result.succeeded)
        self.assertFalse(os.path.lexists(link))
        self.assertFalse(target.exists())

    def test_retargeted_link_removal_is_refused_before_mutation(self):
        from core.filesystem_changes import (
            FilesystemChangeError, execute_filesystem_change,
            prepare_link_removal,
        )

        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        link = self.root / "alias.txt"
        self.make_symlink(link, first)
        change = prepare_link_removal(
            inspect_link_path(link, follow_final_link=False),
        )
        os.unlink(link)
        os.symlink(second, link)
        with self.assertRaises(FilesystemChangeError) as captured:
            execute_filesystem_change(change)
        self.assertFalse(captured.exception.result.attempted)
        self.assertTrue(os.path.lexists(link))
        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")

    def test_unconverted_consumers_cannot_omit_tree_links(self):
        from core.file_observation import observe_file, observe_tree
        from core.filesystem_changes import prepare_tree_copy, prepare_tree_removal
        from core.protection_projection import (
            FixedEffect, FixedUnit, absent_state,
            build_protection_projection, state_from_observation,
        )

        scope = self.root / "scope"
        scope.mkdir()
        link = scope / "alias.txt"
        self.make_symlink(link, self.root / "missing.txt")
        observed = observe_tree(
            scope, include_links=True, include_bytes=True,
            include_mtime=True,
        )
        removal = prepare_tree_removal(observed)
        self.assertEqual(
            tuple(kind for kind, _, _ in removal.steps),
            ("remove_link", "remove_empty_directory"),
        )
        copy = prepare_tree_copy(
            observed, observe_file(self.root / "copy_target"),
        )
        self.assertIs(copy.source, observed)
        self.assertEqual(copy.source.links, observed.links)
        from core.filesystem_changes import cancel_tree_copy
        cancel_tree_copy(copy)
        structural_state = state_from_observation(observed)
        self.assertEqual(len(structural_state.link_definitions), 1)
        # 正式复制准备及保护均须完整保留链接；纯准备不改变磁盘。
        # 修改包本地授权尚未接通，其业务入口仍由原路径政策拒绝。
        projection = build_protection_projection(
            (FixedUnit(
                ("remove001",),
                (FixedEffect(observed, absent_state()),),
            ),),
            forbidden_roots=(),
        )
        self.assertEqual(len(projection.objects), 1)
        protected = projection.objects[0]
        self.assertIs(protected.before, observed)
        self.assertEqual(protected.target, scope)
        self.assertEqual(protected.original, structural_state)
        self.assertEqual(
            protected.original.records, observed.content_records,
        )
        from core.link_paths import specification_from_link
        self.assertEqual(
            protected.original.link_definitions,
            (("alias.txt", specification_from_link(
                observed.links[0][1].entry_link,
            )),),
        )
        self.assertEqual(protected.file_times, observed.file_times)
        self.assertEqual(projection.units[0].object_ids, (protected.object_id,))
        self.assertEqual(projection.units[0].effects[0].after, absent_state())

        # 含链接记录却丢失定义的状态仍非法，不授予保护资格。
        from dataclasses import replace
        with self.assertRaises(ValueError):
            build_protection_projection(
                (FixedUnit(
                    ("invalid001",),
                    (FixedEffect(
                        observed,
                        replace(structural_state, link_definitions=()),
                    ),),
                ),),
                forbidden_roots=(),
            )
        self.assertEqual(recheck_link_path(observed.links[0][1]), observed.links[0][1])
        self.assertTrue(os.path.lexists(link))
        self.assertFalse(os.path.lexists(self.root / "copy_target"))
        self.assertFalse((self.root / "missing.txt").exists())

    def rebuild_saved_link(self, entry):
        from core.file_observation import observe_file
        from core.filesystem_changes import (
            execute_filesystem_change, prepare_link_creation,
            prepare_link_removal,
        )
        from core.link_paths import (
            decode_link_specification, encode_link_specification,
            specification_from_link,
        )

        original = inspect_link_path(entry, follow_final_link=False)
        specification = specification_from_link(original.entry_link)
        material = encode_link_specification(
            specification, entry_path=entry,
        )
        decoded = decode_link_specification(material, entry_path=entry)
        self.assertEqual(decoded, specification)
        removed = execute_filesystem_change(prepare_link_removal(original))
        self.assertTrue(removed.succeeded)
        change = prepare_link_creation(observe_file(entry), decoded)
        self.assertFalse(os.path.lexists(entry))
        result = execute_filesystem_change(change)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.kind, "create_link")
        self.assertIsNotNone(result.created_link)
        self.assertEqual(
            specification_from_link(result.created_link.entry_link),
            specification,
        )
        self.assertEqual(
            result.created_link.entry_link.direct_target,
            original.entry_link.direct_target,
        )
        with self.assertRaises(ValueError):
            execute_filesystem_change(change)
        return result

    def test_relative_file_link_definition_round_trips_and_rebuilds(self):
        source = self.root / "source.txt"
        source.write_bytes(b"fixed")
        middle = self.root / "middle.txt"
        entry = self.root / "first.txt"
        self.make_symlink(middle, "source.txt")
        self.make_symlink(entry, "middle.txt")
        result = self.rebuild_saved_link(entry)
        self.assertEqual(result.created_link.entry_link.raw_target, "middle.txt")
        self.assertEqual(result.created_link.entry_link.direct_target, middle)
        self.assertEqual(entry.read_bytes(), b"fixed")

    def test_directory_symlink_definition_round_trips_and_rebuilds(self):
        target = self.root / "target"
        target.mkdir()
        source = target / "keep.txt"
        source.write_bytes(b"keep")
        entry = self.root / "directory_alias"
        self.make_symlink(entry, "target", directory=True)
        self.rebuild_saved_link(entry)
        self.assertEqual(os.readlink(entry), "target")
        self.assertEqual((entry / "keep.txt").read_bytes(), b"keep")
        self.assertEqual(source.read_bytes(), b"keep")

    def test_junction_definition_round_trips_with_native_rebuild(self):
        parent = self.root / "中文 空格目录"
        parent.mkdir()
        target = parent / "实际 目标"
        target.mkdir()
        source = target / "keep.txt"
        source.write_bytes(b"keep")
        entry = parent / "联接 入口"
        self.make_junction(entry, target)
        self.rebuild_saved_link(entry)
        self.assertEqual((entry / "keep.txt").read_bytes(), b"keep")
        self.assertEqual(source.read_bytes(), b"keep")

    def test_dangling_link_definition_rebuild_does_not_create_target(self):
        entry = self.root / "alias.txt"
        target = self.root / "missing.txt"
        self.make_symlink(entry, "missing.txt")
        with patch(
            "pathlib.Path.open",
            side_effect=AssertionError("定义重建不得读取目标正文"),
        ):
            self.rebuild_saved_link(entry)
        self.assertTrue(os.path.lexists(entry))
        self.assertFalse(target.exists())

    def test_link_definition_material_is_an_independent_regular_file(self):
        from core.content_publication import (
            cleanup_content_artifact, prepare_content_artifact, publish_content,
        )
        from core.file_observation import observe_file
        from core.link_paths import (
            decode_link_specification, encode_link_specification,
            specification_from_link,
        )

        entry = self.root / "alias.txt"
        self.make_symlink(entry, "missing.txt")
        definition = inspect_link_path(
            entry, follow_final_link=False,
        ).entry_link
        specification = specification_from_link(definition)
        raw = encode_link_specification(specification, entry_path=entry)
        material = self.root / "definition.json"
        artifact = prepare_content_artifact(observe_file(material), raw)
        try:
            result = publish_content(artifact)
            self.assertTrue(result.succeeded)
            observed = observe_file(material, include_bytes=True)
            self.assertEqual(observed.kind, "file")
            self.assertEqual(observed.link_count, 1)
            self.assertFalse(material.is_symlink())
            self.assertEqual(
                decode_link_specification(
                    observed.source_bytes, entry_path=entry,
                ),
                specification,
            )
        finally:
            cleanup_content_artifact(artifact)

    def test_link_definition_material_rejects_duplicate_and_unknown_fields(self):
        from core.link_paths import decode_link_specification

        entry = self.root / "alias.txt"
        duplicate = (
            b'{"format":"ZIPATCH_LINK_DEFINITION","version":1,'
            b'"kind":"file_symlink","raw_target":"a.txt",'
            b'"relative":true,"relative":false}'
        )
        unknown = (
            b'{"format":"ZIPATCH_LINK_DEFINITION","version":1,'
            b'"kind":"file_symlink","raw_target":"a.txt",'
            b'"relative":true,"extra":0}'
        )
        for raw in (duplicate, unknown):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    decode_link_specification(raw, entry_path=entry)

    def test_link_creation_does_not_overwrite_new_external_object(self):
        from core.file_observation import observe_file
        from core.filesystem_changes import (
            FilesystemChangeError, execute_filesystem_change,
            prepare_link_creation,
        )
        from core.link_paths import LinkSpecification

        entry = self.root / "alias.txt"
        change = prepare_link_creation(
            observe_file(entry),
            LinkSpecification("file_symlink", "missing.txt", True),
        )
        entry.write_bytes(b"external")
        with self.assertRaises(FilesystemChangeError) as captured:
            execute_filesystem_change(change)
        self.assertFalse(captured.exception.result.attempted)
        self.assertIsNone(captured.exception.result.created_link)
        self.assertEqual(entry.read_bytes(), b"external")

    def test_failed_junction_setup_retains_explicit_partial_creation(self):
        from core.file_observation import observe_file
        from core.filesystem_changes import (
            FilesystemChangeError, execute_filesystem_change,
            prepare_link_creation,
        )
        from core.link_paths import (
            specification_from_link,
        )

        target = self.root / "target"
        target.mkdir()
        reference = self.root / "reference"
        self.make_junction(reference, target)
        specification = specification_from_link(
            inspect_link_path(reference, follow_final_link=False).entry_link,
        )
        entry = self.root / "new_junction"
        change = prepare_link_creation(observe_file(entry), specification)

        def fail_after_directory_creation(path, direct_target, raw_target):
            self.assertEqual(path, entry)
            self.assertEqual(direct_target, target)
            self.assertEqual(raw_target, specification.raw_target)
            path.mkdir(exist_ok=False)
            raise OSError("模拟目录创建后重解析点设置失败")

        with patch(
            "core.filesystem_changes._create_junction_entry",
            side_effect=fail_after_directory_creation,
        ) as create_junction:
            with self.assertRaises(FilesystemChangeError) as captured:
                execute_filesystem_change(change)
            create_junction.assert_called_once_with(
                entry, target, specification.raw_target,
            )
        self.assertTrue(captured.exception.result.attempted)
        self.assertFalse(captured.exception.result.succeeded)
        self.assertIsNone(captured.exception.result.created_link)
        self.assertTrue(entry.is_dir())
        self.assertEqual(tuple(entry.iterdir()), ())
        self.assertTrue(target.is_dir())
        with self.assertRaises(ValueError):
            execute_filesystem_change(change)

    def test_complete_tree_removal_does_not_enter_directory_links(self):
        from core.file_observation import observe_tree
        from core.filesystem_changes import (
            execute_tree_removal, prepare_tree_removal,
        )

        scope = self.root / "scope"
        scope.mkdir()
        child = scope / "child"
        child.mkdir()
        (child / "ordinary.txt").write_bytes(b"ordinary")
        target = self.root / "outside"
        target.mkdir()
        protected_file = target / "keep.txt"
        protected_file.write_bytes(b"keep")

        junction = scope / "joined"
        self.make_junction(junction, target)
        directory_link = child / "directory_alias"
        self.make_symlink(directory_link, target, directory=True)
        dangling = child / "dangling.txt"
        self.make_symlink(dangling, "missing.txt")
        observed = observe_tree(
            scope, include_links=True, include_bytes=True,
            include_mtime=True,
        )
        removal = prepare_tree_removal(observed)
        result = execute_tree_removal(removal)
        self.assertTrue(result.succeeded)
        self.assertFalse(scope.exists())
        self.assertEqual(protected_file.read_bytes(), b"keep")
        self.assertEqual(
            sum(receipt.kind == "remove_link" for receipt in result.results), 3,
        )
        self.assertEqual(
            tuple((receipt.kind, receipt.target) for receipt in result.results),
            tuple((kind, path) for kind, _, path in removal.steps),
        )
        with self.assertRaises(ValueError):
            execute_tree_removal(removal)

    def test_tree_removal_preserves_external_hardlink_content(self):
        from core.file_observation import observe_tree
        from core.filesystem_changes import (
            execute_tree_removal, prepare_tree_removal,
        )

        scope = self.root / "scope"
        scope.mkdir()
        first = scope / "a.txt"
        first.write_bytes(b"shared")
        second = scope / "b.txt"
        external = self.root / "external.txt"
        os.link(first, second)
        os.link(first, external)
        link = scope / "alias.txt"
        self.make_symlink(link, "a.txt")
        observed = observe_tree(
            scope, include_links=True, include_bytes=True,
            include_mtime=True,
        )
        result = execute_tree_removal(prepare_tree_removal(observed))
        self.assertTrue(result.succeeded)
        self.assertFalse(scope.exists())
        self.assertEqual(external.read_bytes(), b"shared")
        self.assertEqual(external.lstat().st_nlink, 1)

    def test_tree_removal_refuses_retargeting_before_any_step(self):
        from core.file_observation import observe_tree
        from core.filesystem_changes import (
            TreeRemovalError, execute_tree_removal, prepare_tree_removal,
        )

        scope = self.root / "scope"
        scope.mkdir()
        ordinary = scope / "a.txt"
        ordinary.write_bytes(b"keep")
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        link = scope / "z_alias.txt"
        self.make_symlink(link, first)
        removal = prepare_tree_removal(observe_tree(
            scope, include_links=True, include_bytes=True,
            include_mtime=True,
        ))
        os.unlink(link)
        os.symlink(second, link)
        with self.assertRaises(TreeRemovalError) as captured:
            execute_tree_removal(removal)
        self.assertEqual(captured.exception.result.results, ())
        self.assertFalse(captured.exception.result.attempted)
        self.assertEqual(ordinary.read_bytes(), b"keep")
        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")

    def test_tree_removal_stops_on_external_member_after_successful_prefix(self):
        from core.file_observation import observe_tree
        from core.filesystem_changes import (
            TreeRemovalError, execute_tree_removal, prepare_tree_removal,
        )

        scope = self.root / "scope"
        scope.mkdir()
        ordinary = scope / "a.txt"
        ordinary.write_bytes(b"ordinary")
        link = scope / "z_alias.txt"
        self.make_symlink(link, "missing.txt")
        removal = prepare_tree_removal(observe_tree(
            scope, include_links=True, include_bytes=True,
            include_mtime=True,
        ))
        calls = 0
        external = scope / "external.txt"

        def add_external_before_second_step():
            nonlocal calls
            calls += 1
            if calls == 2:
                external.write_bytes(b"external")

        with self.assertRaises(TreeRemovalError) as captured:
            execute_tree_removal(
                removal, before_step=add_external_before_second_step,
            )
        receipts = captured.exception.result.results
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0].succeeded)
        self.assertEqual(receipts[0].target, ordinary)
        self.assertFalse(ordinary.exists())
        self.assertTrue(os.path.lexists(link))
        self.assertEqual(external.read_bytes(), b"external")

    def test_task_projection_keeps_link_records_and_identity_constraints(self):
        from core.file_observation import observe_tree, recheck_tree
        from core.task_observation import TaskObservationState

        scope = self.root / "scope"
        scope.mkdir()
        (scope / "ordinary.txt").write_bytes(b"ordinary")
        link = scope / "alias.txt"
        self.make_symlink(link, "missing.txt")
        observed = observe_tree(
            scope, include_links=True, include_bytes=True,
            include_mtime=True,
        )
        state = TaskObservationState((observed,))
        projected = state.project(observed, ())
        self.assertEqual(projected.links, observed.links)
        self.assertEqual(projected.content_records, observed.content_records)
        self.assertEqual(projected.integrity, observed.integrity)
        recheck_tree(projected)

    def test_explicit_final_link_policy_is_required(self):
        with self.assertRaises(TypeError):
            inspect_link_path(self.root, follow_final_link="yes")


if __name__ == "__main__":
    unittest.main()
