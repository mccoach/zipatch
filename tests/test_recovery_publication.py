# -*- coding: utf-8 -*-

"""直接覆盖 recovery.json 正式发布的各个故障阶段。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.atomic_file as atomic_file
from core.rule_lists import analyze_rule_list
from services.patch_executor import PatchExecutor
from services.patch_restore import BackupRestoreExecutor


class FailingTemporaryStream:
    """使用真实临时文件，只注入指定流阶段的 I/O 故障。"""

    def __init__(self, stream, stage):
        self.stream = stream
        self.stage = stage

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, error_type, error, traceback):
        result = self.stream.__exit__(error_type, error, traceback)
        if self.stage == "close" and error_type is None:
            raise OSError("injected close failure")
        return result

    @property
    def name(self):
        return self.stream.name

    def write(self, text):
        if self.stage == "write":
            raise OSError("injected write failure")
        return self.stream.write(text)

    def flush(self):
        if self.stage == "flush":
            raise OSError("injected flush failure")
        return self.stream.flush()

    def fileno(self):
        if self.stage == "fsync":
            raise OSError("injected fsync preparation failure")
        return self.stream.fileno()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "project"
        self.root.mkdir()
        (self.root / "a.txt").write_bytes(b"A ORIGINAL")
        (self.root / "b.txt").write_bytes(b"B ORIGINAL")

    def task(self):
        executor = PatchExecutor(analyze_rule_list("", "file"),
            self.root, backup_dir=str(self.folder / "backups"),
        )
        result = executor.preview({
            "version": "3.0",
            "operations": [
                {
                    "id": "first", "op": "write_file", "path": "a.txt",
                    "content": "A NEW", "if_exists": "overwrite",
                },
                {
                    "id": "second", "op": "write_file", "path": "b.txt",
                    "content": "B NEW", "if_exists": "overwrite",
                },
            ],
        })
        self.assertEqual(result[4], 0, result[0])
        return executor

    def test_stream_failures_preserve_previous_manifest_and_materials(self):
        original_factory = atomic_file.NamedTemporaryFile
        for stage in ("write", "flush", "fsync", "close"):
            with self.subTest(stage=stage):
                task = self.task()
                task.protection.protect(0)
                manifest = task.backup_root / "recovery.json"
                previous = manifest.read_bytes()

                def factory(*args, **kwargs):
                    return FailingTemporaryStream(
                        original_factory(*args, **kwargs), stage,
                    )

                with patch(
                    "core.atomic_file.NamedTemporaryFile", side_effect=factory,
                ):
                    with self.assertRaisesRegex(OSError, "未完成清单替换"):
                        task.protection.protect(1)
                self.assertEqual(manifest.read_bytes(), previous)
                self.assertEqual(
                    (task.backup_root / "files" / "a.txt").read_bytes(),
                    b"A ORIGINAL",
                )
                self.assertEqual((self.root / "b.txt").read_bytes(), b"B ORIGINAL")
                reverse = BackupRestoreExecutor(self.root, task.backup_root, False)
                reverse.apply(True)

    def test_file_fsync_failure_before_replace(self):
        task = self.task()
        task.protection.protect(0)
        manifest = task.backup_root / "recovery.json"
        previous = manifest.read_bytes()
        original_fsync = os.fsync

        def fail_manifest_fsync(descriptor):
            raise OSError("injected file fsync failure")

        # 真实材料先完成复制、校验和持久化；只对清单 writer 注入故障。
        entry = task.protection.prepare_object(task.plan.objects[1])
        document = {
            **task.protection.header,
            "entries": list(task.protection.entries.values()) + [entry],
        }
        with patch("core.atomic_file.os.fsync", side_effect=fail_manifest_fsync):
            with self.assertRaisesRegex(OSError, "file fsync failure"):
                with atomic_file.atomic_text_writer(
                    manifest, newline="\n", durable_publication=True,
                ) as stream:
                    json.dump(document, stream)
        self.assertIs(os.fsync, original_fsync)
        self.assertEqual(manifest.read_bytes(), previous)
        BackupRestoreExecutor(self.root, task.backup_root, False).apply(True)

    def test_first_temporary_creation_failure_has_no_manifest(self):
        task = self.task()
        with patch(
            "core.atomic_file.NamedTemporaryFile",
            side_effect=PermissionError("temporary denied"),
        ):
            with self.assertRaisesRegex(OSError, "首次发布可能尚无"):
                task.apply()
        self.assertFalse((task.backup_root / "recovery.json").exists())
        self.assertEqual((self.root / "a.txt").read_bytes(), b"A ORIGINAL")

    def test_replace_failure_keeps_old_manifest_usable(self):
        task = self.task()
        task.protection.protect(0)
        manifest = task.backup_root / "recovery.json"
        previous = manifest.read_bytes()
        original_replace = os.replace

        def fail_manifest_replace(source, destination):
            if Path(destination).name == "recovery.json":
                raise OSError("replace denied")
            return original_replace(source, destination)

        with patch("core.atomic_file.os.replace", side_effect=fail_manifest_replace):
            with self.assertRaisesRegex(OSError, "替换结果无法确认"):
                task.protection.protect(1)
        self.assertEqual(manifest.read_bytes(), previous)
        BackupRestoreExecutor(self.root, task.backup_root, False).apply(True)

    def test_unknown_replace_result_and_postreplace_sync_failure(self):
        for fault in ("unknown", "directory"):
            with self.subTest(fault=fault):
                task = self.task()
                original_replace = os.replace

                def replace_then_error(source, destination):
                    result = original_replace(source, destination)
                    if Path(destination).name == "recovery.json":
                        raise OSError("replace reported error")
                    return result

                location = (
                    "core.atomic_file.os.replace" if fault == "unknown"
                    else "core.atomic_file.sync_directory"
                )
                values = (
                    {"side_effect": replace_then_error}
                    if fault == "unknown"
                    else {"side_effect": OSError("directory sync denied")}
                )
                expected = "替换结果无法确认" if fault == "unknown" else "正式清单已替换"
                with patch(location, **values):
                    with self.assertRaisesRegex(OSError, expected):
                        task.apply()
                self.assertEqual((self.root / "a.txt").read_bytes(), b"A ORIGINAL")
                self.assertTrue((task.backup_root / "files" / "a.txt").is_file())
                manifest = json.loads(
                    (task.backup_root / "recovery.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["entries"][0]["path"], "a.txt")
                BackupRestoreExecutor(self.root, task.backup_root, False).apply(True)


if __name__ == "__main__":
    unittest.main()