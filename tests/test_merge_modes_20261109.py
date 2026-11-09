# -*- coding: utf-8 -*-

"""统一合并模式的真实文件发布及配置契约回归。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager, deep_merge_config, normalize_config
from core.constants import DEFAULT_CONFIG
from core.exclusion_rules import prepare_exclusions
from core.merged_format import OutputSettingsError, parse_file_blocks
from core.rule_lists import analyze_rule_list
from services.demand_rules import DemandMatch
from services.merge_service import merge_files


HEADER = "---TEST_CODE_BEGIN_20261109---"
FOOTER = "---TEST_CODE_END_20261109---"


class MergeModesRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "project"
        self.source.mkdir()
        self.external = self.base / "external"
        self.external.mkdir()
        self.output = self.base / "merged.txt"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content, *, external=False):
        root = self.external if external else self.source
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def merge(self, mode, **overrides):
        parameters = {
            "mode": mode,
            "source_folder": str(self.source),
            "list_text": "",
            "output_file": str(self.output),
            "exclusions": prepare_exclusions("", ""),
            "preamble_text": "UNIFIED_PREAMBLE",
            "ending_text": "UNIFIED_ENDING",
            "code_header_line": HEADER,
            "code_footer_line": FOOTER,
            "record_file_mtime": False,
        }
        parameters.update(overrides)
        return merge_files(**parameters)

    def blocks(self):
        return parse_file_blocks(
            self.output.read_bytes().decode("utf-8"), HEADER, FOOTER,
        )

    def select_files(self, paths):
        # 名单语法已有独立职责；这里只替换选择边界，
        # 其后去重、源码读取、容器构建和发布全部使用真实实现。
        return patch(
            "services.merge_service.inspect_demand_rules",
            return_value={
                1: DemandMatch(tuple(map(str, paths)), completed=True),
            },
        )

    def test_directory_modes_reject_empty_source_before_selection(self):
        self.output.write_bytes(b"OLD_OUTPUT")
        for mode in ("directory", "combined"):
            with self.subTest(mode=mode), patch(
                "services.merge_service.inspect_demand_rules",
                side_effect=AssertionError("空目录来源不得开始列表检查"),
            ), patch(
                "services.merge_service.build_regular_merge_plan",
                side_effect=AssertionError("空目录来源不得遍历当前工作目录"),
            ):
                with self.assertRaisesRegex(ValueError, "源文件夹不能为空"):
                    self.merge(
                        mode, source_folder="", list_text="selected",
                    )
            self.assertEqual(self.output.read_bytes(), b"OLD_OUTPUT")

    def test_missing_directory_source_does_not_publish(self):
        self.output.write_bytes(b"OLD_OUTPUT")
        with self.assertRaises(ValueError):
            self.merge(
                "directory", source_folder=str(self.base / "missing"),
            )
        self.assertEqual(self.output.read_bytes(), b"OLD_OUTPUT")

    def test_directory_does_not_inspect_unused_list(self):
        target = self.write("a.py", b"a\n")
        with patch(
            "services.merge_service.inspect_demand_rules",
            side_effect=AssertionError("仅目录不得检查未使用列表"),
        ):
            result = self.merge("directory", list_text="C:invalid")
        self.assertTrue(result.output_result.succeeded)
        self.assertEqual(self.blocks(), [(str(target), "a\n")])

    def test_list_does_not_walk_directory_or_use_exclusions(self):
        target = self.write("outside.py", b"outside", external=True)
        with self.select_files((target,)), patch(
            "services.merge_service.build_regular_merge_plan",
            side_effect=AssertionError("仅列表不得遍历目录"),
        ):
            result = self.merge(
                "list", source_folder="", list_text="selected",
                exclusions=None,
            )
        self.assertEqual(result.total_files, 1)
        self.assertEqual(self.blocks(), [(str(target), "outside")])

    def test_combined_directory_first_list_append_and_dedup(self):
        first = self.write("a.py", b"A")
        second = self.write("b.py", b"B")
        extra = self.write("extra.py", b"EXTRA", external=True)
        with self.select_files((second, extra, first, extra)):
            result = self.merge("combined", list_text="selected")
        self.assertEqual(result.total_files, 3)
        self.assertEqual(
            self.blocks(),
            [(str(first), "A"), (str(second), "B"), (str(extra), "EXTRA")],
        )

    def test_list_explicit_inclusion_overrides_directory_file_exclusion(self):
        target = self.write("excluded.py", b"REAL_CONTENT")
        with self.select_files((target,)):
            result = self.merge(
                "combined", list_text="selected",
                exclusions=prepare_exclusions("", "*.py"),
            )
        self.assertEqual(result.excluded_count, 0)
        self.assertEqual(result.total_files, 1)
        self.assertEqual(self.blocks(), [(str(target), "REAL_CONTENT")])

    def test_list_can_include_file_under_excluded_directory(self):
        regular = self.write("a.py", b"A")
        selected = self.write("ignored/selected.py", b"SELECTED")
        self.write("ignored/unselected.py", b"UNSELECTED")
        with self.select_files((selected,)):
            self.merge(
                "combined", list_text="selected",
                exclusions=prepare_exclusions("ignored", ""),
            )
        self.assertEqual(
            self.blocks(),
            [(str(regular), "A"), (str(selected), "SELECTED")],
        )

    def test_all_modes_use_shared_extra_text(self):
        target = self.write("a.py", b"A")
        for mode in ("directory", "list", "combined"):
            with self.subTest(mode=mode):
                if mode == "directory":
                    self.merge(mode)
                else:
                    with self.select_files((target,)):
                        self.merge(mode, list_text="selected")
                text = self.output.read_bytes().decode("utf-8")
                self.assertEqual(text.count("UNIFIED_PREAMBLE"), 1)
                self.assertEqual(text.count("UNIFIED_ENDING"), 1)

    def test_empty_combined_list_allowed_but_empty_list_only_rejected(self):
        target = self.write("a.py", b"A")
        self.merge("combined")
        self.assertEqual(self.blocks(), [(str(target), "A")])
        previous = self.output.read_bytes()
        with self.assertRaises(ValueError):
            self.merge("list")
        self.assertEqual(self.output.read_bytes(), previous)

    def test_list_error_rejects_entire_combined_output(self):
        self.write("a.py", b"A")
        self.output.write_bytes(b"OLD_OUTPUT")
        with patch(
            "services.merge_service.inspect_demand_rules",
            return_value={1: DemandMatch(issue="指定文件不存在。")},
        ):
            with self.assertRaisesRegex(ValueError, "第 1 行"):
                self.merge("combined", list_text="missing")
        self.assertEqual(self.output.read_bytes(), b"OLD_OUTPUT")

    def test_output_explicitly_selected_is_rejected(self):
        self.output.write_bytes(b"OLD_OUTPUT")
        with self.select_files((self.output,)):
            with self.assertRaisesRegex(ValueError, "输出文件"):
                self.merge("combined", list_text="selected")
        self.assertEqual(self.output.read_bytes(), b"OLD_OUTPUT")

    def test_combined_external_file_newline_matching(self):
        regular = self.write("root.cmd", b"ROOT\n")
        external = self.write("root.cmd", b"EXTERNAL\r\n", external=True)
        rules = analyze_rule_list(r"\*.cmd", "file")
        with self.select_files((external,)):
            self.merge(
                "combined", list_text="selected",
                normalize_text_newlines=True, output_rules=rules,
            )
        self.assertEqual(
            self.blocks(),
            [(str(regular), "ROOT\r\n"), (str(external), "EXTERNAL\r\n")],
        )

    def test_combined_internal_subdirectory_keeps_root_rule_semantics(self):
        regular = self.write("scripts/start.cmd", b"INTERNAL\r\n")
        external = self.write("start.cmd", b"EXTERNAL\n", external=True)
        rules = analyze_rule_list(r"\*.cmd", "file")
        with self.select_files((regular, external)):
            self.merge(
                "combined", list_text="selected",
                normalize_text_newlines=True, output_rules=rules,
            )
        self.assertEqual(
            self.blocks(),
            [(str(regular), "INTERNAL\n"), (str(external), "EXTERNAL\r\n")],
        )

    def test_invalid_mode_does_not_publish(self):
        self.output.write_bytes(b"OLD_OUTPUT")
        with self.assertRaises(ValueError):
            self.merge("unknown")
        self.assertEqual(self.output.read_bytes(), b"OLD_OUTPUT")

    def test_mode_persistence_and_deprecated_extra_fields_removed(self):
        config = normalize_config(deep_merge_config(DEFAULT_CONFIG, {
            "merge": {
                "demand_file_list_text": "a.py\n\n",
                "demand_preamble_text": "UNUSED_PREAMBLE",
                "demand_ending_text": "UNUSED_ENDING",
            },
        }))
        self.assertEqual(config["merge"]["merge_mode"], "directory")
        self.assertNotIn("demand_preamble_text", config["merge"])
        self.assertNotIn("demand_ending_text", config["merge"])
        self.assertEqual(config["merge"]["demand_file_list_text"], "a.py\n\n")
        manager = ConfigSaveManager(config, path=self.base / "config.json")
        with self.assertRaises(OutputSettingsError):
            manager.accept(config["merge"], {"merge_mode": "unknown"})
        self.assertEqual(config["merge"]["merge_mode"], "directory")
        manager.accept(config["merge"], {"merge_mode": "combined"})
        self.assertTrue(manager.save().persisted)
        from core.config import load_user_config
        restored, _ = load_user_config(self.base / "config.json")
        self.assertEqual(restored["merge"]["merge_mode"], "combined")

    @unittest.skipUnless(os.name == "nt", "真实Windows列表路径测试")
    def test_real_absolute_list_with_no_source_directory(self):
        target = self.write("absolute.py", b"ABSOLUTE", external=True)
        result = self.merge(
            "list", source_folder="", list_text=str(target), exclusions=None,
        )
        self.assertEqual(result.total_files, 1)
        self.assertEqual(self.blocks(), [(str(target), "ABSOLUTE")])

    @unittest.skipUnless(os.name == "nt", "真实Windows列表路径测试")
    def test_real_combined_relative_absolute_and_repeated_rules(self):
        regular = self.write("a.py", b"A")
        external = self.write("extra.py", b"EXTRA", external=True)
        result = self.merge(
            "combined",
            list_text=f"a.py\n{external}\na.py\n{external}\n",
        )
        self.assertEqual(result.total_files, 2)
        self.assertEqual(
            self.blocks(),
            [(str(regular), "A"), (str(external), "EXTRA")],
        )

    def test_real_relative_list_requires_source_directory(self):
        self.output.write_bytes(b"OLD_OUTPUT")
        with self.assertRaisesRegex(ValueError, "源文件夹"):
            self.merge(
                "list", source_folder="", list_text="a.py", exclusions=None,
            )
        self.assertEqual(self.output.read_bytes(), b"OLD_OUTPUT")


if __name__ == "__main__":
    unittest.main()
