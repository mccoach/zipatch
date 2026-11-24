# -*- coding: utf-8 -*-

"""文案收尾专项：静态检查，不启动GUI，不执行文件修改业务。"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

MODIFIED_FILES = (
    "panels/restore_panel.py",
    "services/restore_service.py",
    "panels/backup_restore_panel.py",
    "ui/backup_restore.py",
    "ui/recovery_selection.py",
    "services/patch_restore.py",
    "ui/patch_link_authorization.py",
    "ui/restore_scope.py",
    "ui/exclusion_editor.py",
    "ui/newline_settings.py",
    "ui/text_output_settings.py",
    "services/source_traversal.py",
    "services/merge_service.py",
    "panels/merge_panel.py",
    "services/scan_service.py",
    "services/patch_executor.py",
)


def source(relative):
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def named_literal(relative, name):
    tree = ast.parse(source(relative), filename=relative)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                return ast.literal_eval(node.value)
    raise AssertionError("Missing assignment: " + name)


class WordingCloseoutTests(unittest.TestCase):
    def test_modified_sources_compile(self):
        for relative in MODIFIED_FILES:
            with self.subTest(path=relative):
                compile(
                    (ROOT / relative).read_bytes(),
                    str(ROOT / relative),
                    "exec",
                )

    def test_split_page_reports_have_no_retired_folder_phrases(self):
        text = source("panels/restore_panel.py")
        for retired in (
            "普通目录要求",
            "实际创建目录",
            "强制备份目录",
            "指定目录下的大写盘符字母目录",
            "无实际工作，目录要求已满足",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, text)
        self.assertIn("普通文件夹要求", text)
        self.assertIn("本次备份路径", text)

    def test_backup_preview_uses_page_wording(self):
        text = source("ui/backup_restore.py")
        self.assertNotIn("备份恢复独立预检", text)
        self.assertNotIn("恢复独立预检已结束", text)
        self.assertIn("备份还原 - 独立预演", text)
        self.assertIn("备份还原 - 仅预演", text)

    def test_exclusion_reason_producer_and_consumer_agree(self):
        producer = source("services/source_traversal.py")
        consumer = source("services/merge_service.py")
        self.assertNotIn('"排除文件名单"', producer)
        self.assertNotIn('"排除文件名单"', consumer)
        self.assertIn(
            '"文件夹排除名单" if directory else "文件排除名单"',
            producer,
        )
        self.assertIn(
            'if kind == "file" and reason == "文件排除名单":',
            consumer,
        )

    def test_help_remaining_names(self):
        exclusion = named_literal(
            "ui/exclusion_editor.py", "EXCLUSION_RULES_TEXT",
        )
        newline = named_literal(
            "ui/newline_settings.py", "NEWLINE_HELP",
        )
        output = named_literal(
            "ui/text_output_settings.py", "OUTPUT_HELP",
        )
        self.assertNotIn("只有文件名单中的", exclusion)
        self.assertNotIn("冒号只能作为文件名单", exclusion)
        self.assertNotIn("备份恢复", newline)
        self.assertNotIn("子目录", newline)
        for retired in (
            "修改包目录枚举",
            "一个完整目录树是一个对象",
            "恢复目录树可以移除",
            "完整准备及执行确认之前不创建工作目录",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, output)

    def test_protocol_mode_and_recovery_values_are_preserved(self):
        self.assertEqual(
            named_literal("core/constants.py", "MERGE_MODES"),
            ("directory", "list", "combined"),
        )
        self.assertEqual(
            named_literal("core/constants.py", "PATCH_PROTOCOL_NAME"),
            "ZIPATCH_V3",
        )
        self.assertEqual(
            named_literal("core/recovery_document.py", "VERSION"),
            4,
        )
        merged = source("core/merged_format.py")
        self.assertIn('SOURCE_PREFIX = "实际路径："', merged)
        self.assertIn('MTIME_PREFIX = "最后修改时间："', merged)
        recovery = source("core/recovery_document.py")
        self.assertIn(
            '("prepared", "started", "completed", "uncertain")',
            recovery,
        )

    def test_merge_page_mode_labels_match_action_names(self):
        text = source("panels/merge_panel.py")
        self.assertIn(
            '"directory": "合并文件夹", "list": "合并文件", '
            '"combined": "组合合并"',
            text,
        )

    def test_patch_report_path_names(self):
        text = source("services/patch_executor.py")
        self.assertIn('self.log(f"项目根文件夹：{self.root}")', text)
        self.assertNotIn('self.log(f"项目根目录：{self.root}")', text)
        self.assertNotIn('f"备份目录：{self.backup_root', text)


if __name__ == "__main__":
    unittest.main()