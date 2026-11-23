# -*- coding: utf-8 -*-

"""目标级共同决定、排除前移、尾部名单及普通合并静默通过。"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.link_paths import inspect_link_path
from core.merged_format import parse_merge_blocks
from services.merge_service import (
    build_regular_merge_plan, format_merge_omissions, merge_files,
)
from services.patch_scope import (
    apply_patch_target_decisions, patch_target_groups,
)
from services.patch_service import preview_patch, invalidate_preview
from ui.source_scope import make_merge_selector


@unittest.skipUnless(os.name == "nt", "正式Windows来源及目标观察")
class TargetMergeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "merged.txt"
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def merge(self, *, mode="directory", text="", exclusions=None, selector=None):
        return merge_files(
            mode, str(self.source), text, str(self.output),
            exclusions or prepare_exclusions("", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
            select_merge_scope=selector,
        )

    def result_text(self):
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

    def test_excluded_files_have_no_blocks_and_directory_is_not_expanded(self):
        keep = self.source / "keep.py"
        keep.write_bytes(b"keep")
        excluded = self.source / "omit.log"
        excluded.write_bytes(b"excluded-secret")
        folder = self.source / "blocked"
        folder.mkdir()
        child = folder / "private.py"
        child.write_bytes(b"private-secret")
        original_scandir = os.scandir
        original_open = Path.open

        def scandir(path):
            if Path(path) == folder:
                raise AssertionError("排除目录不得枚举")
            return original_scandir(path)

        def opened(path, *arguments, **keywords):
            if path in (excluded, child):
                raise AssertionError("排除文件不得读取")
            return original_open(path, *arguments, **keywords)

        with patch("os.scandir", new=scandir), patch("pathlib.Path.open", new=opened):
            result = self.merge(exclusions=prepare_exclusions("blocked", "*.log"))
        text = self.result_text()
        blocks = parse_merge_blocks(text)
        self.assertEqual(tuple(block.path for block in blocks), (str(keep),))
        self.assertEqual(result.total_files, 1)
        self.assertEqual(result.excluded_count, 2)
        self.assertIn(str(excluded), text)
        self.assertIn(str(folder), text)
        self.assertNotIn(str(child), text)
        self.assertNotIn("excluded-secret", text)
        self.assertNotIn("private-secret", text)

    def test_excluded_directory_link_does_not_read_definition_or_target(self):
        actual = self.root / "actual"
        actual.mkdir()
        alias = self.source / "blocked"
        os.symlink(actual, alias, target_is_directory=True)
        self.links.append((alias, True))
        ignored = []
        original_readlink = os.readlink
        original_scandir = os.scandir

        def readlink(path, *arguments, **keywords):
            if Path(path) == alias:
                raise AssertionError("排除入口不得读取原定义")
            return original_readlink(path, *arguments, **keywords)

        def scandir(path):
            if Path(path) == actual:
                raise AssertionError("不得枚举排除链接目标")
            return original_scandir(path)

        with patch("os.readlink", new=readlink), patch("os.scandir", new=scandir):
            plan = build_regular_merge_plan(
                self.source, prepare_exclusions("blocked", ""),
                ignored_entries=ignored,
            )
        self.assertEqual(plan, [])
        self.assertEqual(ignored, [(str(alias), "directory", "排除目录名单")])

    def test_passive_decode_failure_is_tail_only(self):
        bad = self.source / "bad.bin"
        bad.write_bytes(b"\xff")
        good = self.source / "good.py"
        good.write_bytes(b"good")
        result = self.merge()
        text = self.result_text()
        blocks = parse_merge_blocks(text)
        self.assertEqual(tuple(block.path for block in blocks), (str(good),))
        self.assertEqual(result.read_failure_count, 1)
        self.assertEqual(result.total_files, 1)
        self.assertEqual(dict(result.entry_counts)["file"], 1)
        self.assertIn("本次被动失败", text)
        self.assertIn(str(bad), text)
        self.assertNotIn("文件读取或解码失败，内容未提供", text)

    def test_explicit_list_inclusion_removes_final_file_exclusion_record(self):
        source = self.source / "included.log"
        source.write_bytes(b"included")
        result = self.merge(
            mode="combined", text="included.log",
            exclusions=prepare_exclusions("", "*.log"),
        )
        text = self.result_text()
        blocks = parse_merge_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].body, "included")
        tail = text.split("【本次主动忽略", 1)[1]
        self.assertNotIn(str(source), tail)
        self.assertEqual(result.excluded_count, 0)

    def test_unselected_file_has_no_body_block_and_is_in_tail(self):
        first = self.source / "first.py"
        second = self.source / "second.py"
        first.write_bytes(b"first")
        second.write_bytes(b"not-selected")
        result = self.merge(
            selector=lambda prepared, accesses, output: ("authorize", "skip"),
        )
        text = self.result_text()
        self.assertEqual(len(parse_merge_blocks(text)), 1)
        self.assertEqual(result.excluded_count, 1)
        self.assertIn(str(second), text)
        self.assertIn("本次来源未授权", text)
        self.assertNotIn("not-selected", text)

    def test_plain_merge_factory_never_opens_selection_window(self):
        source = self.source / "plain.py"
        source.write_bytes(b"plain")
        selector = make_merge_selector(None, None)
        with patch(
            "ui.source_scope.select_work_scope",
            side_effect=AssertionError("普通范围不应弹窗"),
        ):
            result = self.merge(selector=selector)
        self.assertEqual(result.total_files, 1)

    def test_directory_omission_compacts_descendants_without_filesystem_access(self):
        directory = self.source / "blocked"
        with patch(
            "os.scandir", side_effect=AssertionError("报告不能扫描"),
        ), patch(
            "pathlib.Path.open", side_effect=AssertionError("报告不能读正文"),
        ):
            text = format_merge_omissions((
                (str(directory), "directory", "未授权"),
                (str(directory / "child.py"), "file", "未授权"),
                (str(directory), "directory", "排除名单"),
            ), ())
        self.assertIn(str(directory), text)
        self.assertNotIn("child.py", text)
        self.assertIn("排除名单", text)

    def test_same_actual_content_target_has_one_group_and_common_skip(self):
        body = self.source / "body.py"
        body.write_bytes(b"first second")
        aliases = []
        for name in ("one", "two"):
            alias = self.source / name
            os.symlink(body, alias)
            self.links.append((alias, False))
            aliases.append(inspect_link_path(alias, follow_final_link=True))
        scopes = tuple(
            SimpleNamespace(
                fixed_skip=False,
                operation={"op": "replace_exact", "id": f"edit{number}"},
                accesses=(access,),
            )
            for number, access in enumerate(aliases)
        )
        self.assertEqual(patch_target_groups(scopes), ((0, 1),))
        self.assertEqual(
            apply_patch_target_decisions(scopes, ("authorize", "skip")),
            ("skip", "skip"),
        )

    def test_service_common_skip_does_not_validate_unselected_old_text(self):
        body = self.source / "body.py"
        body.write_bytes(b"first second")
        alias = self.source / "alias"
        os.symlink(body, alias)
        self.links.append((alias, False))
        boundary = "ZIPATCH_BOUNDARY_TARGET_TEST_20261123145000_K7M4"
        lines = [f'<<ZIPATCH_V3 boundary="{boundary}">>']
        for identifier, name, old in (
            ("first001", "alias", "missing-old"),
            ("second001", "body.py", "second"),
        ):
            lines.extend((
                f'---OP replace_exact id="{identifier}" count="1"',
                "---PATH", name, boundary,
                "---OLD", old, boundary,
                "---NEW", "replacement", boundary,
                "---END_OP",
            ))
        lines.append("<</ZIPATCH_V3>>")
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == body:
                raise AssertionError("共同取消的目标不得读取正文")
            return original_open(path, *arguments, **keywords)

        preview = None
        try:
            with patch("pathlib.Path.open", new=opened):
                preview = preview_patch(
                    None, self.source, "\n".join(lines),
                    backup_dir=str(self.backups),
                    normalize_text_newlines=False,
                    authorize_patch_scope=lambda scopes, root: ("authorize", "skip"),
                )
            self.assertFalse(preview.has_errors, preview.preview_text)
            self.assertEqual(preview.success_count, 0)
            self.assertEqual(preview.execution.projection.objects, ())
            self.assertEqual(body.read_bytes(), b"first second")
        finally:
            if preview is not None:
                invalidate_preview(preview)


if __name__ == "__main__":
    unittest.main()