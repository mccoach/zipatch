# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from services.demand_rules import (
    DemandCheckCancelled,
    DemandMatch,
    build_demand_selection,

    inspect_demand_rule,
    inspect_demand_rules,
    parse_demand_rule,
    windows_path_key,
)
from services.merge_service import merge_demand_files
from core.rule_lists import clean_rule_quotes


class DemandRuleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for relative in (
            "README.md",
            "docs/a.md",
            "docs/b.txt",
            "docs/sub/c.md",
            "docs/sub/README.md",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(relative, encoding="utf-8")
        self.cancel = Event()

    def inspect(self, expression):
        return inspect_demand_rule(expression, str(self.root), self.cancel)

    def test_cleaning_preserves_spaces_order_and_duplicate_rules(self):
        self.assertEqual(
            clean_rule_quotes('" README.md "\n""\n"README.md"\n"README.md"'),
            " README.md \n\nREADME.md\nREADME.md",
        )

    def test_disabled_and_empty_rules_do_not_check_filesystem(self):
        with patch(
            "services.demand_rules.os.stat",
            side_effect=AssertionError("must not inspect"),
        ):
            self.assertTrue(self.inspect('"|missing"').disabled)
            self.assertTrue(self.inspect('""').empty)

    def test_semicolon_is_not_disabled(self):
        result = self.inspect(";README.md")
        self.assertFalse(result.disabled)
        self.assertEqual(result.issue, "指定文件不存在。")

    def test_leading_space_does_not_enable_disabled_syntax(self):
        result = self.inspect(" |README.md")
        self.assertFalse(result.disabled)
        self.assertIn("路径语法错误", result.issue)

    def test_whitespace_only_rule_is_not_empty(self):
        result = self.inspect("   ")
        self.assertFalse(result.empty)
        self.assertTrue(result.issue)

    def test_drive_relative_and_incomplete_unc_are_rejected(self):
        for expression in ("C:", "C:docs\\a.md"):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ValueError, "完整绝对路径"):
                    parse_demand_rule(expression)
        for expression in ("\\\\server", "\\\\server\\"):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ValueError, "服务器名和共享名"):
                    parse_demand_rule(expression)

    def test_absolute_classification_does_not_depend_on_source_drive(self):
        for expression in (
            "C:\\docs\\a.md", "D:/docs/a.md",
            "\\\\server\\share\\docs\\a.md",
        ):
            with self.subTest(expression=expression):
                self.assertFalse(parse_demand_rule(expression).relative)

    def test_invalid_paths_and_double_stars(self):
        for expression in (
            "\\docs\\a.md",
            "\\\\?\\C:\\docs\\a.md",
            "\\\\.\\C:\\docs\\a.md",
            "..\\README.md",
            "docs\\a**\\a.md",
            "docs\\***\\a.md",
        ):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    parse_demand_rule(expression)

    def test_relative_rule_requires_explicit_source(self):
        result = inspect_demand_rule("README.md", "", self.cancel)
        self.assertIn("源文件夹", result.issue)
        result = inspect_demand_rule("README.md", "relative-source", self.cancel)
        self.assertIn("源文件夹必须是绝对路径", result.issue)

    def test_exact_name_selects_only_source_root(self):
        result = self.inspect("README.md")
        self.assertTrue(result.completed)
        self.assertEqual(result.files, (str(self.root / "README.md"),))

    def test_directory_is_not_implicitly_expanded(self):
        result = self.inspect("docs")
        self.assertFalse(result.completed)
        self.assertIn("指定路径是文件夹", result.issue)

    def test_current_level_and_recursive_patterns(self):
        current = self.inspect("docs\\*.md")
        recursive = self.inspect("docs\\**\\*.md")
        self.assertTrue(current.completed)
        self.assertTrue(recursive.completed)
        self.assertEqual(
            {Path(path).relative_to(self.root).as_posix() for path in current.files},
            {"docs/a.md"},
        )
        self.assertEqual(
            {Path(path).relative_to(self.root).as_posix() for path in recursive.files},
            {"docs/a.md", "docs/sub/c.md", "docs/sub/README.md"},
        )

    def test_explicit_recursive_same_name(self):
        result = self.inspect("**\\README.md")
        self.assertTrue(result.completed, result.issue)
        self.assertEqual(len(result.files), 2)

    def test_recursive_selection_with_zero_direntry_identity(self):
        import os
        import stat
        from types import SimpleNamespace
        import services.demand_rules as module

        original_scandir = module.os.scandir

        class Entry:
            def __init__(owner, entry):
                owner.entry = entry
                owner.name = entry.name
                owner.path = entry.path

            def stat(owner, **kwargs):
                attributes = owner.entry.stat(**kwargs)
                if stat.S_ISDIR(attributes.st_mode):
                    return SimpleNamespace(
                        st_mode=attributes.st_mode,
                        st_dev=attributes.st_dev,
                        st_ino=0,
                    )
                return attributes

        class Enumerator:
            def __init__(owner, path):
                owner.entries = original_scandir(path)

            def __enter__(owner):
                owner.entries.__enter__()
                return owner

            def __exit__(owner, *args):
                return owner.entries.__exit__(*args)

            def __next__(owner):
                return Entry(next(owner.entries))

        with patch("services.demand_rules.os.scandir", side_effect=Enumerator):
            result = self.inspect("**\\README.md")
        self.assertTrue(result.completed, result.issue)
        self.assertEqual(
            {os.path.normcase(path) for path in result.files},
            {
                os.path.normcase(str(self.root / "README.md")),
                os.path.normcase(str(self.root / "docs/sub/README.md")),
            },
        )

    def test_real_directory_link_cycle_is_rejected(self):
        import os

        link = self.root / "docs/sub/back"
        try:
            os.symlink(self.root / "docs", link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"当前环境不能创建目录链接：{error}")
        result = self.inspect("**\\README.md")
        self.assertFalse(result.completed)
        self.assertEqual(result.files, ())
        self.assertIn("目录链接循环", result.issue)

    def test_zero_hits_and_only_directories_have_distinct_diagnostics(self):
        self.assertEqual(
            self.inspect("docs\\*.missing").issue,
            "通配符未命中任何文件。",
        )
        self.assertEqual(
            self.inspect("docs\\s*").issue,
            "匹配结果只有文件夹，没有可合并文件。",
        )

    def test_access_errors_are_not_missing_or_zero_hits(self):
        with patch(
            "services.demand_rules.os.scandir",
            side_effect=PermissionError("enumeration denied"),
        ):
            result = self.inspect("docs\\*.md")
        self.assertIn("无法完整检查指定目录范围", result.issue)
        self.assertIn("enumeration denied", result.issue)
        self.assertFalse(result.completed)

    def test_cancellation_never_returns_partial_task(self):
        self.cancel.set()
        with self.assertRaises(DemandCheckCancelled):
            inspect_demand_rules(
                ((1, "README.md"), (2, "docs\\*.md")),
                str(self.root), self.cancel,
            )

    def test_duplicate_relationship_is_memory_only(self):
        first = self.inspect("docs\\**\\*.md")
        second = self.inspect("docs\\a.md")
        with patch(
            "services.demand_rules.os.stat",
            side_effect=AssertionError("duplicate calculation must not scan"),
        ):
            result = build_demand_selection([first, second])
        self.assertTrue(result.complete)
        self.assertFalse(result.has_errors)
        self.assertEqual(result.duplicates[1].hits, 1)
        self.assertEqual(result.duplicates[1].duplicates, 1)
        self.assertEqual(result.duplicates[1].additions, 0)

    def test_unknown_prefix_withholds_later_green_or_yellow(self):
        matched = self.inspect("README.md")
        result = build_demand_selection([None, matched])
        self.assertFalse(result.complete)
        self.assertFalse(result.duplicates[1].determined)
        self.assertEqual(result.duplicates[1].hits, 1)

    def test_windows_deduplication_is_case_insensitive(self):
        path = str(self.root / "README.md")
        result = build_demand_selection([
            DemandMatch((path,), completed=True),
            DemandMatch((path.lower(),), completed=True),
        ])
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.duplicates[1].duplicates, 1)
        self.assertEqual(windows_path_key(path), windows_path_key(path.lower()))

    def test_output_selection_is_rejected_before_truncation(self):
        output = self.root / "README.md"
        original = output.read_bytes()
        with self.assertRaisesRegex(ValueError, "输出文件被实际选中"):
            merge_demand_files(
                [str(output)], str(output), "", "", "HEADER", "FOOTER",
            )
        self.assertEqual(output.read_bytes(), original)

    def test_demand_source_read_failure_is_not_reported_as_success(self):
        output = self.root / "output.txt"
        with patch(
            "core.text_io.Path.read_bytes",
            side_effect=PermissionError("source denied"),
        ):
            with self.assertRaises(OSError):
                merge_demand_files(
                    [str(self.root / "README.md")],
                    str(output), "", "", "HEADER", "FOOTER",
                )


if __name__ == "__main__":
    unittest.main()