# -*- coding: utf-8 -*-

"""临时目录中的真实Windows路径、树复制、正式保护及主动恢复。

不mock复制、材料发布或恢复业务，不操作项目工作文件。
只有Windows软链接创建权限不足明确skip；Junction失败仍为失败。
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import (
    execute_filesystem_change, execute_tree_copy, execute_tree_removal,
    prepare_link_creation, prepare_tree_copy, prepare_tree_removal,
)
from core.link_paths import (
    LinkSpecification, inspect_link_path, recheck_link_dependencies,
    recheck_link_path, recheck_path_dependencies,
)
from core.protection_projection import (
    FixedEffect, FixedUnit, absent_state, build_protection_projection,
    observation_state,
)
from core.recovery_protection import (
    RecoveryProtectionSession, read_recovery_evidence,
)
from services.patch_restore import BackupRestoreExecutor
from services.source_recheck import recheck_source_paths


@unittest.skipUnless(os.name == "nt", "真实链接链路仅适用于本项目Windows环境")
class WindowsLinkRecoveryTests(unittest.TestCase):
    def create_link(self, path, kind, raw_target, relative=False):
        specification = LinkSpecification(kind, raw_target, relative)
        try:
            return execute_filesystem_change(prepare_link_creation(
                observe_file(path), specification,
            )).created_link
        except OSError as error:
            # 正式步骤错误持有原始系统错误；只识别明确权限不足，
            # 不把定义不符、成果核验失败或其他IO错误伪装成跳过。
            receipt = getattr(error, "result", None)
            cause = error if receipt is None else receipt.error
            if kind != "junction" and getattr(cause, "winerror", None) == 1314:
                self.skipTest(
                    "Windows未授予软链接创建权限；请启用开发者模式"
                    "或在具有该权限的终端运行。本项未验证，不计为通过。"
                )
            raise

    def fixture(self, root, kind):
        source = root / "source"
        source.mkdir()
        (source / "empty").mkdir()
        (source / "a.txt").write_bytes(b"original\r\n")
        external = root / "external"
        external.mkdir()
        (external / "sentinel.txt").write_bytes(b"external must survive")
        if kind == "file_symlink":
            self.create_link(source / "link", kind, "a.txt", True)
            self.create_link(
                source / "dangling", kind, "missing.txt", True,
            )
        elif kind == "directory_symlink":
            # 初始现场由系统接口建立，再读取Windows实际保存的定义。
            # 传入表达不等于readlink返回表达，不能把它直接当恢复权威。
            # 后续复制、正式保护和恢复仍全部调用生产核心，
            # 严格保持这里取得的真实原定义，不放宽定义比较。
            entry = source / "link"
            try:
                os.symlink(str(external), entry, target_is_directory=True)
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest(
                        "Windows未授予软链接创建权限；请启用开发者模式"
                        "或在具有该权限的终端运行。本项未验证，不计为通过。"
                    )
                raise
            initial = inspect_link_path(entry, follow_final_link=False)
            self.assertEqual(initial.kind, "directory_symlink")
            self.assertEqual(initial.actual_path, entry)
            self.assertEqual(initial.followed_links, ())
            self.assertIsNotNone(initial.entry_link)
            self.assertEqual(initial.entry_link.direct_target, external)
            self.assertFalse(initial.entry_link.relative)
            self.assertEqual(initial.entry_link.raw_target, os.readlink(entry))
        else:
            self.create_link(
                source / "link", kind, "\\\\?\\" + str(external),
            )
        observed = observe_tree(
            source, include_bytes=True, include_mtime=True, include_links=True,
        )
        return source, external, observed

    def assert_external_untouched(self, external):
        self.assertEqual(
            (external / "sentinel.txt").read_bytes(),
            b"external must survive",
        )
        self.assertEqual(
            tuple(path.name for path in external.iterdir()),
            ("sentinel.txt",),
        )

    def copy_case(self, kind):
        with TemporaryDirectory(prefix="Zipatch_link_copy_") as folder:
            root = Path(folder)
            source, external, observed = self.fixture(root, kind)
            target = root / "copied"
            copy = prepare_tree_copy(observed, observe_file(target))
            result = execute_tree_copy(copy)
            self.assertTrue(result.succeeded)
            actual = copy.final_observation
            self.assertEqual(actual.content_records, observed.content_records)
            self.assertEqual(actual.file_times, observed.file_times)
            self.assertEqual(
                tuple(position for position, _ in actual.links),
                tuple(position for position, _ in observed.links),
            )
            self.assertTrue(all(receipt.succeeded for receipt in copy.results))
            self.assertEqual((target / "a.txt").read_bytes(), b"original\r\n")
            self.assertEqual((source / "a.txt").read_bytes(), b"original\r\n")
            self.assert_external_untouched(external)

    def recovery_case(self, kind):
        with TemporaryDirectory(prefix="Zipatch_link_recovery_") as folder:
            root = Path(folder)
            source, external, observed = self.fixture(root, kind)
            original, times = observation_state(observed)
            projection = build_protection_projection((
                FixedUnit(
                    ("remove_original_tree",),
                    (FixedEffect(observed, absent_state()),),
                ),
            ), forbidden_roots=())
            session = RecoveryProtectionSession(
                projection, root / "original_backup",
                "windows_link_regression", "patch",
                material_base=root,
            )
            session.protect_all()
            self.assertTrue(session.backup_completed)
            self.assertEqual(len(session.material_results), 1)
            material = next(iter(session.material_results.values()))
            self.assertTrue(material.succeeded)
            self.assertEqual(material.observation.links, ())
            self.assertTrue(all(
                member.link_count == 1
                for _, member in material.observation.files
            ))
            self.assert_external_untouched(external)

            # 真实移除只消费原固定树；目录链接目标不属于删除范围。
            session.begin(projection.units[0].unit_id)
            removal = prepare_tree_removal(observed)
            removed = execute_tree_removal(removal)
            self.assertTrue(removed.succeeded)
            session.complete(projection.units[0].unit_id, removal.results)
            self.assertEqual(observe_file(source).kind, "absent")
            self.assert_external_untouched(external)

            # 严格从磁盘读取依据，走主动恢复完整入口；
            # 包括新的还原前强制保护、started及完成事实发布。
            evidence = read_recovery_evidence(session.task_root)
            executor = BackupRestoreExecutor(
                evidence,
                backup_dir=str(root / "restore_protection"),
                backup_project_root=str(root),
            )
            self.assertEqual(len(executor.plan.actions), 1)
            self.assertTrue(executor.plan.actions[0].execute)
            executor.confirm_execution()
            executor.apply()
            result = executor.execution_result
            self.assertTrue(result.succeeded)
            self.assertTrue(result.complete_original_state)
            self.assertTrue(executor.execution.backup_completed)
            actual = observe_tree(
                source, include_bytes=True, include_mtime=True,
                include_links=True,
            )
            restored, restored_times = observation_state(actual)
            self.assertEqual(restored, original)
            self.assertEqual(restored_times, times)
            self.assertEqual((source / "a.txt").read_bytes(), b"original\r\n")
            self.assertTrue(all(receipt.succeeded for receipt in result.results))
            self.assertEqual(
                len({id(receipt) for receipt in result.results}),
                len(result.results),
            )
            self.assert_external_untouched(external)
            # 新保护也必须能从磁盘严格读取，而非只有内存成功标志。
            read_recovery_evidence(executor.backup_root)

    def test_file_symlink_tree_copy(self):
        self.copy_case("file_symlink")

    def test_directory_symlink_tree_copy(self):
        self.copy_case("directory_symlink")

    def test_junction_tree_copy(self):
        self.copy_case("junction")

    def test_file_symlink_formal_backup_and_active_recovery(self):
        self.recovery_case("file_symlink")

    def test_directory_symlink_formal_backup_and_active_recovery(self):
        self.recovery_case("directory_symlink")

    def test_junction_formal_backup_and_active_recovery(self):
        self.recovery_case("junction")

    def test_changed_route_stops_without_reparsing_new_target(self):
        with TemporaryDirectory(prefix="Zipatch_path_drift_") as folder:
            root = Path(folder)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "a.txt").write_bytes(b"first")
            (second / "a.txt").write_bytes(b"second")
            route = root / "route"
            raw_first = "\\\\?\\" + str(first)
            raw_second = "\\\\?\\" + str(second)
            created = self.create_link(route, "junction", raw_first)
            access = inspect_link_path(
                route / "a.txt", follow_final_link=True,
            )
            from core.filesystem_changes import prepare_link_removal
            execute_filesystem_change(prepare_link_removal(created))
            self.create_link(route, "junction", raw_second)

            for checker in (
                recheck_link_path, recheck_link_dependencies,
                recheck_path_dependencies,
                lambda expected: recheck_source_paths((expected,)),
            ):
                with self.subTest(checker=checker):
                    with patch(
                        "core.link_paths.inspect_link_path",
                        side_effect=AssertionError("不得重新解析漂移后的路线"),
                    ):
                        with self.assertRaises((OSError, ValueError)):
                            checker(access)
            self.assertEqual((first / "a.txt").read_bytes(), b"first")
            self.assertEqual((second / "a.txt").read_bytes(), b"second")

    def test_normal_file_replacement_preserves_dependency_only_qualification(self):
        with TemporaryDirectory(prefix="Zipatch_path_content_") as folder:
            root = Path(folder)
            target = root / "a.txt"
            target.write_bytes(b"old")
            access = inspect_link_path(target, follow_final_link=True)
            replacement = root / "replacement.txt"
            replacement.write_bytes(b"new")
            os.replace(replacement, target)
            with patch(
                "core.link_paths.inspect_link_path",
                side_effect=AssertionError("依赖复核不重新解析入口"),
            ):
                self.assertIs(recheck_link_dependencies(access), access)
                self.assertIs(recheck_path_dependencies(access), access)
                with self.assertRaises(ValueError):
                    recheck_link_path(access)

    def test_missing_parents_accept_plain_creation_but_reject_new_link(self):
        with TemporaryDirectory(prefix="Zipatch_path_parents_") as folder:
            root = Path(folder)
            parent = root / "new"
            target = parent / "a.txt"
            access = inspect_link_path(target, follow_final_link=True)
            parent.mkdir()
            self.assertIs(recheck_link_dependencies(access), access)
            self.assertIs(recheck_path_dependencies(access), access)
            with self.assertRaises(ValueError):
                recheck_link_path(access)

            parent.rmdir()
            external = root / "external"
            external.mkdir()
            self.create_link(
                parent, "junction", "\\\\?\\" + str(external),
            )
            with patch(
                "core.link_paths.inspect_link_path",
                side_effect=AssertionError("不得读取后来出现的新链接路线"),
            ):
                with self.assertRaises(ValueError):
                    recheck_link_dependencies(access)
                with self.assertRaises(ValueError):
                    recheck_path_dependencies(access)
            self.assertEqual(tuple(external.iterdir()), ())


if __name__ == "__main__":
    unittest.main()