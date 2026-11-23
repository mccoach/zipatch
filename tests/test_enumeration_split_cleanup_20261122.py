# -*- coding: utf-8 -*-

"""任务内明确枚举权限复用及拆分声明唯一权威。"""

import inspect
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from core.constants import DEFAULT_CONFIG, CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.link_paths import inspect_link_path
from core.merged_container import MergedContainer
from core.merged_format import parse_merge_blocks, validate_output_settings
from services.merge_service import merge_files
from services.restore_service import prepare_restore, split_and_restore
from services.source_authorization import EnumerationAuthorization


class SplitBoundaryContractTests(unittest.TestCase):
    def test_restore_defaults_and_interfaces_have_no_marker_settings(self):
        self.assertNotIn("code_header_line", DEFAULT_CONFIG["restore"])
        self.assertNotIn("code_footer_line", DEFAULT_CONFIG["restore"])
        self.assertNotIn("restore.code_header_line", DEFAULT_CONFIG["text_wrap"])
        self.assertNotIn("restore.code_footer_line", DEFAULT_CONFIG["text_wrap"])
        for function in (prepare_restore, split_and_restore):
            self.assertNotIn("code_header_line", inspect.signature(function).parameters)
            self.assertNotIn("code_footer_line", inspect.signature(function).parameters)
        validate_output_settings("restore", deepcopy(DEFAULT_CONFIG["restore"]))
        # 保留用户收藏类别，不以删除废弃入口为由丢弃收藏。
        self.assertIn("restore_code_header_line", DEFAULT_CONFIG["favorites"])
        self.assertIn("restore_code_footer_line", DEFAULT_CONFIG["favorites"])


@unittest.skipUnless(os.name == "nt", "正式Windows来源规划")
class EnumerationSplitCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.actual = self.root / "actual"
        self.actual.mkdir()
        (self.actual / "body.txt").write_bytes(b"text")
        (self.actual / "body.py").write_bytes(b"python")
        self.alias = self.root / "alias"
        os.symlink(self.actual, self.alias, target_is_directory=True)
        self.output = self.root / "merged.txt"
        self.links = [self.alias]

    def tearDown(self):
        try:
            for path in reversed(self.links):
                if os.path.lexists(path):
                    os.rmdir(path)
        finally:
            self.temporary.cleanup()

    def merge(self, mode, text, authorize):
        return merge_files(
            mode, str(self.alias), text, str(self.output),
            prepare_exclusions("", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
            authorize_merge_scope=authorize,
            select_merge_scope=lambda prepared, accesses, output:
            tuple("authorize" for _ in prepared),
        )

    def test_two_rules_same_prefix_authorize_enumeration_once(self):
        calls = []
        result = self.merge(
            "list", "*.txt\n*.py",
            lambda title, description: calls.append((title, description)) or True,
        )
        self.assertEqual(result.total_files, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "合并列表实际目录枚举授权")
        self.assertIn("普通子树", calls[0][1])
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            blocks = parse_merge_blocks(stream.read())
        self.assertEqual(tuple(block.body for block in blocks), ("text", "python"))

    def test_combined_directory_reuses_same_fixed_list_prefix_permission(self):
        calls = []
        result = self.merge(
            "combined", "*.txt",
            lambda title, description: calls.append(title) or True,
        )
        self.assertEqual(result.total_files, 2)
        self.assertEqual(calls, ["合并列表实际目录枚举授权"])

    def test_next_merge_requires_new_enumeration_decision(self):
        calls = []
        authorize = lambda title, description: calls.append(title) or True
        self.merge("list", "*.txt", authorize)
        self.merge("list", "*.txt", authorize)
        self.assertEqual(len(calls), 2)

    def test_distinct_nominal_aliases_do_not_hide_new_access_dependency(self):
        second = self.root / "second-alias"
        os.symlink(self.actual, second, target_is_directory=True)
        self.links.append(second)
        calls = []
        self.merge(
            "list", str(self.alias / "*.txt") + "\n" + str(second / "*.py"),
            lambda title, description: calls.append(description) or True,
        )
        self.assertEqual(len(calls), 2)
        self.assertIn(str(self.alias), calls[0])
        self.assertIn(str(second), calls[1])

    def test_changed_same_nominal_entry_cannot_reuse_approved_snapshot(self):
        calls = []
        authorization = EnumerationAuthorization(
            lambda title, description: calls.append(title) or True,
        )
        original = inspect_link_path(self.alias, follow_final_link=True)
        self.assertTrue(authorization.request(original, "first", "first"))
        other = self.root / "other"
        other.mkdir()
        os.rmdir(self.alias)
        os.symlink(other, self.alias, target_is_directory=True)
        changed = inspect_link_path(self.alias, follow_final_link=True)
        with self.assertRaisesRegex(ValueError, "固定观察变化"):
            authorization.request(changed, "second", "second")
        self.assertEqual(calls, ["first"])

    def test_custom_declared_labels_prepare_without_external_marker_arguments(self):
        target = self.root / "destination"
        target.mkdir()
        backups = self.root / "backups"
        backups.mkdir()
        with MergedContainer("本次自定义开始", "本次自定义结束") as container:
            container.add_block("new.txt", "exact\r\n尾\n\n")
            text = "".join(container.text_chunks())
        plan = prepare_restore(
            None, str(target), "overwrite",
            source_text=text, backup_dir=str(backups),
        )
        self.assertEqual(len(plan.entries), 1)
        self.assertEqual(plan.entries[0].content, "exact\r\n尾\n\n".encode("utf-8"))
        self.assertFalse((target / "new.txt").exists())
        self.assertEqual(tuple(backups.iterdir()), ())


if __name__ == "__main__":
    unittest.main()