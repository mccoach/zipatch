# -*- coding: utf-8 -*-

"""正式服务入口与只读计划契约回归；不读取用户配置。"""

import json
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from services.patch_executor import PatchExecutor
from services.patch_service import (
    apply_backup_restore, apply_patch,
    preview_backup_restore, preview_patch,
)
from services.recovery import snapshot
from core.rule_lists import analyze_rule_list


BOUNDARY = "ZIPATCH_BOUNDARY_TEST_20261012153500_EC1394B82076"


def package(operations):
    lines = [f'<<ZIPATCH_V3 boundary="{BOUNDARY}">>']
    for number, (kind, parameters, blocks) in enumerate(operations, 1):
        header = f'---OP {kind} id="test{number}"'
        for name, value in parameters.items():
            header += f' {name}="{value}"'
        lines.append(header)
        for name, value in blocks:
            lines.extend((name, value, BOUNDARY))
        lines.append("---END_OP")
    lines.append("<</ZIPATCH_V3>>")
    return "\n".join(lines)


class ExecutionContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "project"
        self.root.mkdir()
        self.backups = self.folder / "backups"

    def write(self, relative, content):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def preview(self, operations, enabled=True):
        result = preview_patch(analyze_rule_list("", "file"),
            project_root=str(self.root),
            patch_text=package(operations),
            allow_delete=True,
            allow_multi_replace_exact=True,
            backup_enabled=enabled,
            backup_dir=str(self.backups),
        )
        self.assertEqual(result.failed_count, 0, result.preview_text)
        self.assertFalse(result.has_global_errors)
        return result

    def restore(self, source, enabled=False):
        preview = preview_backup_restore(
            str(self.root), str(source), enabled, str(self.backups),
        )
        result = apply_backup_restore(preview, risk_confirmed=True)
        return preview, result

    def document(self, source):
        return json.loads(
            (Path(source) / "recovery.json").read_text(encoding="utf-8")
        )

    def test_readonly_plan_executes_through_formal_service(self):
        target = self.write("a.bin", b"\x00\xffORIGINAL\r\n")
        preview = self.preview([
            ("write_file", {"if_exists": "overwrite"}, [
                ("---PATH", "a.bin"), ("---CONTENT", "NEW"),
            ]),
        ])
        payload = preview.execution.plan.actions[0].payload
        self.assertIsInstance(payload, MappingProxyType)
        with self.assertRaises(TypeError):
            payload["content"] = "MUTATED"
        result = apply_patch(preview)
        self.assertEqual(target.read_bytes(), b"NEW")
        self.assertTrue(result.backup_root)
        document = self.document(result.backup_root)
        self.assertEqual(document["entries"][0]["path"], "a.bin")
        self.restore(result.backup_root)
        self.assertEqual(target.read_bytes(), b"\x00\xffORIGINAL\r\n")

    def test_fixed_skip_passes_execution_validation_without_backup(self):
        target = self.write("a.txt", b"KEEP")
        preview = self.preview([
            ("write_file", {"if_exists": "skip"}, [
                ("---PATH", "a.txt"), ("---CONTENT", "UNUSED"),
            ]),
        ])
        self.assertFalse(preview.execution.plan.actions[0].execute)
        result = apply_patch(preview)
        self.assertEqual(target.read_bytes(), b"KEEP")
        self.assertEqual(result.backup_root, "")
        self.assertFalse(self.backups.exists())

    def test_multiple_appends_reuse_original_backup(self):
        target = self.write("a.txt", b"A")
        preview = self.preview([
            ("append_text", {}, [
                ("---PATH", "a.txt"), ("---CONTENT", "B"),
            ]),
            ("append_text", {}, [
                ("---PATH", "a.txt"), ("---CONTENT", "C"),
            ]),
        ])
        result = apply_patch(preview)
        self.assertEqual(target.read_bytes(), b"ABC")
        entries = self.document(result.backup_root)["entries"]
        self.assertEqual([entry["path"] for entry in entries], ["a.txt"])
        self.assertEqual(
            (Path(result.backup_root) / "files" / "a.txt").read_bytes(),
            b"A",
        )
        self.restore(result.backup_root)
        self.assertEqual(target.read_bytes(), b"A")

    def test_shared_missing_parent_and_restore_prebackup(self):
        sibling = self.write("keep.bin", b"KEEP")
        preview = self.preview([
            ("write_file", {}, [
                ("---PATH", "generated/sub/a.txt"), ("---CONTENT", "A"),
            ]),
            ("write_file", {}, [
                ("---PATH", "generated/other/b.txt"), ("---CONTENT", "B"),
            ]),
        ])
        forward = apply_patch(preview)
        entries = self.document(forward.backup_root)["entries"]
        self.assertEqual([entry["path"] for entry in entries], ["generated"])
        self.assertEqual(entries[0]["original_type"], "absent")

        reverse_preview, reverse = self.restore(forward.backup_root, True)
        self.assertIn("整个当前目录", reverse_preview.preview_text)
        self.assertFalse((self.root / "generated").exists())
        self.assertEqual(sibling.read_bytes(), b"KEEP")
        self.assertEqual(
            self.document(reverse.backup_root)["task_kind"], "restore",
        )

        self.restore(reverse.backup_root)
        self.assertEqual(
            (self.root / "generated" / "sub" / "a.txt").read_bytes(), b"A",
        )
        self.assertEqual(
            (self.root / "generated" / "other" / "b.txt").read_bytes(), b"B",
        )
        self.assertEqual(sibling.read_bytes(), b"KEEP")

    def test_directory_move_restores_source_target_and_empty_directory(self):
        self.write("source/sub/a.bin", b"\xff\x00")
        (self.root / "source" / "empty").mkdir()
        self.write("target/old.bin", b"OLD")
        source_original = snapshot(self.root / "source").integrity
        target_original = snapshot(self.root / "target").integrity

        preview = self.preview([
            ("move_dir", {"if_exists": "overwrite"}, [
                ("---PATH", "source"), ("---NEW_PATH", "target"),
            ]),
        ])
        result = apply_patch(preview)
        self.assertFalse((self.root / "source").exists())
        self.assertEqual(
            snapshot(self.root / "target").integrity, source_original,
        )
        self.assertEqual(
            {entry["path"] for entry in self.document(result.backup_root)["entries"]},
            {"source", "target"},
        )
        self.restore(result.backup_root)
        self.assertEqual(
            snapshot(self.root / "source").integrity, source_original,
        )
        self.assertEqual(
            snapshot(self.root / "target").integrity, target_original,
        )
        self.assertTrue((self.root / "source" / "empty").is_dir())

    def test_protection_failure_prevents_business_and_consumes_task(self):
        target = self.write("a.txt", b"ORIGINAL")
        preview = self.preview([
            ("write_file", {"if_exists": "overwrite"}, [
                ("---PATH", "a.txt"), ("---CONTENT", "NEW"),
            ]),
        ])
        executor = preview.execution
        with patch.object(
            executor.protection, "protect",
            side_effect=OSError("publication denied"),
        ), patch.object(executor, "perform_action") as business:
            with self.assertRaisesRegex(OSError, "publication denied"):
                apply_patch(preview)
            business.assert_not_called()
        self.assertEqual(target.read_bytes(), b"ORIGINAL")
        with self.assertRaisesRegex(ValueError, "已经执行或失败"):
            apply_patch(preview)
        self.assertFalse(self.backups.exists())

    def test_non_mapping_internal_operation_is_rejected(self):
        executor = PatchExecutor(analyze_rule_list("", "file"),self.root, backup_enabled=False)
        for invalid in (None, [], "operation", 1):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "参数映射"):
                    executor.validate_op(invalid)

    def test_disabled_backup_still_executes_readonly_plan(self):
        preview = self.preview([
            ("write_file", {}, [
                ("---PATH", "new.txt"), ("---CONTENT", "NEW"),
            ]),
        ], enabled=False)
        result = apply_patch(preview)
        self.assertEqual((self.root / "new.txt").read_bytes(), b"NEW")
        self.assertEqual(result.backup_root, "")
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main()