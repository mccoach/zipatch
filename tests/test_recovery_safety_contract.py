# -*- coding: utf-8 -*-

"""原路径安全与普通进程中断回归；所有故障只发生在临时项目。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.recovery_paths import checked_chain, project_path
from core.rule_lists import analyze_rule_list
from services.patch_backup import resolve_backup_base_dir
from services.patch_service import (
    apply_backup_restore,
    apply_patch,
    preview_backup_restore,
    preview_patch,
)


BOUNDARY = "ZIPATCH_BOUNDARY_SAFETY_TEST_20261012154036_B7148D3C0E92"

INTERRUPTED_TASK = r"""
import os
import sys
from pathlib import Path

from services.patch_executor import PatchExecutor
from core.rule_lists import analyze_rule_list

root = Path(sys.argv[1])
executor = PatchExecutor(analyze_rule_list("", "file"),root, backup_dir=sys.argv[2])
report = executor.preview({
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
        {
            "id": "future", "op": "write_file", "path": "c.txt",
            "content": "C NEW", "if_exists": "overwrite",
        },
    ],
})
if report[4] or report[5]:
    raise ValueError(report[0])

perform = executor.perform_action

def interrupt_second(action, prepared_text):
    if action.payload["id"] == "second":
        (root / "b.txt").write_bytes(b"B PARTIAL")
        # 不执行 Python finally、退出钩子或任务结束日志。
        os._exit(23)
    perform(action, prepared_text)

executor.perform_action = interrupt_second
executor.apply()
"""


def write_package(relative, content="NEW"):
    return "\n".join((
        f'<<ZIPATCH_V3 boundary="{BOUNDARY}">>',
        '---OP write_file id="write" if_exists="overwrite"',
        "---PATH", relative, BOUNDARY,
        "---CONTENT", content, BOUNDARY,
        "---END_OP", "<</ZIPATCH_V3>>",
    ))


class SafetyContractTests(unittest.TestCase):
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

    def forward(self, relative="a.txt"):
        preview = preview_patch(analyze_rule_list("", "file"),
            str(self.root), write_package(relative),
            backup_dir=str(self.backups),
        )
        self.assertEqual(preview.failed_count, 0, preview.preview_text)
        return apply_patch(preview)

    def make_directory_link(self):
        outside = self.folder / "outside"
        outside.mkdir()
        link = self.root / "link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"环境不能创建目录符号链接：{error}")
        return link

    def test_navigation_must_not_hide_original_link_chain(self):
        link = self.make_directory_link()
        with patch(
            "core.recovery_paths.absolute_path",
            side_effect=AssertionError("不应先转换绝对路径"),
        ):
            with self.assertRaisesRegex(ValueError, "链接|重解析点"):
                checked_chain(link / ".." / "target")

    def test_backup_selection_must_not_normalize_away_link(self):
        self.make_directory_link()
        with self.assertRaisesRegex(ValueError, "链接|重解析点"):
            resolve_backup_base_dir(self.root, "link/../new_backups")
        self.assertFalse((self.root / "new_backups").exists())

    def test_backup_selection_accepts_safe_parent_navigation(self):
        expected = self.folder / "backups"
        self.assertEqual(
            resolve_backup_base_dir(self.root, "../backups"),
            expected,
        )
        self.assertFalse(expected.exists())

    def test_relative_navigation_does_not_skip_blocking_file(self):
        blocker = self.write("blocker", b"BLOCKER")
        with self.assertRaisesRegex(ValueError, "父路径不是普通目录"):
            checked_chain(blocker / ".." / "target")
        self.assertEqual(blocker.read_bytes(), b"BLOCKER")

    @unittest.skipUnless(os.name == "nt", "Windows 名称策略")
    def test_windows_components_rejected_before_absolute_conversion(self):
        for relative in (
            "trailing.",
            "trailing ",
            "bad./a.txt",
            "bad /a.txt",
            "CON .txt",
            "a.txt:stream",
        ):
            with self.subTest(relative=relative):
                with patch(
                    "core.recovery_paths.absolute_path",
                    side_effect=AssertionError("非法名称不应进入绝对路径转换"),
                ):
                    with self.assertRaisesRegex(ValueError, "Windows 名称"):
                        checked_chain(self.root / relative)

    @unittest.skipUnless(os.name == "nt", "Windows 名称策略")
    def test_windows_invalid_names_rejected_by_formal_preview(self):
        for relative in (
            "trailing.",
            "trailing ",
            "bad./a.txt",
            "bad /a.txt",
            "NUL",
        ):
            with self.subTest(relative=relative):
                preview = preview_patch(analyze_rule_list("", "file"),
                    str(self.root), write_package(relative),
                    backup_dir=str(self.backups),
                )
                self.assertGreater(preview.failed_count, 0)
                self.assertIsNone(preview.execution.plan)
                self.assertFalse(self.backups.exists())
                self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows 驱动器路径策略")
    def test_drive_relative_paths_are_rejected(self):
        drive_relative = self.root.drive + "relative"
        with self.assertRaisesRegex(ValueError, "驱动器当前目录"):
            checked_chain(drive_relative)
        with self.assertRaisesRegex(ValueError, "驱动器当前目录"):
            resolve_backup_base_dir(self.root, drive_relative)

    def test_unknown_format_and_other_project_are_rejected(self):
        target = self.write("a.txt", b"ORIGINAL")
        forward = self.forward()
        source = Path(forward.backup_root)
        manifest = source / "recovery.json"
        original = json.loads(manifest.read_text(encoding="utf-8"))
        other_root = self.folder / "other_project"
        other_root.mkdir()
        invalid_documents = (
            {**original, "format": "UNKNOWN_RECOVERY"},
            {**original, "project": {"root": str(other_root)}},
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                manifest.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(ValueError):
                    preview_backup_restore(
                        str(self.root), str(source),
                        backup_dir=str(self.backups),
                    )
                self.assertEqual(target.read_bytes(), b"NEW")
        manifest.write_text(json.dumps(original), encoding="utf-8")
        preview = preview_backup_restore(
            str(self.root), str(source), backup_enabled=False,
        )
        apply_backup_restore(preview, risk_confirmed=True)
        self.assertEqual(target.read_bytes(), b"ORIGINAL")

    def test_file_blocking_restore_parent_is_not_deleted(self):
        target = self.write("src/a.txt", b"ORIGINAL")
        forward = self.forward("src/a.txt")
        target.unlink()
        target.parent.rmdir()
        blocker = self.write("src", b"BLOCKER")
        with self.assertRaisesRegex(ValueError, "父路径不是普通目录"):
            preview_backup_restore(
                str(self.root), forward.backup_root,
                backup_dir=str(self.backups),
            )
        self.assertEqual(blocker.read_bytes(), b"BLOCKER")
        self.assertEqual(
            (Path(forward.backup_root) / "files" / "src" / "a.txt").read_bytes(),
            b"ORIGINAL",
        )

    def test_process_exit_after_partial_change_keeps_published_recovery(self):
        first = self.write("a.txt", b"A ORIGINAL")
        second = self.write("b.txt", b"B ORIGINAL")
        future = self.write("c.txt", b"C ORIGINAL")
        result = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-c", INTERRUPTED_TASK,
                str(self.root), str(self.backups),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        self.assertEqual(
            result.returncode, 23,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertEqual(first.read_bytes(), b"A NEW")
        self.assertEqual(second.read_bytes(), b"B PARTIAL")
        self.assertEqual(future.read_bytes(), b"C ORIGINAL")

        sources = list(self.backups.iterdir())
        self.assertEqual(len(sources), 1)
        source = sources[0]
        document = json.loads(
            (source / "recovery.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [entry["path"] for entry in document["entries"]],
            ["a.txt", "b.txt"],
        )
        self.assertFalse((source / "files" / "c.txt").exists())
        self.assertFalse((source / "执行日志.txt").exists())
        self.assertEqual(
            (source / "files" / "a.txt").read_bytes(), b"A ORIGINAL",
        )
        self.assertEqual(
            (source / "files" / "b.txt").read_bytes(), b"B ORIGINAL",
        )

        future.write_bytes(b"C LATER")
        preview = preview_backup_restore(
            str(self.root), str(source), backup_enabled=False,
        )
        apply_backup_restore(preview, risk_confirmed=True)
        self.assertEqual(first.read_bytes(), b"A ORIGINAL")
        self.assertEqual(second.read_bytes(), b"B ORIGINAL")
        self.assertEqual(future.read_bytes(), b"C LATER")

    def test_project_paths_still_reject_parent_traversal(self):
        with self.assertRaisesRegex(ValueError, "路径穿越"):
            project_path(self.root, "src/../a.txt")
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
