# -*- coding: utf-8 -*-

"""换行、定位、配置和正式业务字节回归；仅使用临时项目。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG
from core.rule_lists import analyze_rule_list, validate_file_rules
from core.text_locations import TextSnapshot, locate_between, locate_exact
from core.text_newlines import normalize_newlines
from services.patch_executor import PatchExecutor
from services.patch_restore import BackupRestoreExecutor
from services.restore_service import split_and_restore


class NewlineConversionTests(unittest.TestCase):
    def test_units_spaces_endings_and_idempotence(self):
        cases = (
            ("", ""), (" no newline ", " no newline "),
            ("a\r\nb\rc\n", "a\nb\nc\n"),
            ("\n\r", "\n\n"), ("\r\n\r\n", "\n\n"),
            (" \r\nx \r", " \nx \n"),
        )
        for source, lf in cases:
            for newline in ("\n", "\r\n"):
                with self.subTest(source=repr(source), newline=repr(newline)):
                    expected = lf if newline == "\n" else lf.replace("\n", "\r\n")
                    actual = normalize_newlines(source, newline)
                    self.assertEqual(actual, expected)
                    self.assertEqual(normalize_newlines(actual, newline), actual)
                    self.assertNotIn("\r\r\n", actual)

    def test_sparse_mapping_all_boundaries(self):
        snapshot = TextSnapshot.build("甲\r\n乙\r丙\n\r\n终")
        self.assertEqual(snapshot.view, "甲\n乙\n丙\n\n终")
        self.assertEqual(snapshot.collapsed_positions, (1, 6))
        for position, original in enumerate((0, 1, 3, 4, 5, 6, 7, 9, 10)):
            self.assertEqual(snapshot.original_boundary(position), original)
        self.assertEqual(TextSnapshot.build("x" * 10000).collapsed_positions, ())

    def test_equal_multiline_twice_no_recursive_replacement(self):
        source = "HEAD\nA\nB!MIDDLE\rA\r\nB!TAIL\r\n"
        location = locate_exact(TextSnapshot.build(source), "A\rB", 2)
        self.assertEqual(location.hit_count, 2)
        self.assertEqual(
            [source[start:end] for start, end in location.original_ranges],
            ["A\nB", "A\r\nB"],
        )
        self.assertEqual(
            location.compose("A\nB NEW"),
            "HEAD\nA\nB NEW!MIDDLE\rA\nB NEW!TAIL\r\n",
        )
        with self.assertRaisesRegex(ValueError, "actual=2"):
            locate_exact(TextSnapshot.build(source), "A\nB", 1)

    def test_multiline_markers_cut_complete_crlf(self):
        source = "KEEP\r\nS\r\nONE\rinside\r\nE\r\nTWO\r\nTAIL"
        location = locate_between(
            TextSnapshot.build(source), "S\nONE\n", "E\nTWO\n",
        )
        self.assertEqual(location.compose("NEW"), "KEEP\r\nNEWTAIL")

    def test_location_constraints(self):
        for source, start, end, message in (
            ("S\nS\nE", "S", "E", "不唯一"),
            ("E\nS", "S", "E", "之后"),
            ("S\nE", "", "E", "不能为空"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, message):
                    locate_between(TextSnapshot.build(source), start, end)
        with self.assertRaises(ValueError):
            locate_exact(TextSnapshot.build("X"), "", 1)

    def test_rule_and_body_line_semantics_are_separate(self):
        result = analyze_rule_list("*.cmd\r*.bat", "file")
        self.assertTrue(result.has_errors)
        with self.assertRaises(ValueError):
            validate_file_rules(result)
        disabled = analyze_rule_list("|bad\r<name>", "file")
        validate_file_rules(disabled)
        self.assertFalse(disabled.matches(("start.cmd",)))


class NewlineBusinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "project"
        self.root.mkdir()

    def operation(self, kind, path="a.txt", identifier="first", **values):
        return {"op": kind, "path": path, "id": identifier, **values}

    def preview(self, operations, rule_text="", backup=False):
        executor = PatchExecutor(
            analyze_rule_list(rule_text, "file"), self.root,
            allow_multi_replace_exact=True, backup_enabled=backup,
            backup_dir=str(self.folder / "backups"),
        )
        report = executor.preview({"version": "3.0", "operations": operations})
        self.assertEqual(report[4], 0, report[0])
        self.assertFalse(report[5], report[0])
        return executor

    def test_four_operations_complete_output_both_formats(self):
        cases = (
            ("write_file", {"content": " A\r\nB\rC\n ", "if_exists": "overwrite"},
             " A\nB\nC\n "),
            ("append_text", {"content": "\nTAIL\r\n"}, "A\nB\nTAIL\n"),
            ("replace_exact", {"old": "A\nB", "new": "X\rY", "count": "1"}, "X\nY"),
            ("replace_between", {
                "start_marker": "A\n", "end_marker": "B", "content": "X\r\nY",
             }, "X\nY"),
        )
        for rule_text, newline in (("", "\n"), ("*.cmd", "\r\n")):
            for kind, values, expected in cases:
                with self.subTest(kind=kind, newline=repr(newline)):
                    target = self.root / "a.cmd"
                    target.write_bytes(b"A\r\nB")
                    self.preview(
                        [self.operation(kind, "a.cmd", **values)], rule_text,
                    ).apply()
                    self.assertEqual(
                        target.read_bytes(),
                        expected.replace("\n", newline).encode("utf-8"),
                    )

    def test_snapshot_and_locations_reused_then_rebuilt_for_execution(self):
        target = self.root / "a.txt"
        target.write_bytes(b"A\r\nB\r\nC\r\nD")
        operations = [
            self.operation("replace_exact", old="A\nB", new="X", count="1"),
            self.operation("replace_exact", identifier="second", old="C\nD", new="Y", count="1"),
        ]
        import services.patch_executor as module
        with (
            patch.object(module, "read_text_auto", wraps=module.read_text_auto) as reader,
            patch.object(module.TextSnapshot, "build", wraps=module.TextSnapshot.build) as build,
            patch.object(module, "locate_replacement", wraps=module.locate_replacement) as locate,
        ):
            executor = self.preview(operations)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(locate.call_count, 2)
        self.assertEqual(executor.source_snapshots, {})
        self.assertEqual(executor.located_operations, [])
        target.write_bytes(b"A\rB\nC\rD")
        executor.apply()
        self.assertEqual(target.read_bytes(), b"X\nY")
        self.assertEqual(operations[0]["old"], "A\nB")

    def test_equivalent_conflict_is_global_rejection(self):
        (self.root / "a.txt").write_bytes(b"A\r\nB\r\nC")
        executor = PatchExecutor(
            analyze_rule_list("", "file"), self.root, backup_enabled=False,
        )
        result = executor.preview({
            "version": "3.0",
            "operations": [
                self.operation("replace_exact", old="A\nB", new="X", count="1"),
                self.operation("replace_exact", identifier="second", old="B\nC", new="Y", count="1"),
            ],
        })
        self.assertTrue(result[5])
        self.assertIsNone(executor.plan)

    def test_backup_and_formal_restore_keep_original_bytes(self):
        target = self.root / "a.cmd"
        original = b"A\rB\nC\r\n"
        target.write_bytes(original)
        executor = self.preview([
            self.operation("append_text", "a.cmd", content="TAIL\n"),
        ], "*.cmd", True)
        executor.apply()
        self.assertEqual(target.read_bytes(), b"A\r\nB\r\nC\r\nTAIL\r\n")
        self.assertEqual(
            (executor.backup_root / "files" / "a.cmd").read_bytes(), original,
        )
        BackupRestoreExecutor(self.root, executor.backup_root, False).apply(True)
        self.assertEqual(target.read_bytes(), original)

    def test_empty_append_join_boundary_and_fixed_skip(self):
        target = self.root / "a.txt"
        target.write_bytes(b"A\r")
        executor = self.preview([
            self.operation("append_text", content="\nB"),
            self.operation("append_text", identifier="second", content=""),
        ])
        executor.apply()
        self.assertEqual(target.read_bytes(), b"A\nB")
        self.assertEqual(len(executor.committed_text_operations), 2)
        original = b"A\r\nB\rC"
        target.write_bytes(original)
        self.preview([
            self.operation("write_file", content="UNUSED", if_exists="skip"),
        ], "*").apply()
        self.assertEqual(target.read_bytes(), original)

    def test_invalid_rules_reject_before_writing(self):
        with patch("core.atomic_file.NamedTemporaryFile") as temporary:
            with self.assertRaises(ValueError):
                PatchExecutor(analyze_rule_list("<bad>", "file"), self.root)
        temporary.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_multiple_equivalent_hits_final_crlf(self):
        target = self.root / "a.txt"
        target.write_bytes(b"KEEP:A\nB|A\r\nB:TAIL\r")
        self.preview([
            self.operation("replace_exact", old="A\nB", new="A\nB NEW", count="2"),
        ], "*").apply()
        self.assertEqual(
            target.read_bytes(), b"KEEP:A\r\nB NEW|A\r\nB NEW:TAIL\r\n",
        )

    def test_rules_do_not_scan_and_compile_only_at_preparation(self):
        import core.rule_lists as module
        with (
            patch("os.scandir", side_effect=AssertionError("must not scan")),
            patch.object(module, "_compile_segment", wraps=module._compile_segment) as compile_segment,
        ):
            rules = analyze_rule_list(
                "*.bat\nscripts\\*.cmd\ntools\\**\\*.bat\n:\\README\n|<bad>", "file",
            )
            validate_file_rules(rules)
            count = compile_segment.call_count
            for _ in range(10):
                self.assertTrue(rules.matches(("scripts", "start.CMD")))
                self.assertTrue(rules.matches(("x", "tools", "sub", "run.bat")))
                self.assertTrue(rules.matches(("README",)))
                self.assertFalse(rules.matches(("x", "README")))
                self.assertFalse(rules.matches(("x", "start.cmd")))
            self.assertEqual(compile_segment.call_count, count)

    def test_split_uses_actual_renamed_name_and_existing_rstrip(self):
        source = self.folder / "merged.txt"
        source.write_bytes(
            "---第1个文件---\na.cmd\nHEADER\n A\r\nB \n\nFOOTER\n".encode("utf-8")
        )
        output = self.folder / "restored"
        output.mkdir()
        (output / "a.cmd").write_bytes(b"OLD")
        result = split_and_restore(
            analyze_rule_list("\\a_1.cmd", "file"),
            source, output, "HEADER", "FOOTER", "rename",
        )
        self.assertEqual(result.restored_count, 1)
        self.assertEqual((output / "a.cmd").read_bytes(), b"OLD")
        self.assertEqual((output / "a_1.cmd").read_bytes(), b"A\r\nB")
        result = split_and_restore(
            analyze_rule_list("*", "file"),
            source, output, "HEADER", "FOOTER", "skip",
        )
        self.assertEqual(result.restored_count, 0)
        self.assertEqual((output / "a.cmd").read_bytes(), b"OLD")

    def test_new_empty_files_and_all_disabled_rules(self):
        for name, content in (("empty.cmd", ""), ("plain.cmd", "文字")):
            with self.subTest(name=name):
                self.preview([
                    self.operation("write_file", name, content=content),
                ], "|*.cmd").apply()
                self.assertEqual((self.root / name).read_bytes(), content.encode("utf-8"))

    def test_nontext_copy_does_not_convert_bytes(self):
        original = b"A\rB\nC\r\n\xff"
        (self.root / "source.cmd").write_bytes(original)
        self.preview([
            self.operation("copy_file", "source.cmd", new_path="target.cmd"),
        ], "*.cmd").apply()
        self.assertEqual((self.root / "target.cmd").read_bytes(), original)


class NewlineConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "config.json"

    def load(self, source):
        self.path.write_text(json.dumps(source), encoding="utf-8")
        return load_user_config(self.path)

    def test_missing_defaults_and_explicit_empty(self):
        loaded, _ = self.load({})
        self.assertEqual(loaded["settings"]["crlf_file_list"], "*.bat\n*.CMD")
        self.assertEqual(loaded["favorites"]["crlf_file_list"], [])
        self.assertTrue(loaded["text_wrap"]["settings.crlf_file_list"])
        source = deep_merge_config(DEFAULT_CONFIG, {})
        source["settings"]["crlf_file_list"] = ""
        source["text_wrap"]["settings.crlf_file_list"] = False
        loaded, changed = self.load(source)
        self.assertFalse(changed)
        self.assertEqual(loaded["settings"]["crlf_file_list"], "")
        self.assertFalse(loaded["text_wrap"]["settings.crlf_file_list"])

    def test_structure_errors_preserve_disk(self):
        cases = (
            {"settings": []},
            {"settings": {"crlf_file_list": None}},
            {"settings": {"crlf_file_list": False}},
            {"favorites": {"crlf_file_list": {}}},
            {"favorites": {"crlf_file_list": [None]}},
            {"favorites": {"crlf_file_list": [{"name": "x", "content": 1}]}},
            {"text_wrap": []},
            {"text_wrap": {"settings.crlf_file_list": "false"}},
        )
        for source in cases:
            with self.subTest(source=source):
                self.path.write_text(json.dumps(source), encoding="utf-8")
                original = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    load_user_config(self.path)
                self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_rule_and_favorite_bodies_preserved(self):
        source = deep_merge_config(DEFAULT_CONFIG, {})
        raw = '\n"<bad>"\n|bad\rname\n README '
        source["settings"]["crlf_file_list"] = raw
        source["favorites"]["crlf_file_list"] = [{"name": "raw", "content": raw}]
        loaded, changed = self.load(source)
        self.assertFalse(changed)
        self.assertEqual(loaded["settings"]["crlf_file_list"], raw)
        self.assertEqual(loaded["favorites"]["crlf_file_list"][0]["content"], raw)


if __name__ == "__main__":
    unittest.main()