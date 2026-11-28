# -*- coding: utf-8 -*-

"""只读文案专项；不启动Tk，不修改文件，不执行业务任务。"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative):
    return (ROOT / relative).read_bytes().decode("utf-8-sig")


def tree(relative):
    return ast.parse(source(relative), filename=relative)


def assigned_literal(relative, name):
    for node in tree(relative).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Assignment not found: {relative}: {name}")


class WordingHelpTests(unittest.TestCase):
    def test_modified_sources_compile(self):
        files = (
            "ui/demand_editor.py",
            "ui/exclusion_editor.py",
            "core/exclusion_rules.py",
            "core/rule_lists.py",
            "ui/newline_settings.py",
            "ui/text_output_settings.py",
            "ui/work_scope.py",
            "ui/patch_link_authorization.py",
            "ui/source_scope.py",
            "ui/backup_restore.py",
            "services/patch_backup.py",
        )
        for relative in files:
            with self.subTest(file=relative):
                compile(source(relative), relative, "exec")

    def test_source_help_names_and_execution_rules(self):
        help_text = assigned_literal("ui/demand_editor.py", "SOURCE_LIST_HELP")
        self.assertNotIn("目录", help_text)
        for text in (
            "文件夹列表与文件列表",
            "“合并文件夹”",
            "“合并文件”",
            "“组合合并”",
            "上级和下级文件夹不擅自归并",
            "本次使用的列表任一错误，整次合并停止",
        ):
            self.assertIn(text, help_text)

    def test_help_has_no_retired_merge_button_names(self):
        for relative, name in (
            ("ui/exclusion_editor.py", "EXCLUSION_RULES_TEXT"),
            ("ui/newline_settings.py", "NEWLINE_HELP"),
            ("ui/text_output_settings.py", "OUTPUT_HELP"),
        ):
            help_text = assigned_literal(relative, name)
            for old in ("仅目录合并", "仅列表合并", "目录+列表合并"):
                with self.subTest(file=relative, old=old):
                    self.assertNotIn(old, help_text)
        help_text = assigned_literal("ui/text_output_settings.py", "OUTPUT_HELP")
        for text in (
            "合并文件夹",
            "合并文件",
            "组合合并",
            "“记录修改时间”",
            "“显示文件大小”",
            "“显示修改时间”",
            "“还原前备份位置”",
        ):
            self.assertIn(text, help_text)

    def test_link_group_labels_match_without_importing_ui(self):
        labels = assigned_literal("ui/work_scope.py", "LINK_LABELS")
        order = assigned_literal("ui/work_scope.py", "GROUP_ORDER")
        groups = assigned_literal("ui/patch_link_authorization.py", "LINK_GROUPS")
        self.assertEqual(dict(groups), labels)
        self.assertTrue(set(labels.values()).issubset(order))
        self.assertEqual(labels, {
            "file_hardlink": "文件硬链接",
            "file_symlink": "文件软链接",
            "directory_symlink": "文件夹软链接",
            "junction": "文件夹联接（Junction）",
        })

    def test_exclusion_types_and_diagnostic_label(self):
        fields = assigned_literal("ui/exclusion_editor.py", "EXCLUSION_FIELDS")
        self.assertEqual(fields, (
            ("exclude_folders", "文件夹排除名单", "directory"),
            ("exclude_files", "文件排除名单", "file"),
        ))
        from core.rule_lists import analyze_rule_list

        result = analyze_rule_list(":README", "directory", semantics="exclusion")
        self.assertTrue(result.has_errors)
        self.assertIn(
            "文件夹名单不支持无扩展名条件",
            result.diagnostics[0].reason,
        )


if __name__ == "__main__":
    unittest.main()