# -*- coding: utf-8 -*-

"""只使用临时项目；通过正式预演、执行和恢复链验证。"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.recovery_paths import checked_chain, project_path
from core.rule_lists import analyze_rule_list
from services.patch_executor import PatchExecutor
from services.patch_restore import BackupRestoreExecutor
from services.recovery import (
    RECOVERY_FORMAT, load_recovery, snapshot,
)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "project"
        self.root.mkdir()
        self.backups = self.folder / "backups"

    def write(self, relative, content=b"ORIGINAL"):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def op(self, kind, relative, identifier="first", **values):
        return {"id": identifier, "op": kind, "path": relative, **values}

    def preview(self, operations, enabled=True, backup_dir=None):
        executor = PatchExecutor(analyze_rule_list("", "file"),
            self.root, allow_delete=True, allow_multi_replace_exact=True,
            backup_enabled=enabled,
            backup_dir=str(self.backups) if backup_dir is None else backup_dir,
        )
        result = executor.preview({"version": "3.0", "operations": operations})
        self.assertEqual(result[4], 0, result[0])
        return executor

    def execute(self, operations, enabled=True):
        executor = self.preview(operations, enabled)
        executor.apply()
        return executor

    def restore(self, source, enabled=False):
        executor = BackupRestoreExecutor(
            self.root, source, enabled, str(self.backups),
        )
        executor.apply(risk_confirmed=True)
        return executor

    def document(self, source):
        return json.loads(
            (Path(source) / "recovery.json").read_text(encoding="utf-8")
        )

    def paths(self, source):
        return [entry["path"] for entry in self.document(source)["entries"]]

    def test_original_bytes_and_text_semantics(self):
        for number, original in enumerate((
            b"\x00\xff\xfe\r\n",
            b"\xef\xbb\xbfBOM\r\nTAIL\r",
            "中文\r\n".encode("gb18030"),
        )):
            with self.subTest(original=original):
                relative = f"a{number}.txt"
                target = self.write(relative, original)
                task = self.execute([self.op(
                    "write_file", relative, content="NEW", if_exists="overwrite",
                )])
                self.assertEqual(target.read_bytes(), b"NEW")
                self.restore(task.backup_root)
                self.assertEqual(target.read_bytes(), original)
        cases = (
            ("append_text", b"ABC", {"content": "TAIL"}, b"ABCTAIL"),
            ("replace_exact", b"ABC", {
                "old": "B", "new": "X", "count": "1",
            }, b"AXC"),
            ("replace_between", b"A<START>B<END>C", {
                "start_marker": "<START>", "end_marker": "<END>",
                "content": "X",
            }, b"AXC"),
        )
        for kind, original, values, expected in cases:
            with self.subTest(kind=kind):
                target = self.write("text.txt", original)
                task = self.execute([self.op(kind, "text.txt", **values)])
                self.assertEqual(target.read_bytes(), expected)
                self.restore(task.backup_root)
                self.assertEqual(target.read_bytes(), original)

    def test_multiple_legal_appends_publish_once(self):
        target = self.write("a.txt", b"A")
        task = self.execute([
            self.op("append_text", "a.txt", "first", content="B"),
            self.op("append_text", "a.txt", "second", content="C"),
        ])
        self.assertEqual(target.read_bytes(), b"ABC")
        self.assertEqual(self.paths(task.backup_root), ["a.txt"])
        self.restore(task.backup_root)
        self.assertEqual(target.read_bytes(), b"A")

    def test_shared_missing_parent_and_unrelated_sibling(self):
        sibling = self.write("keep.txt", b"KEEP")
        task = self.execute([
            self.op("write_file", "generated/sub/a.txt", "first", content="A"),
            self.op("write_file", "generated/other/b.txt", "second", content="B"),
        ])
        self.assertEqual(self.paths(task.backup_root), ["generated"])
        self.write("generated/later.txt", b"LATER")
        restore = BackupRestoreExecutor(self.root, task.backup_root, False)
        self.assertIn("整个当前目录", restore.preview_text)
        restore.apply(True)
        self.assertFalse((self.root / "generated").exists())
        self.assertEqual(sibling.read_bytes(), b"KEEP")

    def test_existing_parent_is_not_protected(self):
        target = self.write("src/a.txt", b"OLD")
        sibling = self.write("src/b.txt", b"KEEP")
        task = self.execute([self.op(
            "write_file", "src/a.txt", content="NEW", if_exists="overwrite",
        )])
        sibling.write_bytes(b"LATER")
        self.assertEqual(self.paths(task.backup_root), ["src/a.txt"])
        self.restore(task.backup_root)
        self.assertEqual(target.read_bytes(), b"OLD")
        self.assertEqual(sibling.read_bytes(), b"LATER")

    def test_create_and_delete(self):
        old = self.write("old.bin", b"OLD")
        task = self.execute([
            self.op("delete_file", "old.bin", "first"),
            self.op("write_file", "new.txt", "second", content="NEW"),
            self.op("create_dir", "new_dir", "third"),
        ])
        self.restore(task.backup_root)
        self.assertEqual(old.read_bytes(), b"OLD")
        self.assertFalse((self.root / "new.txt").exists())
        self.assertFalse((self.root / "new_dir").exists())

    def test_file_copy_move_and_rename(self):
        for kind in ("copy_file", "move_file", "rename_file"):
            with self.subTest(kind=kind):
                source = self.write("source.bin", b"SOURCE")
                target = self.write("target.bin", b"TARGET")
                task = self.execute([self.op(
                    kind, "source.bin", new_path="target.bin",
                    if_exists="overwrite",
                )])
                self.assertEqual(target.read_bytes(), b"SOURCE")
                expected = {"target.bin"} if kind == "copy_file" else {
                    "source.bin", "target.bin",
                }
                self.assertEqual(set(self.paths(task.backup_root)), expected)
                self.restore(task.backup_root)
                self.assertEqual(source.read_bytes(), b"SOURCE")
                self.assertEqual(target.read_bytes(), b"TARGET")

    def test_directory_copy_move_rename_and_delete(self):
        for kind in ("copy_dir", "move_dir", "rename_dir", "delete_dir"):
            with self.subTest(kind=kind):
                for relative in ("source", "target"):
                    target = self.root / relative
                    if target.exists():
                        shutil.rmtree(target)
                self.write("source/sub/a.bin", b"\xff\x00")
                (self.root / "source" / "empty").mkdir()
                self.write("target/old.bin", b"TARGET")
                original = snapshot(self.root / "source").integrity
                target_original = snapshot(self.root / "target").integrity
                values = {} if kind == "delete_dir" else {
                    "new_path": "target", "if_exists": "overwrite",
                }
                task = self.execute([self.op(kind, "source", **values)])
                if kind != "delete_dir":
                    self.assertEqual(
                        snapshot(self.root / "target").integrity, original,
                    )
                    self.write("target/extra.bin", b"EXTRA")
                self.restore(task.backup_root)
                self.assertEqual(
                    snapshot(self.root / "source").integrity, original,
                )
                self.assertEqual(
                    snapshot(self.root / "target").integrity, target_original,
                )
                self.assertTrue((self.root / "source" / "empty").is_dir())

    def test_skip_does_not_become_create(self):
        target = self.write("a.txt", b"ORIGINAL")
        task = self.preview([self.op(
            "write_file", "a.txt", content="NEW", if_exists="skip",
        )])
        target.unlink()
        with self.assertRaisesRegex(ValueError, "规划后对象"):
            task.apply()
        self.assertFalse(target.exists())

    def test_restore_noop_does_not_become_delete_after_confirmation(self):
        task = self.execute([self.op("write_file", "new.txt", content="NEW")])
        (self.root / "new.txt").unlink()
        restore = BackupRestoreExecutor(self.root, task.backup_root, False)
        external = self.write("new.txt", b"EXTERNAL")
        with self.assertRaisesRegex(ValueError, "规划后对象"):
            restore.apply(True)
        self.assertEqual(external.read_bytes(), b"EXTERNAL")

    def test_external_parent_creation_stops_before_materials(self):
        task = self.preview([self.op(
            "write_file", "generated/a.txt", content="NEW",
        )])
        external = self.write("generated/external.txt", b"EXTERNAL")
        with self.assertRaisesRegex(ValueError, "必要父目录"):
            task.apply()
        self.assertFalse((self.root / "generated" / "a.txt").exists())
        self.assertFalse(task.backup_root.exists())
        self.assertEqual(external.read_bytes(), b"EXTERNAL")

    def test_task_cannot_be_reexecuted_or_replanned(self):
        task = self.execute([self.op("write_file", "a.txt", content="NEW")])
        with self.assertRaisesRegex(ValueError, "已经执行"):
            task.apply()
        with self.assertRaisesRegex(ValueError, "不能重新规划"):
            task.preview({
                "version": "3.0",
                "operations": [self.op("write_file", "b.txt", content="B")],
            })
        self.assertFalse((self.root / "b.txt").exists())

    def test_plan_does_not_prepare_future_objects(self):
        self.write("a.txt", b"A")
        self.write("b.txt", b"B")
        task = self.preview([
            self.op("write_file", "a.txt", "first", content="X", if_exists="overwrite"),
            self.op("write_file", "b.txt", "second", content="Y", if_exists="overwrite"),
        ])
        self.assertFalse(task.backup_root.exists())
        task.protection.protect(0)
        self.assertEqual(self.paths(task.backup_root), ["a.txt"])
        self.assertFalse((task.backup_root / "files" / "b.txt").exists())
        self.restore(task.backup_root)
        self.assertEqual((self.root / "a.txt").read_bytes(), b"A")

    def test_copy_and_material_sync_fail_before_business(self):
        for fault in ("copy", "sync"):
            with self.subTest(fault=fault):
                target = self.write("a.txt", b"ORIGINAL")
                task = self.preview([self.op(
                    "write_file", "a.txt", content="NEW", if_exists="overwrite",
                )])
                location = (
                    "services.recovery.shutil.copy2"
                    if fault == "copy" else "services.recovery.sync_snapshot"
                )
                with patch(location, side_effect=OSError(fault + " denied")):
                    with self.assertRaisesRegex(OSError, fault + " denied"):
                        task.apply()
                self.assertEqual(target.read_bytes(), b"ORIGINAL")
                self.assertFalse((task.backup_root / "recovery.json").exists())

    def test_backup_integrity_failure(self):
        target = self.write("a.txt", b"ORIGINAL")
        task = self.preview([self.op(
            "write_file", "a.txt", content="NEW", if_exists="overwrite",
        )])
        original_copy = shutil.copy2

        def corrupt_copy(source, destination, *args, **kwargs):
            result = original_copy(source, destination, *args, **kwargs)
            Path(destination).write_bytes(b"CORRUPTED")
            return result

        with patch("services.recovery.shutil.copy2", side_effect=corrupt_copy):
            with self.assertRaisesRegex(OSError, "完整性不符"):
                task.apply()
        self.assertEqual(target.read_bytes(), b"ORIGINAL")

    def test_multiobject_preparation_failure(self):
        source = self.write("source.bin", b"SOURCE")
        target = self.write("target.bin", b"TARGET")
        task = self.preview([self.op(
            "move_file", "source.bin", new_path="target.bin", if_exists="overwrite",
        )])
        original_prepare = task.protection.prepare_object
        calls = []

        def fail_second(protected):
            calls.append(protected.path)
            if len(calls) == 2:
                raise OSError("second object denied")
            return original_prepare(protected)

        with patch.object(task.protection, "prepare_object", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second object denied"):
                task.apply()
        self.assertEqual(len(calls), 2)
        self.assertFalse((task.backup_root / "recovery.json").exists())
        self.assertEqual(source.read_bytes(), b"SOURCE")
        self.assertEqual(target.read_bytes(), b"TARGET")

    def test_partial_business_failure_and_future_object(self):
        first = self.write("a.txt", b"A ORIGINAL")
        second = self.write("b.bin", b"B ORIGINAL")
        future = self.write("c.txt", b"C ORIGINAL")
        self.write("source.bin", b"SOURCE")
        task = self.preview([
            self.op("write_file", "a.txt", "first", content="A NEW", if_exists="overwrite"),
            self.op("copy_file", "source.bin", "second", new_path="b.bin", if_exists="overwrite"),
            self.op("write_file", "c.txt", "third", content="C NEW", if_exists="overwrite"),
        ])
        original_copy = shutil.copy2

        def partial_copy(source, destination, *args, **kwargs):
            if Path(destination) == second:
                second.write_bytes(b"PARTIAL")
                raise OSError("business partial failure")
            return original_copy(source, destination, *args, **kwargs)

        with patch("services.patch_executor.shutil.copy2", side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, "business partial failure"):
                task.apply()
        self.assertEqual(first.read_bytes(), b"A NEW")
        self.assertEqual(second.read_bytes(), b"PARTIAL")
        self.assertEqual(future.read_bytes(), b"C ORIGINAL")
        self.assertEqual(self.paths(task.backup_root), ["a.txt", "b.bin"])
        future.write_bytes(b"C LATER")
        self.restore(task.backup_root)
        self.assertEqual(first.read_bytes(), b"A ORIGINAL")
        self.assertEqual(second.read_bytes(), b"B ORIGINAL")
        self.assertEqual(future.read_bytes(), b"C LATER")

    def test_first_business_partial_failure(self):
        self.write("source.bin", b"SOURCE")
        target = self.write("target.bin", b"TARGET")
        task = self.preview([self.op(
            "copy_file", "source.bin", new_path="target.bin", if_exists="overwrite",
        )])
        original_copy = shutil.copy2

        def partial_copy(source, destination, *args, **kwargs):
            if Path(destination) == target:
                target.write_bytes(b"PARTIAL")
                raise OSError("first partial failure")
            return original_copy(source, destination, *args, **kwargs)

        with patch("services.patch_executor.shutil.copy2", side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, "first partial failure"):
                task.apply()
        self.assertEqual(target.read_bytes(), b"PARTIAL")
        self.restore(task.backup_root)
        self.assertEqual(target.read_bytes(), b"TARGET")

    def test_auxiliary_failure_does_not_disable_recovery(self):
        target = self.write("a.txt", b"ORIGINAL")
        task = self.preview([self.op(
            "write_file", "a.txt", content="NEW", if_exists="overwrite",
        )])
        with patch.object(
            task, "write_execution_log_file", side_effect=OSError("log denied"),
        ):
            with self.assertRaisesRegex(OSError, "业务动作已经完成"):
                task.apply()
        self.assertEqual(target.read_bytes(), b"NEW")
        self.restore(task.backup_root)
        self.assertEqual(target.read_bytes(), b"ORIGINAL")

    def test_restore_missing_parent_and_restore_backup_roundtrip(self):
        self.write("src/a.txt", b"A")
        self.write("src/b.txt", b"B")
        forward = self.execute([
            self.op("write_file", "src/a.txt", "first", content="X", if_exists="overwrite"),
            self.op("write_file", "src/b.txt", "second", content="Y", if_exists="overwrite"),
        ])
        shutil.rmtree(self.root / "src")
        reverse = self.restore(forward.backup_root, True)
        self.assertEqual((self.root / "src" / "a.txt").read_bytes(), b"A")
        self.assertEqual((self.root / "src" / "b.txt").read_bytes(), b"B")
        self.assertEqual(self.paths(reverse.backup_root), ["src"])
        self.assertIn("再次恢复时删除", reverse.preview_text)
        self.assertEqual(self.document(reverse.backup_root)["task_kind"], "restore")
        self.restore(reverse.backup_root)
        self.assertFalse((self.root / "src").exists())

    def test_restore_prebackup_failure_and_partial_restore(self):
        target = self.write("a.txt", b"ORIGINAL")
        forward = self.execute([self.op(
            "write_file", "a.txt", content="NEW", if_exists="overwrite",
        )])
        reverse = BackupRestoreExecutor(
            self.root, forward.backup_root, True, str(self.backups),
        )
        with patch(
            "services.recovery.shutil.copy2", side_effect=OSError("prebackup denied"),
        ):
            with self.assertRaisesRegex(OSError, "prebackup denied"):
                reverse.apply(True)
        self.assertEqual(target.read_bytes(), b"NEW")
        reverse = BackupRestoreExecutor(self.root, forward.backup_root, False)
        original_copy = shutil.copy2

        def partial_copy(source, destination, *args, **kwargs):
            if Path(destination) == target:
                target.write_bytes(b"PARTIAL RESTORE")
                raise OSError("restore denied")
            return original_copy(source, destination, *args, **kwargs)

        with patch("services.recovery.shutil.copy2", side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, "可能部分改变目标：是"):
                reverse.apply(True)
        self.assertEqual(target.read_bytes(), b"PARTIAL RESTORE")
        self.restore(forward.backup_root)
        self.assertEqual(target.read_bytes(), b"ORIGINAL")

    def test_changed_source_manifest_requires_new_confirmation(self):
        target = self.write("a.txt", b"ORIGINAL")
        forward = self.execute([self.op(
            "write_file", "a.txt", content="NEW", if_exists="overwrite",
        )])
        reverse = BackupRestoreExecutor(self.root, forward.backup_root, False)
        manifest = forward.backup_root / "recovery.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "源恢复清单变化"):
            reverse.apply(True)
        self.assertEqual(target.read_bytes(), b"NEW")

    def test_corrupt_later_material_blocks_all_restore(self):
        first = self.write("a.txt", b"A")
        second = self.write("b.txt", b"B")
        forward = self.execute([
            self.op("write_file", "a.txt", "first", content="X", if_exists="overwrite"),
            self.op("write_file", "b.txt", "second", content="Y", if_exists="overwrite"),
        ])
        reverse = BackupRestoreExecutor(self.root, forward.backup_root, False)
        (forward.backup_root / "files" / "b.txt").write_bytes(b"CORRUPTED")
        with self.assertRaisesRegex(ValueError, "损坏"):
            reverse.apply(True)
        self.assertEqual(first.read_bytes(), b"X")
        self.assertEqual(second.read_bytes(), b"Y")

    def test_old_format_empty_format_and_unreferenced_material(self):
        legacy = self.folder / "legacy"
        legacy.mkdir()
        (legacy / "manifest.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "旧 manifest 不再支持"):
            load_recovery(legacy, self.root)
        self.assertFalse((legacy / "recovery.json").exists())
        empty = self.folder / "empty"
        empty.mkdir()
        (empty / "recovery.json").write_text(json.dumps({
            "format": RECOVERY_FORMAT, "version": 1, "task_id": "empty",
            "task_kind": "restore", "project": {"root": str(self.root)},
            "backup_root": "files", "entries": [],
        }), encoding="utf-8")
        self.assertIn(
            "无需恢复",
            BackupRestoreExecutor(self.root, empty, False).apply(False),
        )
        target = self.write("a.txt", b"A")
        forward = self.execute([self.op(
            "write_file", "a.txt", content="X", if_exists="overwrite",
        )])
        (forward.backup_root / "files" / "extra.bin").write_bytes(b"EXTRA")
        self.restore(forward.backup_root)
        self.assertEqual(target.read_bytes(), b"A")
        self.assertFalse((self.root / "extra.bin").exists())

    def test_bad_manifest_entries_and_material_type(self):
        self.write("a.txt", b"A")
        forward = self.execute([self.op(
            "write_file", "a.txt", content="X", if_exists="overwrite",
        )])
        manifest = forward.backup_root / "recovery.json"
        original = self.document(forward.backup_root)
        entry = original["entries"][0]
        cases = (
            {**original, "version": 2},
            {**original, "entries": [entry, dict(entry)]},
            {**original, "entries": [{**entry, "existed": False}]},
            {**original, "entries": [{**entry, "backup_path": "../outside"}]},
            {**original, "entries": [{**entry, "path": "../outside"}]},
        )
        for document in cases:
            with self.subTest(document=document):
                manifest.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_recovery(forward.backup_root, self.root)
        manifest.write_text(json.dumps(original), encoding="utf-8")
        material = forward.backup_root / "files" / "a.txt"
        material.unlink()
        material.mkdir()
        with self.assertRaisesRegex(ValueError, "类型错误"):
            load_recovery(forward.backup_root, self.root)

    def test_safety_isolation_and_disabled_backup(self):
        task = PatchExecutor(analyze_rule_list("", "file"),
            self.root, backup_enabled=True, backup_dir="generated/backups",
        )
        report = task.preview({
            "version": "3.0",
            "operations": [self.op("write_file", "generated/a.txt", content="A")],
        })
        self.assertTrue(report[5])
        self.assertIsNone(task.plan)
        self.assertFalse((self.root / "generated").exists())
        self.write("source/a.txt", b"A")
        task = PatchExecutor(analyze_rule_list("", "file"),
            self.root, backup_enabled=True, backup_dir="source/backups",
        )
        report = task.preview({
            "version": "3.0",
            "operations": [self.op("copy_dir", "source", new_path="target")],
        })
        self.assertTrue(report[5])
        self.assertFalse((self.root / "target").exists())
        blocker = self.folder / "blocker"
        blocker.write_bytes(b"BLOCKER")
        task = self.preview(
            [self.op("write_file", "new.txt", content="NEW")],
            False, str(blocker / "unused"),
        )
        task.apply()
        self.assertEqual((self.root / "new.txt").read_bytes(), b"NEW")

    def test_v3_conflicts_remain_rejected(self):
        self.write("a.txt", b"ABCDE")
        for operations in (
            [
                self.op("write_file", "a.txt", "first", content="X", if_exists="overwrite"),
                self.op("append_text", "a.txt", "second", content="Y"),
            ],
            [
                self.op("replace_exact", "a.txt", "first", old="ABC", new="X", count="1"),
                self.op("replace_exact", "a.txt", "second", old="BCD", new="Y", count="1"),
            ],
            [self.op("copy_file", "a.txt", new_path="sub/../a.txt")],
        ):
            task = PatchExecutor(analyze_rule_list("", "file"),self.root, backup_dir=str(self.backups))
            report = task.preview({"version": "3.0", "operations": operations})
            self.assertTrue(report[2])
            self.assertIsNone(task.plan)
        self.assertEqual((self.root / "a.txt").read_bytes(), b"ABCDE")

    def make_link(self, target, link, directory=False):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"环境不能创建符号链接：{error}")

    def test_links_are_checked_before_resolve(self):
        outside = self.folder / "outside"
        outside.mkdir()
        link = self.root / "link"
        self.make_link(outside, link, True)
        with patch("pathlib.Path.resolve", side_effect=AssertionError("resolve")):
            with self.assertRaisesRegex(ValueError, "链接|重解析点"):
                checked_chain(link / "a.txt")

    def test_copy_source_tree_link(self):
        source = self.write("source/a.txt", b"A")
        self.make_link(source, self.root / "source" / "link")
        task = PatchExecutor(analyze_rule_list("", "file"),self.root, backup_dir=str(self.backups))
        report = task.preview({
            "version": "3.0",
            "operations": [self.op("copy_dir", "source", new_path="target")],
        })
        self.assertTrue(report[2])
        self.assertFalse((self.root / "target").exists())

    def test_restore_target_and_material_links(self):
        target = self.write("a.txt", b"A")
        forward = self.execute([self.op(
            "write_file", "a.txt", content="X", if_exists="overwrite",
        )])
        outside = self.folder / "outside.bin"
        outside.write_bytes(b"OUTSIDE")
        target.unlink()
        self.make_link(outside, target)
        with self.assertRaisesRegex(ValueError, "链接|重解析点"):
            BackupRestoreExecutor(self.root, forward.backup_root, False)
        target.unlink()
        target.write_bytes(b"X")
        material = forward.backup_root / "files" / "a.txt"
        material.unlink()
        self.make_link(outside, material)
        with self.assertRaisesRegex(ValueError, "链接|重解析点"):
            load_recovery(forward.backup_root, self.root)
        self.assertEqual(outside.read_bytes(), b"OUTSIDE")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "平台不提供 FIFO")
    def test_special_tree_entity(self):
        (self.root / "tree").mkdir()
        os.mkfifo(self.root / "tree" / "fifo")
        task = PatchExecutor(analyze_rule_list("", "file"),self.root, allow_delete=True)
        report = task.preview({
            "version": "3.0", "operations": [self.op("delete_dir", "tree")],
        })
        self.assertTrue(report[2])
        self.assertTrue((self.root / "tree").exists())

    @unittest.skipUnless(os.name == "nt", "Windows 名称策略")
    def test_windows_names(self):
        for relative in ("a.txt:stream", "trailing.", "trailing ", "CON .txt", "NUL"):
            with self.subTest(relative=relative):
                with self.assertRaises(ValueError):
                    project_path(self.root, relative)

    def test_restore_requires_current_confirmation(self):
        self.write("a.txt", b"A")
        forward = self.execute([self.op(
            "write_file", "a.txt", content="X", if_exists="overwrite",
        )])
        reverse = BackupRestoreExecutor(self.root, forward.backup_root, False)
        with self.assertRaisesRegex(ValueError, "必须当次确认"):
            reverse.apply(False)
        self.assertEqual((self.root / "a.txt").read_bytes(), b"X")


if __name__ == "__main__":
    unittest.main()