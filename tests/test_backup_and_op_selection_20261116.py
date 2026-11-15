# -*- coding: utf-8 -*-

"""OP结构选择及备份恢复契约；文件操作只作用于自有临时目录。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.recovery_protection import (
    ProtectionPublicationError, read_recovery_evidence,
)
from services.patch_executor import PatchExecutionError
from services.patch_service import (
    apply_backup_restore, apply_patch, confirm_backup_restore,
    invalidate_preview, preview_backup_restore, preview_patch,
)
from ui.patch_op_selection import locate_op_selection


BOUNDARY = "ZIPATCH_BOUNDARY_CONTRACT_20261116031200_79AB420E"


def write_op(identifier, path, content):
    return (
        f'---OP write_file id="{identifier}" if_exists="overwrite"\n'
        f"---PATH\n{path}\n{BOUNDARY}\n"
        f"---CONTENT\n{content}\n{BOUNDARY}\n---END_OP\n"
    )


def package(*operations):
    return (
        f'<<ZIPATCH_V3 boundary="{BOUNDARY}">>\n'
        + "".join(operations) + "<</ZIPATCH_V3>>\n"
    )


class OpSelectionTests(unittest.TestCase):
    def setUp(self):
        self.first = write_op(
            "op001", "a.txt",
            '正文示例\n---OP delete_file id="fake"\n---END_OP\n中文😀',
        )
        self.second = write_op("op002", "b.txt", "第二项")
        self.source = package(self.first, self.second)
        self.first_start = self.source.index(self.first)
        self.second_start = self.source.index(self.second)

    def test_exact_id_includes_end_line_newline(self):
        left, right = locate_op_selection(self.source, op_id="op001")
        self.assertEqual(self.source[left:right], self.first)
        self.assertEqual(right, self.second_start)

    def test_body_fake_id_is_not_an_operation(self):
        with self.assertRaisesRegex(ValueError, "没找到"):
            locate_op_selection(self.source, op_id="fake")

    def test_cursor_inside_body_selects_owner(self):
        position = self.source.index("中文😀")
        left, right = locate_op_selection(self.source, start=position)
        self.assertEqual(self.source[left:right], self.first)

    def test_selection_expands_all_intersected_operations(self):
        left, right = locate_op_selection(
            self.source,
            start=self.first_start + 5,
            end=self.second_start + 5,
        )
        self.assertEqual(self.source[left:right], self.first + self.second)

    def test_end_at_next_start_excludes_next_operation(self):
        left, right = locate_op_selection(
            self.source,
            start=self.first_start + 5,
            end=self.second_start,
        )
        self.assertEqual(self.source[left:right], self.first)

    def test_package_header_is_not_an_operation(self):
        with self.assertRaisesRegex(ValueError, "没有 OP"):
            locate_op_selection(self.source, start=0)

    def test_incomplete_package_is_rejected(self):
        with self.assertRaises(ValueError):
            locate_op_selection(
                self.source.replace("<</ZIPATCH_V3>>", ""), op_id="op001",
            )

    def test_crlf_ranges_preserve_original_text(self):
        source = self.source.replace("\n", "\r\n")
        left, right = locate_op_selection(source, op_id="op002")
        self.assertEqual(source[left:right], self.second.replace("\n", "\r\n"))


@unittest.skipUnless(os.name == "nt", "正式备份材料提交仅支持Windows")
class BackupContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="ZipatchContract_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        self.backups = self.root / "backups"
        self.work.mkdir()
        self.backups.mkdir()
        (self.work / "a.txt").write_bytes(b"original-a")
        (self.work / "b.txt").write_bytes(b"original-b")

    def prepare_patch(self):
        preview = preview_patch(
            None, str(self.work),
            package(
                write_op("op001", "a.txt", "changed-a"),
                write_op("op002", "b.txt", "changed-b"),
            ),
            backup_dir=str(self.backups),
            normalize_text_newlines=False,
        )
        self.addCleanup(invalidate_preview, preview)
        self.assertFalse(preview.has_errors, preview.preview_text)
        return preview

    def test_human_restore_protects_and_repeat_skips(self):
        applied = apply_patch(self.prepare_patch())
        original_backup = Path(applied.backup_root)
        self.assertTrue((original_backup / "backup.complete").is_file())
        read_recovery_evidence(original_backup)

        preview = preview_backup_restore(
            str(original_backup), backup_dir="restore-backups",
            backup_project_root=str(self.root),
        )
        self.addCleanup(invalidate_preview, preview)
        confirm_backup_restore(preview)
        restored = apply_backup_restore(preview)
        self.assertTrue(restored.complete_original_state)
        self.assertTrue(
            (Path(restored.backup_root) / "backup.complete").is_file(),
        )
        self.assertEqual((self.work / "a.txt").read_bytes(), b"original-a")
        self.assertEqual((self.work / "b.txt").read_bytes(), b"original-b")

        repeated = preview_backup_restore(
            str(original_backup), backup_dir="restore-backups",
            backup_project_root=str(self.root),
        )
        self.addCleanup(invalidate_preview, repeated)
        self.assertEqual(repeated.risk_summary["restore_count"], 0)
        confirm_backup_restore(repeated)
        result = apply_backup_restore(repeated)
        self.assertEqual(result.backup_root, "")
        self.assertEqual(len(repeated.execution.execution_result.skipped_paths), 2)

    def test_missing_completion_rejects_restore(self):
        applied = apply_patch(self.prepare_patch())
        backup = Path(applied.backup_root)
        (backup / "backup.complete").unlink()
        with self.assertRaisesRegex(ValueError, "完成凭据"):
            read_recovery_evidence(backup)
        self.assertEqual((self.work / "a.txt").read_bytes(), b"changed-a")

    def test_completion_drift_blocks_started(self):
        preview = self.prepare_patch()
        session = preview.execution.protection
        session.protect_all()
        (session.task_root / "backup.complete").unlink()
        with self.assertRaises(ProtectionPublicationError):
            session.begin(session.projection.units[0].unit_id)
        self.assertEqual((self.work / "a.txt").read_bytes(), b"original-a")
        self.assertEqual((self.work / "b.txt").read_bytes(), b"original-b")

    def test_work_failure_rolls_back_without_second_backup(self):
        preview = self.prepare_patch()
        import services.patch_executor as executor_module
        original_publish = executor_module.publish_content

        def fail_second_work_file(artifact):
            if artifact.expected.path == self.work / "b.txt":
                raise PermissionError("测试注入：第二个工作文件无法发布")
            return original_publish(artifact)

        # 只替换修改包工作发布的调用点；备份保存和恢复算法真实执行。
        with patch.object(
            executor_module, "publish_content",
            side_effect=fail_second_work_file,
        ):
            with self.assertRaises(PatchExecutionError) as captured:
                apply_patch(preview)
        self.assertEqual(captured.exception.rollback_status, "completed")
        self.assertEqual((self.work / "a.txt").read_bytes(), b"original-a")
        self.assertEqual((self.work / "b.txt").read_bytes(), b"original-b")
        tasks = tuple(self.backups.iterdir())
        self.assertEqual(len(tasks), 1)
        self.assertTrue((tasks[0] / "backup.complete").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
