# -*- coding: utf-8 -*-

"""所选恢复和拆分正式服务：未选不获取、不保护、不复核或修改。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.merged_container import MergedContainer
from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.file_observation import observe_file
from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)
from core.recovery_protection import RecoveryProtectionSession, read_recovery_evidence
from services.patch_service import (
    preview_backup_restore, confirm_backup_restore, apply_backup_restore,
)
from services.restore_service import prepare_restore, RestoreTask


@unittest.skipUnless(os.name == "nt", "正式保护使用Windows")
class SelectedRestoreSplitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.first = self.project / "first"
        self.second = self.project / "second"
        self.first.write_bytes(b"original-first")
        self.second.write_bytes(b"original-second")

    def tearDown(self):
        self.temporary.cleanup()

    def source_backup(self):
        projection = build_protection_projection(
            tuple(
                FixedUnit(
                    (f"work{number}",),
                    (FixedEffect(
                        observe_file(
                            path, include_bytes=True, include_digest=True,
                            include_mtime=True,
                        ),
                        state_from_bytes(b"changed"),
                    ),),
                )
                for number, path in enumerate((self.first, self.second), 1)
            ),
            forbidden_roots=(self.project,),
        )
        session = RecoveryProtectionSession(
            projection, self.backups / "source", "source", "patch",
            material_base=self.project,
        )
        session.protect_all()
        self.first.write_bytes(b"current-first")
        self.second.write_bytes(b"current-second")
        return session.task_root

    def text(self):
        with MergedContainer(CODE_HEADER_LINE, CODE_FOOTER_LINE) as container:
            container.add_block("first", "new-first")
            container.add_block("second", "new-second")
            return "".join(container.text_chunks())

    def test_partial_recovery_never_reads_or_protects_unselected_target(self):
        source = self.source_backup()
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == self.second:
                raise AssertionError("未选恢复目标不得读取正文")
            return original_open(path, *arguments, **keywords)

        with patch("pathlib.Path.open", new=opened):
            preview = preview_backup_restore(
                str(source), str(self.backups),
                backup_project_root=str(self.project),
                select_restore_scope=lambda entries, accesses: ("authorize", "skip"),
            )
            self.assertEqual(len(preview.execution.plan.actions), 1)
            projection = preview.execution.execution.projection
            self.assertEqual(tuple(obj.target for obj in projection.objects), (self.first,))
            # 未选现场改变不污染所选计划的执行依赖。
            self.second.rename(self.project / "second-saved")
            # 测试自身模拟外部改动，明确使用保留的原始打开函数。
            # 生产恢复和拆分仍受opened守卫约束，不得访问未选对象。
            with original_open(self.second, "wb") as external_stream:
                external_stream.write(b"external-second")
            confirm_backup_restore(preview)
            result = apply_backup_restore(preview)
        self.assertFalse(result.complete_original_state)
        self.assertEqual(self.first.read_bytes(), b"original-first")
        self.assertEqual(self.second.read_bytes(), b"external-second")
        evidence = read_recovery_evidence(result.backup_root)
        self.assertEqual(len(evidence.document.value["objects"]), 1)
        self.assertEqual(evidence.materials["o000001"].source_bytes, b"current-first")

    def test_no_recovery_selection_creates_no_protection_task(self):
        source = self.source_backup()
        before = set(self.backups.iterdir())
        preview = preview_backup_restore(
            str(source), str(self.backups),
            backup_project_root=str(self.project),
            select_restore_scope=lambda entries, accesses: ("skip", "skip"),
        )
        self.assertEqual(preview.execution.plan.actions, ())
        confirm_backup_restore(preview)
        result = apply_backup_restore(preview)
        self.assertFalse(result.complete_original_state)
        self.assertEqual(result.backup_root, "")
        self.assertEqual(set(self.backups.iterdir()), before)
        self.assertEqual(self.first.read_bytes(), b"current-first")

    def test_split_selection_prunes_body_protection_and_dependency(self):
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == self.second:
                raise AssertionError("未选拆分目标不得读取旧正文")
            return original_open(path, *arguments, **keywords)

        with patch("pathlib.Path.open", new=opened):
            plan = prepare_restore(
                None, str(self.project),
                "overwrite", source_text=self.text(), backup_dir=str(self.backups),
                select_split_scope=lambda candidates: ("authorize", "skip"),
            )
            self.assertEqual(tuple(obj.target for obj in plan.projection.objects), (self.first,))
            self.assertEqual(tuple(access.actual_path for access in plan.accesses), (self.first,))
            self.second.rename(self.project / "second-saved")
            # 测试自身模拟外部改动，明确使用保留的原始打开函数。
            # 生产恢复和拆分仍受opened守卫约束，不得访问未选对象。
            with original_open(self.second, "wb") as external_stream:
                external_stream.write(b"external-second")
            result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        self.assertEqual(self.first.read_bytes(), b"new-first")
        self.assertEqual(self.second.read_bytes(), b"external-second")
        self.assertEqual(result.skipped_count, 1)
        read_recovery_evidence(result.backup_root)

    def test_cancelled_split_reads_no_old_body(self):
        with patch(
            "services.restore_service.observe_accessed_file",
            side_effect=AssertionError("取消不获取旧正文"),
        ):
            with self.assertRaisesRegex(ValueError, "已取消"):
                prepare_restore(
                    None, str(self.project),
                    "overwrite", source_text=self.text(), backup_dir=str(self.backups),
                    select_split_scope=lambda candidates: None,
                )
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_malformed_choice_cannot_get_execution_qualification(self):
        with self.assertRaisesRegex(ValueError, "准确对应"):
            prepare_restore(
                None, str(self.project),
                "overwrite", source_text=self.text(), backup_dir=str(self.backups),
                select_split_scope=lambda candidates: ("authorize",),
            )


if __name__ == "__main__":
    unittest.main()