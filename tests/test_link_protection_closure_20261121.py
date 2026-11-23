# -*- coding: utf-8 -*-

"""正式保护、严格依据、主动恢复及固定范围自动回滚集成验证。"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.automatic_rollback_scope import validate_automatic_rollback_scope
from core.content_publication import (
    ContentPublicationError, cleanup_content_artifact,
    dispose_content_artifact, prepare_content_artifact, publish_content,
)
from core.execution_state import ExecutionState
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import (
    FilesystemChangeError, execute_filesystem_change, execute_tree_removal,
    prepare_file_removal, prepare_link_creation, prepare_link_removal,
    prepare_tree_removal,
)
from core.link_paths import inspect_link_path, specification_from_link
from core.protection_projection import (
    FixedEffect, FixedUnit, absent_state, build_protection_projection,
    state_from_bytes, state_from_observation,
)
from core.recovery_document import decode_document
from core.recovery_execution import RecoveryExecution, RecoveryExecutionError
from core.recovery_protection import (
    ProtectionPublicationError, RecoveryProtectionSession,
    read_protection_evidence, read_recovery_evidence,
    recheck_recovery_evidence,
)
from services.patch_executor import PatchExecutor
from services.patch_restore import (
    BackupRestoreExecutionError, BackupRestoreExecutor,
)
from services.recovery import plan_restore


@unittest.skipUnless(os.name == "nt", "保护恢复闭环使用Windows")
class LinkProtectionClosureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_protection_closure_",
        )
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "private.bin").write_bytes(b"outside-private")
        self.links = []
        self.counter = 0

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, kind="file_symlink"):
        directory = kind != "file_symlink"
        if kind == "junction":
            subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(entry), str(target)],
                check=True, capture_output=True,
            )
        else:
            # Windows测试环境必须具有本批实际需要的链接权限；
            # 权限错误是测试失败，不用跳过掩盖未验证能力。
            os.symlink(target, entry, target_is_directory=directory)
        self.links.append((entry, directory))
        return inspect_link_path(entry, follow_final_link=False)

    def observe(self, target):
        access = inspect_link_path(target, follow_final_link=False)
        if access.entry_link is not None:
            return access
        if access.kind == "directory":
            return observe_tree(
                target, include_bytes=True, include_mtime=True,
                include_links=True,
            )
        return observe_file(
            target, include_bytes=True, include_digest=True,
            include_mtime=True,
        )

    def session(self, observed, desired=None):
        self.counter += 1
        projection = build_protection_projection(
            (FixedUnit(
                ("work001",),
                (FixedEffect(
                    observed, absent_state() if desired is None else desired,
                ),),
            ),),
            forbidden_roots=(self.project,),
        )
        session = RecoveryProtectionSession(
            projection,
            self.backups / f"task{self.counter}",
            f"task{self.counter}", "patch", material_base=self.project,
        )
        session.protect_all()
        self.assertTrue(session.backup_completed)
        self.assertTrue((session.task_root / "backup.complete").is_file())
        return session

    def remove(self, observed):
        if hasattr(observed, "entry_link"):
            return (execute_filesystem_change(
                prepare_link_removal(observed),
            ),)
        if hasattr(observed, "directories"):
            return execute_tree_removal(
                prepare_tree_removal(observed),
            ).results
        return (execute_filesystem_change(prepare_file_removal(observed)),)

    def material_path(self, session):
        return session.material_results["o000001"].target

    def executor(self, session):
        return BackupRestoreExecutor(
            read_recovery_evidence(session.task_root),
            str(self.backups), backup_project_root=str(self.project),
        )

    def assert_outside_unchanged(self):
        self.assertEqual(
            (self.outside / "private.bin").read_bytes(), b"outside-private",
        )
        self.assertEqual(
            {entry.name for entry in self.outside.iterdir()}, {"private.bin"},
        )

    def test_three_link_kinds_formal_save_remove_and_active_restore(self):
        for kind, target in (
            ("file_symlink", self.outside / "private.bin"),
            ("directory_symlink", self.outside),
            ("junction", self.outside),
        ):
            with self.subTest(kind=kind):
                entry = self.project / kind
                observed = self.link(entry, target, kind)
                specification = specification_from_link(observed.entry_link)
                # 正式备份只能打开普通材料，不能打开根外目标正文。
                original_open = Path.open

                def guarded_open(path, *args, **keywords):
                    if path == self.outside or self.outside in path.parents:
                        raise AssertionError("正式保存或恢复不得读取根外目标")
                    return original_open(path, *args, **keywords)

                with patch("pathlib.Path.open", new=guarded_open):
                    session = self.session(observed)
                    material = observe_file(
                        self.material_path(session), include_bytes=True,
                    )
                    self.assertEqual(material.link_count, 1)
                    self.assertFalse(material.path.is_symlink())
                    self.assertEqual(
                        json.loads(material.source_bytes)["raw_target"],
                        specification.raw_target,
                    )
                    unit_id = session.projection.units[0].unit_id
                    session.begin(unit_id)
                    receipts = self.remove(observed)
                    session.complete(unit_id, receipts)
                    self.assertFalse(os.path.lexists(entry))
                    executor = self.executor(session)
                    self.assertIn(repr(specification.raw_target), executor.preview_text)
                    self.assertIn("根外引用", executor.preview_text)
                    with self.assertRaises(ValueError):
                        executor.apply()
                    self.assertFalse(os.path.lexists(entry))
                    executor.confirm_execution()
                    executor.apply()
                    actual = inspect_link_path(entry, follow_final_link=False)
                    self.assertEqual(
                        specification_from_link(actual.entry_link), specification,
                    )
                    self.assertTrue(executor.execution_result.succeeded)
                    self.assertTrue(executor.execution.backup_completed)
                self.assert_outside_unchanged()

    def test_mixed_tree_formal_restore_preserves_definitions_and_times(self):
        scope = self.project / "mixed"
        scope.mkdir()
        (scope / "empty").mkdir()
        (scope / "body.bin").write_bytes(b"\x00ordinary")
        modified = 1700000000000000000
        os.utime(scope / "body.bin", ns=(modified, modified))
        # 中间入口在根外；保存及恢复只保留直接引用，不解析最终目标。
        intermediate = self.root / "intermediate.txt"
        self.link(intermediate, self.outside / "private.bin")
        self.link(scope / "file_alias", intermediate)
        self.link(scope / "directory_alias", self.outside, "directory_symlink")
        self.link(scope / "junction_alias", self.outside, "junction")
        self.link(scope / "dangling", "missing.txt")
        observed = self.observe(scope)
        desired = state_from_observation(observed)
        session = self.session(observed)
        material = observe_tree(
            self.material_path(session), include_bytes=True, include_mtime=True,
        )
        self.assertFalse(material.links)
        self.assertEqual(len(material.files), 5)
        self.remove(observed)
        executor = self.executor(session)
        executor.confirm_execution()
        executor.apply()
        actual = self.observe(scope)
        self.assertEqual(state_from_observation(actual), desired)
        self.assertEqual(actual.file_times, observed.file_times)
        self.assertEqual((scope / "body.bin").read_bytes(), b"\x00ordinary")
        file_alias = dict(actual.links)["file_alias"]
        self.assertEqual(file_alias.entry_link.direct_target, intermediate)
        self.assertEqual(
            file_alias.entry_link.raw_target,
            dict(observed.links)["file_alias"].entry_link.raw_target,
        )
        self.assertFalse((scope / "missing.txt").exists())
        self.assertIn(scope / "body.bin", executor.execution_result.rebuilt_files)
        self.assert_outside_unchanged()

    def test_current_link_is_protected_before_restoring_ordinary_file(self):
        target = self.project / "body.txt"
        target.write_bytes(b"original")
        session = self.session(self.observe(target))
        target.unlink()
        observed = self.link(target, self.outside / "private.bin")
        executor = self.executor(session)
        executor.confirm_execution()
        executor.apply()
        self.assertEqual(target.read_bytes(), b"original")
        protection = read_recovery_evidence(executor.backup_root)
        original = protection.document.value["objects"][0]["original"]
        self.assertEqual(original["kind"], "file_symlink")
        self.assertEqual(
            original["link_definitions"][0]["raw_target"],
            observed.entry_link.raw_target,
        )
        self.assert_outside_unchanged()

    def test_damaged_definition_material_stops_formal_reader(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        self.material_path(session).write_bytes(b'{"wrong":"definition"}')
        with self.assertRaises(ValueError):
            read_recovery_evidence(session.task_root)
        self.assertEqual(self.observe(observed.path), observed)

    def test_extra_material_outside_referenced_object_is_rejected(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        (session.task_root / "files" / "unreferenced.bin").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            read_recovery_evidence(session.task_root)
        self.assertEqual(self.observe(observed.path), observed)

    def test_extra_empty_material_directory_is_rejected(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        (session.task_root / "files" / "unreferenced").mkdir()
        with self.assertRaises(ValueError):
            read_recovery_evidence(session.task_root)

    def test_extra_material_after_reader_stops_execution(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        self.remove(observed)
        executor = self.executor(session)
        executor.confirm_execution()
        (session.task_root / "files" / "extra.bin").write_bytes(b"extra")
        with self.assertRaises(BackupRestoreExecutionError):
            executor.apply()
        self.assertFalse(os.path.lexists(observed.path))
        self.assertEqual(executor.execution.results, ())
        self.assertTrue(session.task_root.is_dir())

    def test_manifest_duplicate_and_unknown_fields_are_rejected(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        raw = (session.task_root / "recovery.json").read_bytes()
        duplicate = raw.replace(
            b'"version": 4,', b'"version": 4, "version": 4,', 1,
        )
        with self.assertRaises(ValueError):
            decode_document(duplicate)
        value = json.loads(raw)
        value["objects"][0]["original"]["link_definitions"][0]["unknown"] = 1
        with self.assertRaises(ValueError):
            decode_document(json.dumps(value).encode("utf-8"))
        value = json.loads(raw)
        value["objects"][0]["original"]["link_definitions"][0]["raw_target"] = "other"
        with self.assertRaises(ValueError):
            decode_document(json.dumps(value).encode("utf-8"))

    def test_new_scene_object_after_plan_is_never_overwritten(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        self.remove(observed)
        evidence = read_protection_evidence(session)
        plan = plan_restore(evidence)
        execution = RecoveryExecution(evidence, plan.actions)
        observed.path.write_bytes(b"external")
        with self.assertRaises(RecoveryExecutionError):
            execution.execute()
        self.assertEqual(execution.results, ())
        self.assertEqual(observed.path.read_bytes(), b"external")
        self.assertTrue(session.task_root.is_dir())

    def test_all_backup_materials_precede_any_fixed_work(self):
        first = self.link(self.project / "first", "missing1.txt")
        second = self.link(self.project / "second", "missing2.txt")
        projection = build_protection_projection(
            tuple(
                FixedUnit(
                    (f"remove{number}",),
                    (FixedEffect(observed, absent_state()),),
                )
                for number, observed in enumerate((first, second), 1)
            ),
            forbidden_roots=(self.project,),
        )
        session = RecoveryProtectionSession(
            projection, self.backups / "failed", "failed", "patch",
            material_base=self.project,
        )
        from core.recovery_protection import publish_internal_material
        calls = []

        def fail_second(material):
            calls.append(material)
            if len(calls) == 2:
                raise OSError("injected second material failure")
            return publish_internal_material(material)

        with patch(
            "core.recovery_protection.publish_internal_material",
            side_effect=fail_second,
        ):
            with self.assertRaises(ProtectionPublicationError):
                session.protect_all()
        self.assertEqual(len(calls), 2)
        self.assertFalse(session.backup_completed)
        self.assertFalse(session.task_root.exists())
        self.assertIn("已清理", session.backup_cleanup_report)
        self.assertEqual(self.observe(first.path), first)
        self.assertEqual(self.observe(second.path), second)

    def test_backup_cleanup_failure_is_reported_without_work(self):
        observed = self.link(self.project / "alias", "missing.txt")
        projection = build_protection_projection(
            (FixedUnit(
                ("remove001",), (FixedEffect(observed, absent_state()),),
            ),), forbidden_roots=(self.project,),
        )
        session = RecoveryProtectionSession(
            projection, self.backups / "failed-cleanup",
            "failed-cleanup", "patch", material_base=self.project,
        )
        with patch(
            "core.recovery_protection.prepare_internal_material",
            side_effect=OSError("injected backup failure"),
        ), patch(
            "core.recovery_protection.execute_tree_removal",
            create=True,
        ):
            # 清理函数局部导入共享核心，故直接在真实拥有模块注入故障。
            with patch(
                "core.filesystem_changes.execute_tree_removal",
                side_effect=OSError("injected cleanup failure"),
            ):
                with self.assertRaises(ProtectionPublicationError):
                    session.protect_all()
        self.assertTrue(session.task_root.exists())
        self.assertIn("人工", session.backup_cleanup_report)
        self.assertIsNotNone(session.backup_cleanup_error)
        self.assertEqual(self.observe(observed.path), observed)

    def test_automatic_rollback_restores_real_removed_prefix_only(self):
        scope = self.project / "prefix"
        scope.mkdir()
        first = self.link(scope / "a", "missing-a")
        second = self.link(scope / "b", "missing-b")
        observed = self.observe(scope)
        session = self.session(observed)
        removed = execute_filesystem_change(prepare_link_removal(first))
        # 后一调用明确没有改变；前序正常移除不能被抹去。
        prepared = prepare_link_removal(second)
        original_unlink = Path.unlink

        def fail_second(path, *args, **keywords):
            if path == second.path:
                raise OSError("injected unlink failure")
            return original_unlink(path, *args, **keywords)

        with patch("pathlib.Path.unlink", new=fail_second):
            with self.assertRaises(FilesystemChangeError) as caught:
                execute_filesystem_change(prepared)
        results = (removed, caught.exception.result)
        evidence = read_protection_evidence(session)
        plan = plan_restore(evidence)
        validate_automatic_rollback_scope(
            session.projection, plan.actions, results=results,
        )
        execution = RecoveryExecution(evidence, plan.actions)
        result = execution.execute()
        self.assertTrue(result.succeeded)
        self.assertIsNone(execution.protection)
        self.assertEqual(
            state_from_observation(self.observe(scope)),
            state_from_observation(observed),
        )
        self.assertTrue(session.task_root.is_dir())

    def test_unknown_failed_link_creation_cannot_authorize_scene_removal(self):
        entry = self.project / "new"
        source = self.link(self.project / "definition", "missing")
        desired = state_from_observation(source)
        expected = observe_file(entry)
        session = self.session(expected, desired)
        specification = desired.link_definitions[0][1]
        step = prepare_link_creation(expected, specification)
        with patch(
            "core.filesystem_changes.specification_from_link",
            side_effect=OSError("injected post-create verification failure"),
        ):
            with self.assertRaises(FilesystemChangeError) as caught:
                execute_filesystem_change(step)
        self.links.append((entry, False))
        self.assertTrue(caught.exception.result.attempted)
        self.assertIsNone(caught.exception.result.created_link)
        evidence = read_protection_evidence(session)
        plan = plan_restore(evidence)
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(
                session.projection, plan.actions,
                results=(caught.exception.result,),
            )
        self.assertTrue(os.path.lexists(entry))
        self.assertTrue(session.task_root.is_dir())

    def test_unattempted_original_link_change_stops_automatic_rollback(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        self.remove(observed)
        self.link(observed.path, "different.txt")
        evidence = read_protection_evidence(session)
        plan = plan_restore(evidence)
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(
                session.projection, plan.actions, results=(),
            )
        self.assertEqual(os.readlink(observed.path), "different.txt")
        self.assertTrue(session.task_root.is_dir())

    def test_failed_step_cannot_be_followed_by_another_receipt(self):
        target = self.project / "body"
        target.write_bytes(b"before")
        observed = self.observe(target)
        session = self.session(observed, state_from_bytes(b"after"))
        artifact = prepare_content_artifact(observed, b"after")
        with patch(
            "core.content_publication.os.replace",
            side_effect=OSError("injected replace failure"),
        ):
            with self.assertRaises(ContentPublicationError) as caught:
                publish_content(artifact)
        evidence = read_protection_evidence(session)
        plan = plan_restore(evidence)
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(
                session.projection, plan.actions,
                results=(caught.exception.result, caught.exception.result),
            )
        self.assertFalse(cleanup_content_artifact(artifact))
        with self.assertRaises(ValueError):
            dispose_content_artifact(artifact)
        self.assertTrue(artifact.path.exists())

    def test_active_restore_failure_rolls_back_without_second_backup(self):
        target = self.project / "body"
        target.write_bytes(b"original")
        session = self.session(self.observe(target))
        target.write_bytes(b"current-before-restore")
        executor = self.executor(session)
        executor.confirm_execution()
        original_complete = RecoveryProtectionSession.complete

        def fail_fact(protection, unit_id, results):
            if protection.task_root == executor.backup_root:
                raise OSError("injected restore completed-fact failure")
            return original_complete(protection, unit_id, results)

        before_tasks = {entry.name for entry in self.backups.iterdir()}
        with patch.object(
            RecoveryProtectionSession, "complete", new=fail_fact,
        ):
            with self.assertRaises(BackupRestoreExecutionError):
                executor.apply()
        self.assertEqual(executor.rollback_status, "completed")
        self.assertEqual(target.read_bytes(), b"current-before-restore")
        after_tasks = {entry.name for entry in self.backups.iterdir()}
        self.assertEqual(after_tasks - before_tasks, {executor.backup_root.name})
        self.assertTrue(session.task_root.is_dir())
        self.assertTrue(executor.backup_root.is_dir())

    def test_active_restore_rollback_failure_keeps_both_backups(self):
        target = self.project / "body"
        target.write_bytes(b"original")
        session = self.session(self.observe(target))
        target.write_bytes(b"current-before-restore")
        executor = self.executor(session)
        executor.confirm_execution()

        def fail_fact(protection, unit_id, results):
            raise OSError("injected completed-fact failure")

        with patch.object(
            RecoveryProtectionSession, "complete", new=fail_fact,
        ), patch(
            "core.automatic_rollback_scope.validate_automatic_rollback_scope",
            side_effect=ValueError("injected unreliable rollback scope"),
        ):
            with self.assertRaises(BackupRestoreExecutionError) as caught:
                executor.apply()
        self.assertEqual(executor.rollback_status, "failed")
        self.assertIs(caught.exception.executor, executor)
        self.assertIsInstance(executor.rollback_error, ValueError)
        self.assertEqual(
            str(executor.rollback_error), "injected unreliable rollback scope",
        )
        self.assertTrue(executor.rollback_report)
        report = str(caught.exception)
        self.assertIn(executor.rollback_report, report)
        self.assertIn(str(session.task_root), report)
        self.assertIn(str(executor.backup_root), report)
        self.assertTrue(session.task_root.is_dir())
        self.assertTrue(executor.backup_root.is_dir())
        # 两份备份必须仍可严格读取，不能仅凭目录存在认定保留成功。
        source_evidence = read_recovery_evidence(session.task_root)
        before_evidence = read_recovery_evidence(executor.backup_root)
        self.assertEqual(
            source_evidence.materials["o000001"].source_bytes, b"original",
        )
        self.assertEqual(
            before_evidence.materials["o000001"].source_bytes,
            b"current-before-restore",
        )
        self.assertFalse(executor.execution_result.succeeded)
        self.assertTrue(executor.consumed)
        self.assertEqual(target.read_bytes(), b"original")
        with self.assertRaises(ValueError):
            executor.apply()
        self.assertEqual(target.read_bytes(), b"original")

    def test_existing_hardlink_body_restore_keeps_shared_identity(self):
        target = self.project / "body"
        peer = self.root / "peer"
        target.write_bytes(b"original")
        os.link(target, peer)
        original_identity = self.observe(target).object_identity
        session = self.session(self.observe(target))
        target.write_bytes(b"changed")
        executor = self.executor(session)
        executor.confirm_execution()
        executor.apply()
        actual = self.observe(target)
        self.assertEqual(actual.object_identity, original_identity)
        self.assertEqual(actual.link_count, 2)
        self.assertEqual(peer.read_bytes(), b"original")
        publications = [
            receipt for receipt in executor.execution_result.results
            if hasattr(receipt, "mechanism")
        ]
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0].mechanism, "in_place")

    def test_content_access_dependency_is_not_a_recovery_object(self):
        from core.file_observation import observe_accessed_file

        target = self.project / "body"
        target.write_bytes(b"original")
        entry = self.project / "alias"
        self.link(entry, target)
        access = inspect_link_path(entry, follow_final_link=True)
        observed = observe_accessed_file(
            access, include_bytes=True, include_digest=True, include_mtime=True,
        )
        projection = build_protection_projection(
            (FixedUnit(
                ("edit001",),
                (FixedEffect(observed, state_from_bytes(b"after")),),
            ),), forbidden_roots=(self.project,),
        )
        self.assertEqual(
            tuple(member.target for member in projection.objects), (target,),
        )
        self.assertNotIn(entry, tuple(member.target for member in projection.objects))

    def test_unapproved_patch_link_entry_remains_blocked(self):
        self.link(self.project / "alias", "missing.txt")
        executor = PatchExecutor(
            None, self.project, allow_delete=True,
            backup_dir=str(self.backups), normalize_text_newlines=False,
        )
        operation = {
            "op": "delete_file", "id": "delete001", "path": "alias",
        }
        result = executor.preview({
            "version": "3.0", "operations": [operation],
        })
        self.assertTrue(result[2])
        self.assertIsNone(executor.plan)
        self.assertIsNone(executor.protection)
        self.assertTrue(os.path.lexists(self.project / "alias"))

    def test_fixed_evidence_recheck_does_not_guess_repaired_material(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        evidence = read_recovery_evidence(session.task_root)
        material = self.material_path(session)
        original = material.read_bytes()
        material.unlink()
        material.write_bytes(original)
        with self.assertRaises(ValueError):
            recheck_recovery_evidence(evidence)

    def test_rollback_reader_rejects_same_bytes_replaced_definition_material(self):
        observed = self.link(self.project / "alias", "missing.txt")
        session = self.session(observed)
        material = self.material_path(session)
        original = material.read_bytes()
        original_identity = observe_file(material).object_identity
        saved = self.root / "saved-original-definition.bin"
        # 保留原对象，确保测试不依赖删除后文件编号是否被系统复用。
        material.rename(saved)
        material.write_bytes(original)
        self.assertNotEqual(
            observe_file(material).object_identity, original_identity,
        )
        self.remove(observed)
        with self.assertRaises(ValueError):
            read_protection_evidence(session)
        self.assertFalse(os.path.lexists(observed.path))
        self.assertEqual(material.read_bytes(), original)
        self.assertEqual(saved.read_bytes(), original)
        self.assertTrue(session.task_root.is_dir())
        self.assertTrue((session.task_root / "backup.complete").is_file())
        self.assert_outside_unchanged()

    def test_restore_rollback_rejects_same_bytes_replaced_original_material(self):
        target = self.project / "body"
        target.write_bytes(b"original")
        session = self.session(self.observe(target))
        target.write_bytes(b"current-before-restore")
        executor = self.executor(session)
        executor.confirm_execution()
        saved = self.root / "saved-restore-protection.bin"
        replaced = []

        def replace_material_then_fail_fact(protection, unit_id, results):
            self.assertEqual(protection.task_root, executor.backup_root)
            material = protection.material_results["o000001"].target
            original = material.read_bytes()
            original_identity = observe_file(material).object_identity
            material.rename(saved)
            material.write_bytes(original)
            self.assertNotEqual(
                observe_file(material).object_identity, original_identity,
            )
            replaced.append(material)
            raise OSError("injected fact failure after backup material replacement")

        before_tasks = {entry.name for entry in self.backups.iterdir()}
        with patch.object(
            RecoveryProtectionSession, "complete",
            new=replace_material_then_fail_fact,
        ):
            with self.assertRaises(BackupRestoreExecutionError) as caught:
                executor.apply()
        self.assertEqual(len(replaced), 1)
        self.assertEqual(executor.rollback_status, "failed")
        self.assertIsInstance(executor.rollback_error, ValueError)
        self.assertIn(executor.rollback_report, str(caught.exception))
        # 不接受新备份身份，因此不开始回滚正文写入。
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(saved.read_bytes(), b"current-before-restore")
        self.assertEqual(replaced[0].read_bytes(), b"current-before-restore")
        self.assertTrue(session.task_root.is_dir())
        self.assertTrue(executor.backup_root.is_dir())
        after_tasks = {entry.name for entry in self.backups.iterdir()}
        self.assertEqual(after_tasks - before_tasks, {executor.backup_root.name})
        self.assertTrue(executor.consumed)
        with self.assertRaises(ValueError):
            executor.apply()
        self.assertEqual(target.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()