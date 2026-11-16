# -*- coding: utf-8 -*-

"""共同文件执行契约及真实业务消费者联动，不操作用户项目文件。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.content_publication import (
    ContentPublicationError, ContentPublicationOptions,
    prepare_content_artifact, publish_content,
)
from core.file_observation import observe_file
from core.fixed_content_execution import FixedContentExecution
from core.recovery_protection import read_recovery_evidence
from services.patch_service import (
    apply_backup_restore, apply_patch, confirm_backup_restore,
    preview_backup_restore, preview_patch,
)
from services.restore_service import RestoreTask, prepare_restore


BOUNDARY = "ZIPATCH_BOUNDARY_TEST_20261116103805_C6W9N2"


def replacement_package(path, old, new):
    return (
        f'<<ZIPATCH_V3 boundary="{BOUNDARY}">>\n'
        '---OP replace_exact id="op001" count="1"\n'
        f"---PATH\n{path}\n{BOUNDARY}\n"
        f"---OLD\n{old}\n{BOUNDARY}\n"
        f"---NEW\n{new}\n{BOUNDARY}\n"
        "---END_OP\n<</ZIPATCH_V3>>\n"
    )


def source_package(entries):
    return "".join(
        f"---第{number}个文件---\n{path}\n"
        f"---以下是源代码---\n{content}\n---源代码结束---\n"
        for number, (path, content) in enumerate(entries, 1)
    )


class FixedContentExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "target.txt"

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self, execution, recheck=lambda: None):
        receipts = []
        stages = []
        actual = execution.execute(
            recheck_context=recheck, record_step=receipts.append,
            stage=lambda target, phase: stages.append((target, phase)),
        )
        return actual, receipts, stages

    def test_publication_delivers_its_verified_observation(self):
        artifact = prepare_content_artifact(observe_file(self.target), b"complete")
        result = publish_content(artifact)
        self.assertTrue(result.succeeded)
        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.path, self.target)
        self.assertEqual(result.observation.digest, artifact.digest)
        self.assertIsInstance(result.observation.mtime_ns, int)
        self.assertEqual(self.target.read_bytes(), b"complete")

    def test_fixed_execution_uses_publication_observation_without_rereading(self):
        execution = FixedContentExecution(observe_file(self.target), b"complete")
        # 获取正文观察归发布核心，不在上层执行核心再次读取成果。
        with patch(
            "core.fixed_content_execution.publish_content",
            wraps=publish_content,
        ) as publisher:
            actual, receipts, stages = self.execute(execution)
        self.assertEqual(publisher.call_count, 1)
        self.assertIs(actual, receipts[0].observation)
        self.assertIs(actual, execution.observation)
        self.assertTrue(execution.consumed)
        self.assertEqual(stages[-1], (self.target, "completed"))
        with self.assertRaises(ValueError):
            self.execute(execution)

    def test_fixed_copy_preserves_source_and_explicit_target_time(self):
        source = self.root / "source.txt"
        source.write_bytes(b"source")
        observed = observe_file(
            source, include_bytes=True, include_digest=True, include_mtime=True,
        )
        execution = FixedContentExecution(
            observe_file(self.target), source=observed,
            preserve_target_mode=False,
            options=ContentPublicationOptions(mtime_ns=observed.mtime_ns),
            desired_digest=observed.digest,
        )
        actual, receipts, _ = self.execute(execution)
        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(self.target.read_bytes(), b"source")
        self.assertEqual(actual.mtime_ns, observed.mtime_ns)
        self.assertIs(actual, receipts[0].observation)

    def test_context_rejection_consumes_qualification_and_cleans_only_staging(self):
        self.target.write_bytes(b"original")
        observed = observe_file(self.target, include_digest=True)
        execution = FixedContentExecution(observed, b"changed")
        receipts = []

        def reject():
            raise ValueError("fixed context rejected")

        with self.assertRaisesRegex(ValueError, "fixed context rejected"):
            execution.execute(
                recheck_context=reject, record_step=receipts.append,
                stage=lambda target, phase: None,
            )
        self.assertTrue(execution.consumed)
        self.assertEqual(receipts, [])
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertEqual(execution.artifact._lifecycle, "disposed")
        self.assertFalse(execution.artifact.path.exists())

    def test_failed_publication_receipt_is_archived_once(self):
        self.target.write_bytes(b"original")
        execution = FixedContentExecution(
            observe_file(self.target, include_digest=True), b"changed",
        )
        receipts = []

        def drift():
            self.target.write_bytes(b"external")

        with self.assertRaises(ContentPublicationError):
            execution.execute(
                recheck_context=drift, record_step=receipts.append,
                stage=lambda target, phase: None,
            )
        self.assertEqual(len(receipts), 1)
        self.assertIs(receipts[0], execution.publication)
        self.assertFalse(receipts[0].mutation_attempted)
        self.assertIsNone(receipts[0].observation)
        self.assertEqual(self.target.read_bytes(), b"external")
        self.assertEqual(execution.artifact._lifecycle, "disposed")

    def test_existing_hardlink_is_written_through_original_object(self):
        self.target.write_bytes(b"original")
        alias = self.root / "alias.txt"
        os.link(self.target, alias)
        observed = observe_file(self.target, include_digest=True)
        execution = FixedContentExecution(observed, b"changed")
        actual, receipts, _ = self.execute(execution)
        self.assertEqual(receipts[0].mechanism, "in_place")
        self.assertEqual(actual.object_identity, observed.object_identity)
        self.assertEqual(actual.link_count, observed.link_count)
        self.assertEqual(alias.read_bytes(), b"changed")
        self.assertEqual(receipts[0].emergency_state, "removed")
        self.assertIs(actual, receipts[0].observation)


@unittest.skipUnless(os.name == "nt", "正式独立备份材料提交使用项目Windows契约")
class ProtectedConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.backups = self.root / "backups"
        self.target = self.project / "target.txt"
        self.target.write_bytes(b"original")

    def tearDown(self):
        self.temporary.cleanup()

    def test_patch_and_active_recovery_use_common_content_execution(self):
        preview = preview_patch(
            None, str(self.project),
            replacement_package("target.txt", "original", "changed"),
            backup_dir=str(self.backups), normalize_text_newlines=False,
        )
        self.assertFalse(preview.has_errors, preview.preview_text)
        original_execute = FixedContentExecution.execute
        calls = []

        def tracked(execution, **keywords):
            calls.append(execution.expected.path)
            return original_execute(execution, **keywords)

        with patch.object(FixedContentExecution, "execute", tracked):
            result = apply_patch(preview)
        self.assertEqual(self.target.read_bytes(), b"changed")
        self.assertIn(self.target, calls)
        self.assertTrue((Path(result.backup_root) / "backup.complete").is_file())
        read_recovery_evidence(result.backup_root)

        restore = preview_backup_restore(
            result.backup_root, str(self.backups),
            backup_project_root=str(self.project),
        )
        confirm_backup_restore(restore)
        calls.clear()
        with patch.object(FixedContentExecution, "execute", tracked):
            restored = apply_backup_restore(restore)
        self.assertTrue(restored.complete_original_state)
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertIn(self.target, calls)
        self.assertIn(Path(restored.backup_root) / "还原执行日志.txt", calls)

    def test_split_and_skip_log_use_common_content_execution(self):
        source = source_package((
            ("target.txt", "changed"),
            ("ignored.txt", "文件命中排除名单，内容略"),
        ))
        plan = prepare_restore(
            None, str(self.project),
            "---以下是源代码---", "---源代码结束---", "overwrite",
            source_text=source, target_mode="in_place",
            backup_dir=str(self.backups),
        )
        task = RestoreTask(plan)
        original_execute = FixedContentExecution.execute
        calls = []

        def tracked(execution, **keywords):
            calls.append(execution.expected.path)
            return original_execute(execution, **keywords)

        with patch.object(FixedContentExecution, "execute", tracked):
            result = task.execute()
        self.assertTrue(result.completed)
        self.assertEqual(self.target.read_bytes(), b"changed")
        self.assertEqual(result.completed_files, (self.target,))
        self.assertIn(self.target, calls)
        self.assertIn(Path(result.log_path), calls)
        self.assertEqual(len(result.results), 1)
        self.assertIsNotNone(result.log_result)
        self.assertTrue(result.log_result.succeeded)
        self.assertIsNotNone(result.results[0].observation)
        self.assertTrue((Path(result.backup_root) / "backup.complete").is_file())


if __name__ == "__main__":
    unittest.main()
