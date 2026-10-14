# -*- coding: utf-8 -*-

"""第二批业务提交回归；底层完整流故障矩阵复用 test_atomic_outputs。"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.constants import SKIPPED_LOG_FILENAME
from core.rule_lists import analyze_rule_list
from core.text_io import write_text_utf8
from panels.restore_panel import RestorePanel
from services.patch_executor import PatchExecutor
from services.restore_service import (
    RestoreResult, split_and_restore, write_skipped_log,
)


class AtomicPatchTextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / "a.txt"

    def operation(self, kind):
        arguments = {
            "write_file": {"content": "新正文\r\n尾部", "if_exists": "overwrite"},
            "append_text": {"content": "\r\n追加\n"},
            "replace_exact": {"old": "OLD\n", "new": "NEW\r\n", "count": "1"},
            "replace_between": {
                "start_marker": "<START>", "end_marker": "<END>",
                "content": "新区间\r\n",
            },
        }
        return {
            "id": "op_test", "op": kind, "path": "a.txt", **arguments[kind],
        }

    def source(self, kind):
        if kind == "replace_between":
            return b"HEAD\r\n<START>old<END>\r\nTAIL"
        return b"OLD\r\nTAIL\rEND"

    def preview(self, operations, enabled=False):
        executor = PatchExecutor(analyze_rule_list("", "file"),
            self.root, backup_enabled=enabled, backup_dir="backup",
        )
        result = executor.preview({"version": "3.0", "operations": operations})
        self.assertEqual(result[4], 0, result[0])
        self.assertFalse(result[5], result[0])
        return executor

    def assert_no_completion(self, executor):
        self.assertEqual(executor.completed_actions, set())
        self.assertFalse(any("[完成]" in line for line in executor.logs))

    def test_four_text_results_use_lf_with_empty_output_rules(self):
        expected = {
            "write_file": "新正文\n尾部",
            "append_text": "OLD\nTAIL\nEND\n追加\n",
            "replace_exact": "NEW\nTAIL\nEND",
            "replace_between": "HEAD\n新区间\n\nTAIL",
        }
        for kind, text in expected.items():
            with self.subTest(kind=kind):
                self.target.write_bytes(self.source(kind))
                executor = self.preview([self.operation(kind)])
                executor.apply()
                self.assertEqual(self.target.read_bytes(), text.encode("utf-8"))
                self.assertEqual(executor.completed_actions, {0})
                self.assertEqual(len(executor.committed_text_operations), 1)
                self.assertTrue(any("[已提交]" in line for line in executor.logs))
                self.assertTrue(any("[完成]" in line for line in executor.logs))

    def test_four_commit_failures_keep_old_bytes_and_include_op_locator(self):
        for kind in ("write_file", "append_text", "replace_exact", "replace_between"):
            with self.subTest(kind=kind):
                original = self.source(kind)
                self.target.write_bytes(original)
                executor = self.preview([self.operation(kind)])
                original_error = PermissionError("commit denied")
                with patch("core.atomic_file.os.replace", side_effect=original_error):
                    with self.assertRaisesRegex(OSError, "文本未提交") as caught:
                        executor.apply()
                self.assertIs(caught.exception.__cause__.__cause__, original_error)
                self.assertIn('id="op_test"', str(caught.exception))
                self.assertIn("a.txt", str(caught.exception))
                self.assertEqual(self.target.read_bytes(), original)
                self.assert_no_completion(executor)
                self.assertEqual(executor.committed_text_operations, [])
                self.assertFalse(any("[已提交]" in line for line in executor.logs))
                self.assertEqual(list(self.root.glob(".a.txt.*.tmp")), [])

    def test_new_partial_stream_write_failure_does_not_publish_name(self):
        executor = self.preview([self.operation("write_file")])
        original_factory = tempfile.NamedTemporaryFile

        class PartialStream:
            def __init__(owner, stream):
                owner.stream = stream
                owner.name = stream.name

            def __enter__(owner):
                return owner

            def __exit__(owner, *arguments):
                owner.stream.close()
                return False

            def write(owner, text):
                owner.stream.write(text[:1])
                raise OSError("partial write denied")

        def create(**kwargs):
            return PartialStream(original_factory(**kwargs))

        with patch("core.atomic_file.NamedTemporaryFile", side_effect=create):
            with self.assertRaisesRegex(OSError, "文本未提交"):
                executor.apply()
        self.assertFalse(self.target.exists())
        self.assert_no_completion(executor)
        self.assertEqual(executor.committed_text_operations, [])
        self.assertEqual(list(self.root.glob(".a.txt.*.tmp")), [])

    def test_new_fsync_failure_does_not_publish_name(self):
        executor = self.preview([self.operation("write_file")])
        with patch("core.atomic_file.os.fsync", side_effect=OSError("sync denied")):
            with self.assertRaisesRegex(OSError, "文本未提交"):
                executor.apply()
        self.assertFalse(self.target.exists())
        self.assert_no_completion(executor)
        self.assertEqual(executor.committed_text_operations, [])
        self.assertEqual(list(self.root.glob(".a.txt.*.tmp")), [])

    def test_matching_and_anchor_failures_never_stage(self):
        for kind, original in (
            ("replace_exact", b"NO MATCH"),
            ("replace_between", b"<START>one<START>two<END>"),
            ("replace_between", b"<END>wrong order<START>"),
        ):
            with self.subTest(kind=kind, original=original):
                self.target.write_bytes(original)
                executor = PatchExecutor(analyze_rule_list("", "file"),self.root, backup_enabled=False)
                with patch("core.atomic_file.NamedTemporaryFile") as temporary:
                    result = executor.preview({
                        "version": "3.0", "operations": [self.operation(kind)],
                    })
                    self.assertGreater(result[4], 0)
                    self.assertIsNone(executor.plan)
                    with self.assertRaises(ValueError):
                        executor.apply()
                temporary.assert_not_called()
                self.assertEqual(self.target.read_bytes(), original)
                self.assert_no_completion(executor)

    def test_read_failure_never_stages(self):
        for kind in ("append_text", "replace_exact", "replace_between"):
            with self.subTest(kind=kind):
                original = self.source(kind)
                self.target.write_bytes(original)
                executor = self.preview([self.operation(kind)])
                original_error = PermissionError("read denied")
                with (
                    patch("services.patch_executor.read_text_auto", side_effect=original_error),
                    patch("core.atomic_file.NamedTemporaryFile") as temporary,
                ):
                    with self.assertRaises(OSError) as caught:
                        executor.apply()
                self.assertIs(caught.exception.__cause__, original_error)
                for expected in (
                    'id="op_test"', kind, "a.txt", "文本正文准备", "read denied",
                ):
                    self.assertIn(expected, str(caught.exception))
                temporary.assert_not_called()
                self.assertEqual(self.target.read_bytes(), original)
                self.assertEqual(executor.committed_text_operations, [])
                self.assert_no_completion(executor)
                self.assertEqual(list(self.root.glob(".a.txt.*.tmp")), [])

    def test_existing_error_and_skip_keep_business_choice(self):
        self.target.write_bytes(b"OLD")
        for policy in ("error", "skip"):
            with self.subTest(policy=policy):
                operation = self.operation("write_file")
                operation["if_exists"] = policy
                executor = PatchExecutor(analyze_rule_list("", "file"),self.root, backup_enabled=False)
                with patch("core.atomic_file.NamedTemporaryFile") as temporary:
                    result = executor.preview({
                        "version": "3.0", "operations": [operation],
                    })
                    if policy == "error":
                        self.assertGreater(result[4], 0)
                        with self.assertRaises(ValueError):
                            executor.apply()
                        self.assert_no_completion(executor)
                    else:
                        self.assertEqual(result[4], 0, result[0])
                        self.assertFalse(executor.plan.actions[0].execute)
                        executor.apply()
                        self.assertTrue(any("[跳过]" in line for line in executor.logs))
                temporary.assert_not_called()
                self.assertEqual(self.target.read_bytes(), b"OLD")
                self.assertEqual(executor.committed_text_operations, [])

    def test_bom_and_gb18030_follow_existing_decode_and_utf8_output(self):
        for original in (
            b"\xef\xbb\xbf" + "中文\r\n".encode("utf-8"),
            "中文\r\n".encode("gb18030"),
        ):
            with self.subTest(original=original):
                self.target.write_bytes(original)
                executor = self.preview([{
                    "id": "encoding", "op": "append_text",
                    "path": "a.txt", "content": "尾部",
                }])
                executor.apply()
                self.assertEqual(
                    self.target.read_bytes(), "中文\n尾部".encode("utf-8"),
                )

    def test_low_level_writer_does_not_convert_or_extend_prepared_text(self):
        for text in ("", "a", "a\n", "a\r\nb\rc\n"):
            with self.subTest(text=repr(text)):
                write_text_utf8(self.target, text)
                self.assertEqual(self.target.read_bytes(), text.encode("utf-8"))

    def test_encoding_failure_is_reported_without_destroying_old_file(self):
        self.target.write_bytes(b"OLD")
        operation = self.operation("write_file")
        operation["content"] = "\ud800"
        executor = self.preview([operation])
        with self.assertRaisesRegex(OSError, "文本未提交") as caught:
            executor.apply()
        self.assertIsInstance(
            caught.exception.__cause__.__cause__, UnicodeEncodeError,
        )
        self.assertEqual(self.target.read_bytes(), b"OLD")
        self.assert_no_completion(executor)
        self.assertEqual(executor.committed_text_operations, [])

    def test_completion_log_failure_reports_committed_file(self):
        self.target.write_bytes(b"OLD")
        executor = self.preview([self.operation("write_file")])
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
        self.assertEqual(
            self.target.read_bytes(), "新正文\n尾部".encode("utf-8"),
        )
        self.assertEqual(executor.completed_actions, {0})
        self.assertEqual(len(executor.committed_text_operations), 1)
        self.assertIn("阶段=完成日志", str(caught.exception))
        self.assertIn("已提交文件没有自动撤销", str(caught.exception))
        self.assertFalse(any("[完成]" in line for line in executor.logs))

    def test_batch_late_failure_does_not_roll_back_previous_commit(self):
        second = self.root / "b.txt"
        second.write_bytes(b"SECOND OLD")
        executor = self.preview([
            {"id": "first", "op": "write_file", "path": "a.txt", "content": "FIRST NEW"},
            {
                "id": "second", "op": "write_file", "path": "b.txt",
                "content": "SECOND NEW", "if_exists": "overwrite",
            },
        ])
        original_replace = os.replace
        original_error = PermissionError("second denied")

        def commit(source, target):
            if Path(target) == second:
                raise original_error
            return original_replace(source, target)

        with patch("core.atomic_file.os.replace", side_effect=commit):
            with self.assertRaisesRegex(OSError, "文本未提交") as caught:
                executor.apply()
        self.assertIs(caught.exception.__cause__.__cause__, original_error)
        self.assertIn('id="second"', str(caught.exception))
        self.assertEqual(self.target.read_bytes(), b"FIRST NEW")
        self.assertEqual(second.read_bytes(), b"SECOND OLD")
        self.assertEqual(executor.completed_actions, {0})
        self.assertEqual(len(executor.committed_text_operations), 1)
        self.assertFalse(any(
            '[完成] id="second"' in line for line in executor.logs
        ))

    def test_final_auxiliary_failure_reports_committed_file_and_keeps_recovery(self):
        self.target.write_bytes(b"OLD")
        executor = self.preview([self.operation("write_file")], enabled=True)
        original_error = OSError("final execution log denied")
        with patch.object(
            executor, "write_execution_log_file", side_effect=original_error,
        ):
            with self.assertRaisesRegex(OSError, "业务动作已经完成") as caught:
                executor.apply()
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertIn("已提交文件没有自动撤销", str(caught.exception))
        self.assertEqual(
            self.target.read_bytes(), "新正文\n尾部".encode("utf-8"),
        )
        self.assertEqual(executor.completed_actions, {0})
        from services.patch_restore import BackupRestoreExecutor
        BackupRestoreExecutor(self.root, executor.backup_root, False).apply(True)
        self.assertEqual(self.target.read_bytes(), b"OLD")

    @unittest.skipIf(os.name == "nt", "POSIX 基础权限断言，Windows 另测只读")
    def test_existing_posix_mode_is_preserved(self):
        self.target.write_bytes(b"OLD")
        self.target.chmod(0o640)
        self.preview([self.operation("write_file")]).apply()
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)

    @unittest.skipUnless(os.name == "nt", "Windows 只读目标专项")
    def test_windows_readonly_target_is_not_overwritten(self):
        self.target.write_bytes(b"OLD")
        self.target.chmod(stat.S_IREAD)
        try:
            executor = self.preview([self.operation("write_file")])
            with self.assertRaisesRegex(OSError, "文本未提交"):
                executor.apply()
            self.assertEqual(self.target.read_bytes(), b"OLD")
            self.assert_no_completion(executor)
            self.assertEqual(executor.committed_text_operations, [])
        finally:
            self.target.chmod(stat.S_IREAD | stat.S_IWRITE)


class AtomicRestoreTextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "merged.txt"
        self.target = self.root / "restored"

    def merged(self, items, encoding="utf-8", newline="\n"):
        text = "".join(
            f"---第{number}个文件---\n{path}\nHEADER\n{content}\nFOOTER\n"
            for number, (path, content) in enumerate(items, 1)
        )
        self.source.write_bytes(
            text.replace("\n", newline).encode(encoding),
        )

    def restore(self, policy="overwrite", logs=None):
        return split_and_restore(analyze_rule_list("", "file"),
            self.source,
            self.target,
            "HEADER",
            "FOOTER",
            policy,
            log_func=None if logs is None else logs.append,
        )

    def test_overwrite_rename_skip_keep_choices_and_counts(self):
        self.merged([("a.txt", "NEW")])
        self.target.mkdir()
        target = self.target / "a.txt"
        renamed = self.target / "a_1.txt"
        for policy in ("overwrite", "rename", "skip"):
            with self.subTest(policy=policy):
                renamed.unlink(missing_ok=True)
                target.write_bytes(b"OLD")
                result = self.restore(policy)
                self.assertEqual(result.failed_count, 0)
                if policy == "overwrite":
                    self.assertEqual(target.read_bytes(), b"NEW")
                    self.assertEqual(result.restored_count, 1)
                    self.assertEqual(result.skipped_count, 0)
                elif policy == "rename":
                    self.assertEqual(target.read_bytes(), b"OLD")
                    self.assertEqual(renamed.read_bytes(), b"NEW")
                    self.assertEqual(result.restored_count, 1)
                    self.assertEqual(result.skipped_count, 0)
                else:
                    self.assertEqual(target.read_bytes(), b"OLD")
                    self.assertFalse(renamed.exists())
                    self.assertEqual(result.restored_count, 0)
                    self.assertEqual(result.skipped_count, 1)

    def test_encoding_and_existing_trimming_with_lf_output(self):
        for encoding, newline in (
            ("utf-8-sig", "\r\n"),
            ("gb18030", "\n"),
        ):
            with self.subTest(encoding=encoding):
                self.merged(
                    [("a.txt", "  中文\n第二行  \n\n")],
                    encoding,
                    newline,
                )
                result = self.restore()
                self.assertEqual(result.restored_count, 1)
                self.assertEqual(result.failed_count, 0)
                self.assertEqual(
                    (self.target / "a.txt").read_bytes(),
                    "中文\n第二行".encode("utf-8"),
                )

    def test_failed_file_not_counted_and_previous_file_not_rolled_back(self):
        self.merged([("a.txt", "A NEW"), ("b.txt", "B NEW")])
        self.target.mkdir()
        first = self.target / "a.txt"
        second = self.target / "b.txt"
        first.write_bytes(b"A OLD")
        second.write_bytes(b"B OLD")
        logs = []
        original_replace = os.replace

        def commit(source, target):
            if Path(target) == second:
                raise PermissionError("second commit denied")
            return original_replace(source, target)

        with patch("core.atomic_file.os.replace", side_effect=commit):
            result = self.restore(logs=logs)
        self.assertEqual(first.read_bytes(), b"A NEW")
        self.assertEqual(second.read_bytes(), b"B OLD")
        self.assertEqual(result.restored_count, 1)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertIn(
            "second commit denied",
            Path(result.log_path).read_text(encoding="utf-8"),
        )
        self.assertFalse(any(
            "代码拆分还原完成：" in line for line in logs
        ))
        self.assertTrue(any("文件未提交：" in line for line in logs))
        self.assertEqual(list(self.target.glob(".*.tmp")), [])

    def test_new_restore_failure_does_not_publish_formal_file(self):
        self.merged([("a.txt", "NEW")])
        original_replace = os.replace

        def commit(source, target):
            if Path(target).name == "a.txt":
                raise PermissionError("new commit denied")
            return original_replace(source, target)

        with patch("core.atomic_file.os.replace", side_effect=commit):
            result = self.restore()
        self.assertFalse((self.target / "a.txt").exists())
        self.assertEqual(result.restored_count, 0)
        self.assertEqual(result.failed_count, 1)
        self.assertTrue(Path(result.log_path).is_file())
        self.assertEqual(list(self.target.glob(".*.tmp")), [])

    def test_skip_log_format_and_old_log_visible_until_commit(self):
        self.target.mkdir()
        log_path = self.target / SKIPPED_LOG_FILENAME
        log_path.write_bytes(b"OLD LOG")
        entries = ["a.txt", "b.txt (目标已存在，已跳过)"]
        expected = (
            f"在 {self.source} 的还原过程中，以下文件因内容被忽略、已存在或还原失败而被跳过：\n"
            + "-" * 60 + "\n"
            + "".join(entry + "\n" for entry in entries)
        )
        original_replace = os.replace

        def commit(source, target):
            self.assertEqual(Path(target), log_path)
            self.assertEqual(log_path.read_bytes(), b"OLD LOG")
            self.assertEqual(
                Path(source).read_bytes(),
                expected.replace("\n", os.linesep).encode("utf-8"),
            )
            return original_replace(source, target)

        with patch("core.atomic_file.os.replace", side_effect=commit):
            result = write_skipped_log(
                self.target, self.source, entries,
            )
        self.assertEqual(result, str(log_path))
        self.assertEqual(
            log_path.read_bytes(),
            expected.replace("\n", os.linesep).encode("utf-8"),
        )

    def test_skip_log_failure_keeps_old_log_and_reports_committed_files(self):
        self.merged([
            ("a.txt", "NEW"),
            ("b.txt", "文件命中排除名单，内容略"),
        ])
        self.target.mkdir()
        log_path = self.target / SKIPPED_LOG_FILENAME
        log_path.write_bytes(b"OLD LOG")
        logs = []
        original_replace = os.replace

        def commit(source, target):
            if Path(target) == log_path:
                raise PermissionError("log commit denied")
            return original_replace(source, target)

        # 用现有常量构造被排除正文，避免测试依赖提示文字的猜测。
        from core.constants import MESSAGE_FILE_EXCLUDED
        self.merged([
            ("a.txt", "NEW"),
            ("b.txt", MESSAGE_FILE_EXCLUDED),
        ])

        with patch("core.atomic_file.os.replace", side_effect=commit):
            with self.assertRaisesRegex(
                OSError, "已有 1 个还原文件提交",
            ) as caught:
                self.restore(logs=logs)
        self.assertIsInstance(caught.exception.__cause__, PermissionError)
        self.assertEqual((self.target / "a.txt").read_bytes(), b"NEW")
        self.assertEqual(log_path.read_bytes(), b"OLD LOG")
        self.assertFalse(any(
            "代码拆分还原完成：" in line for line in logs
        ))
        self.assertEqual(list(self.target.glob(".*.tmp")), [])

    def test_missing_and_invalid_source_never_stage(self):
        with patch("core.atomic_file.NamedTemporaryFile") as temporary:
            with self.assertRaises(ValueError):
                self.restore()
        temporary.assert_not_called()

        self.source.write_bytes(b"NOT MERGED")
        with patch("core.atomic_file.NamedTemporaryFile") as temporary:
            with self.assertRaises(ValueError):
                self.restore()
        temporary.assert_not_called()

    def test_programming_error_is_not_converted_to_skipped_success(self):
        self.merged([("a.txt", "NEW")])
        with patch(
            "services.restore_service.atomic_text_writer",
            side_effect=TypeError("programming defect"),
        ):
            with self.assertRaisesRegex(TypeError, "programming defect"):
                self.restore()

    def test_nonordinary_target_rejection_is_not_silently_skipped(self):
        self.merged([("a.txt", "NEW")])
        with patch(
            "services.restore_service.atomic_text_writer",
            side_effect=ValueError("nonordinary target"),
        ):
            with self.assertRaisesRegex(ValueError, "nonordinary target"):
                self.restore()

    def test_no_skipped_entries_does_not_overwrite_historical_log(self):
        self.target.mkdir()
        log_path = self.target / SKIPPED_LOG_FILENAME
        log_path.write_bytes(b"OLD LOG")
        with patch("core.atomic_file.NamedTemporaryFile") as temporary:
            self.assertIsNone(
                write_skipped_log(self.target, self.source, []),
            )
        temporary.assert_not_called()
        self.assertEqual(log_path.read_bytes(), b"OLD LOG")

    def test_final_runtime_log_failure_reports_committed_file(self):
        self.merged([("a.txt", "NEW")])
        original_error = OSError("final runtime log denied")

        def log(message):
            if message.startswith("代码拆分还原完成："):
                raise original_error

        with self.assertRaisesRegex(
            OSError, "最终运行记录失败",
        ) as caught:
            split_and_restore(analyze_rule_list("", "file"),
                self.source,
                self.target,
                "HEADER",
                "FOOTER",
                "overwrite",
                log_func=log,
            )
        self.assertIs(caught.exception.__cause__, original_error)
        self.assertIn("已提交 1 个文件", str(caught.exception))
        self.assertEqual((self.target / "a.txt").read_bytes(), b"NEW")

    def panel(self):
        return SimpleNamespace(
            prepare=Mock(return_value={
                "source_txt_file": str(self.source),
                "target_folder": str(self.target),
                "code_header_line": "HEADER",
                "code_footer_line": "FOOTER",
                "existing_file_policy": "overwrite",
                "open_after_done": False,
            }),
            root=None,
            config_data={"settings": {"crlf_file_list": ""}},
            set_status=Mock(),
            log=Mock(),
            handle_error=Mock(),
            commits=SimpleNamespace(
                metrics={"business_starts": 0},
                prepare_rule=lambda config, field, kind:
                analyze_rule_list(config[field], kind),
            ),
        )

    def test_panel_does_not_report_failed_files_as_complete_success(self):
        panel = self.panel()
        result = RestoreResult(
            target_folder=str(self.target),
            restored_count=1,
            skipped_count=1,
            log_path="skip.log",
            failed_count=1,
        )
        with (
            patch(
                "panels.restore_panel.split_and_restore",
                return_value=result,
            ),
            patch("panels.restore_panel.safe_show_info") as success,
            patch("panels.restore_panel.safe_show_error") as failure,
        ):
            RestorePanel._execute(panel)
        success.assert_not_called()
        failure.assert_called_once()
        self.assertIn("未计入成功数量", failure.call_args.args[1])
        panel.set_status.assert_called_with("代码拆分还原结束，存在失败")

    def test_panel_keeps_success_prompt_for_successful_restore(self):
        panel = self.panel()
        result = RestoreResult(
            target_folder=str(self.target),
            restored_count=1,
            skipped_count=0,
            log_path=None,
            failed_count=0,
        )
        with (
            patch(
                "panels.restore_panel.split_and_restore",
                return_value=result,
            ),
            patch("panels.restore_panel.safe_show_info") as success,
            patch("panels.restore_panel.safe_show_error") as failure,
        ):
            RestorePanel._execute(panel)
        failure.assert_not_called()
        success.assert_called_once()
        self.assertIn("还原任务完成", success.call_args.args[1])
        panel.set_status.assert_called_with("代码拆分还原完成")


if __name__ == "__main__":
    unittest.main()
