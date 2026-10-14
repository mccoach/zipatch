# -*- coding: utf-8 -*-

"""文本提交事实不依赖日志或完成记录，失败报告保留原异常上下文。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.constants import MESSAGE_FILE_EXCLUDED
from core.rule_lists import analyze_rule_list
from services.patch_executor import PatchExecutor
from services.restore_service import split_and_restore


class PatchCommitContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / "a.txt"

    def operation(self, identifier="first", path="a.txt"):
        return {
            "id": identifier, "op": "write_file", "path": path,
            "content": "NEW", "if_exists": "overwrite",
        }

    def preview(self, operations, enabled=False):
        executor = PatchExecutor(analyze_rule_list("", "file"),
            self.root, backup_enabled=enabled, backup_dir="backup",
        )
        result = executor.preview({
            "version": "3.0", "operations": operations,
        })
        self.assertEqual(result[4], 0, result[0])
        self.assertFalse(result[5], result[0])
        return executor

    def test_first_committed_log_failure_reports_file_already_submitted(self):
        self.target.write_bytes(b"OLD")
        executor = self.preview([self.operation()])
        original_log = executor.log
        original_error = OSError("first committed log denied")

        def log(message):
            if message.startswith("[已提交]"):
                raise original_error
            original_log(message)

        with patch.object(executor, "log", side_effect=log):
            with self.assertRaises(OSError) as caught:
                executor.apply()
        self.assertIs(caught.exception.__cause__.__cause__, original_error)
        self.assertIn("文本已经提交", str(caught.exception))
        self.assertIn('id="first"', str(caught.exception))
        self.assertEqual(self.target.read_bytes(), b"NEW")
        self.assertEqual(len(executor.committed_text_operations), 1)
        self.assertEqual(executor.completed_actions, set())
        self.assertFalse(any("[完成]" in line for line in executor.logs))

    def test_read_failure_has_op_path_phase_and_original_context(self):
        self.target.write_bytes(b"OLD")
        executor = self.preview([{
            "id": "append", "op": "append_text",
            "path": "a.txt", "content": "TAIL",
        }])
        original_error = PermissionError("source read denied")
        with (
            patch("services.patch_executor.read_text_auto", side_effect=original_error),
            patch("core.atomic_file.NamedTemporaryFile") as temporary,
        ):
            with self.assertRaises(OSError) as caught:
                executor.apply()
        self.assertIs(caught.exception.__cause__, original_error)
        for expected in ('id="append"', "append_text", "a.txt", "文本正文准备"):
            self.assertIn(expected, str(caught.exception))
        temporary.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"OLD")
        self.assertEqual(executor.committed_text_operations, [])

    def test_backup_failure_is_located_and_does_not_submit(self):
        self.target.write_bytes(b"OLD")
        executor = self.preview([self.operation()], enabled=True)
        original_error = PermissionError("backup denied")
        with (
            patch.object(executor.protection, "protect", side_effect=original_error),
            patch.object(executor, "perform_action") as business,
        ):
            with self.assertRaises(OSError) as caught:
                executor.apply()
        business.assert_not_called()
        self.assertIs(caught.exception.__cause__, original_error)
        for expected in (
            'id="first"', "修改前保护及清单发布", str(executor.backup_root),
        ):
            self.assertIn(expected, str(caught.exception))
        self.assertEqual(self.target.read_bytes(), b"OLD")
        self.assertEqual(executor.committed_text_operations, [])
        self.assertFalse(executor.backup_root.exists())

    def test_late_read_failure_reports_previous_commit_without_backup(self):
        second = self.root / "b.txt"
        second.write_bytes(b"SECOND OLD")
        executor = self.preview([
            self.operation(),
            {"id": "second", "op": "append_text", "path": "b.txt", "content": "TAIL"},
        ])
        original_error = PermissionError("late read denied")
        with patch("services.patch_executor.read_text_auto", side_effect=original_error):
            with self.assertRaises(OSError) as caught:
                executor.apply()
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertEqual(self.target.read_bytes(), b"NEW")
        self.assertEqual(second.read_bytes(), b"SECOND OLD")
        for expected in (
            "已提交文本操作：1 个", 'id="first"', 'id="second"',
            "a.txt", "b.txt", "已提交文件没有自动撤销",
        ):
            self.assertIn(expected, str(caught.exception))

    def test_business_failure_does_not_attempt_final_auxiliary_save(self):
        executor = self.preview([
            self.operation(), self.operation("second", "b.txt"),
        ], enabled=True)
        original_perform = executor.perform_action
        original_error = PermissionError("second operation denied")

        def perform(action, prepared_text):
            if action.payload["id"] == "second":
                raise original_error
            original_perform(action, prepared_text)

        with (
            patch.object(executor, "perform_action", side_effect=perform),
            patch.object(executor, "write_execution_log_file") as auxiliary,
        ):
            with self.assertRaises(OSError) as caught:
                executor.apply()
        auxiliary.assert_not_called()
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertEqual(self.target.read_bytes(), b"NEW")
        self.assertFalse((self.root / "b.txt").exists())
        self.assertTrue((executor.backup_root / "recovery.json").is_file())
        from services.patch_restore import BackupRestoreExecutor
        BackupRestoreExecutor(self.root, executor.backup_root, False).apply(True)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "b.txt").exists())

    def test_completion_record_failure_reports_completed_operation_stage(self):
        executor = self.preview([self.operation()])
        original_log = executor.log
        original_error = OSError("completion record denied")

        def log(message):
            if message.startswith("[完成]"):
                raise original_error
            original_log(message)

        with patch.object(executor, "log", side_effect=log):
            with self.assertRaises(OSError) as caught:
                executor.apply()
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertEqual(self.target.read_bytes(), b"NEW")
        self.assertEqual(executor.completed_actions, {0})
        self.assertIn("阶段=完成日志", str(caught.exception))
        self.assertIn("已提交文件没有自动撤销", str(caught.exception))

    def test_unknown_programming_error_is_not_converted_to_business_failure(self):
        executor = self.preview([self.operation()])
        original_error = TypeError("programming defect")
        with patch.object(executor, "perform_action", side_effect=original_error):
            with self.assertRaises(TypeError) as caught:
                executor.apply()
        self.assertIs(caught.exception, original_error)
        self.assertFalse(self.target.exists())
        self.assertTrue(executor.consumed)
        with self.assertRaises(ValueError):
            executor.apply()


class RestoreCommitContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "merged.txt"
        self.target = self.root / "restored"

    def merged(self, entries):
        self.source.write_text(
            "".join(
                f"---第{number}个文件---\n{path}\nHEADER\n{body}\nFOOTER\n"
                for number, (path, body) in enumerate(entries, 1)
            ),
            encoding="utf-8",
        )

    def restore(self, log=None):
        return split_and_restore(analyze_rule_list("", "file"),
            self.source, self.target, "HEADER", "FOOTER",
            "overwrite", log_func=log,
        )

    def test_late_progress_record_failure_reports_previous_commit(self):
        self.merged([("a.txt", "A"), ("b.txt", "B")])
        original_error = OSError("second progress denied")

        def log(message):
            if message.startswith("正在还原第 2 个文件"):
                raise original_error

        with self.assertRaises(OSError) as caught:
            self.restore(log)
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertEqual((self.target / "a.txt").read_bytes(), b"A")
        self.assertFalse((self.target / "b.txt").exists())
        self.assertIn("已有 1 个还原文件提交", str(caught.exception))
        self.assertIn("还原进度记录", str(caught.exception))
        self.assertIn("b.txt", str(caught.exception))

    def test_late_path_rejection_remains_value_error_with_commit_context(self):
        self.merged([("a.txt", "A"), ("../outside.txt", "B")])
        with self.assertRaises(ValueError) as caught:
            self.restore()
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual((self.target / "a.txt").read_bytes(), b"A")
        self.assertFalse((self.root / "outside.txt").exists())
        self.assertIn("已有 1 个还原文件提交", str(caught.exception))
        self.assertIn("目标路径解析", str(caught.exception))
        self.assertIn("跳过日志尚未提交", str(caught.exception))

    def test_skip_log_path_record_failure_precedes_completion_message(self):
        self.merged([
            ("a.txt", "A"),
            ("b.txt", MESSAGE_FILE_EXCLUDED),
        ])
        logs = []
        original_error = OSError("skip log path record denied")

        def log(message):
            if message.startswith("跳过日志："):
                raise original_error
            logs.append(message)

        with self.assertRaises(OSError) as caught:
            self.restore(log)
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertEqual((self.target / "a.txt").read_bytes(), b"A")
        self.assertFalse(any(
            message.startswith("代码拆分还原完成：") for message in logs
        ))
        self.assertIn("已提交 1 个文件", str(caught.exception))
        self.assertIn("最终运行记录失败", str(caught.exception))

    def test_nonordinary_target_rejection_is_not_normal_skip(self):
        self.merged([("a.txt", "A")])
        original_error = ValueError("nonordinary target")
        with patch(
            "services.restore_service.atomic_text_writer",
            side_effect=original_error,
        ):
            with self.assertRaises(ValueError) as caught:
                self.restore()
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertIn("目标文件提交", str(caught.exception))
        self.assertIn("已有 0 个还原文件提交", str(caught.exception))


if __name__ == "__main__":
    unittest.main()