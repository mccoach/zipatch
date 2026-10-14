# -*- coding: utf-8 -*-

"""次数与目标决定复用、失败预演资格、服务输入和窗口错误契约。"""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from tkinter import TclError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.paths import center_window
from core.rule_lists import analyze_rule_list, validate_file_rules
from panels.restore_panel import RestorePanel
from services.patch_executor import PatchExecutor
from services.patch_parser import parse_patch_v3
from services.restore_service import split_and_restore


BOUNDARY = "ZIPATCH_BOUNDARY_COUNT_TEST_20261012193500_C91D3A7E204B"


class NewlineCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def executor(self, rules=None, *, allow_multi=False, backup=False):
        return PatchExecutor(
            analyze_rule_list("", "file") if rules is None else rules,
            self.root,
            allow_multi_replace_exact=allow_multi,
            backup_enabled=backup,
            backup_dir=str(self.root / "backups"),
        )

    def exact(self, count="1"):
        return {
            "id": "exact", "op": "replace_exact", "path": "a.txt",
            "old": "A\nB", "new": "X", "count": count,
        }

    def assert_failed_preview_has_no_execution(self, executor):
        self.assertEqual(executor.source_snapshots, {})
        self.assertEqual(executor.located_operations, [])
        self.assertIsNone(executor.plan)
        self.assertEqual(executor.text_newlines, {})
        self.assertIsNone(executor.protection)
        self.assertIsNone(executor.backup_root)
        self.assertEqual(executor.preview_text, "")
        self.assertTrue(executor.previewed)
        self.assertFalse(executor.consumed)
        self.assertEqual(executor.committed_text_operations, [])
        self.assertEqual(executor.completed_actions, set())
        with patch.object(executor, "perform_action") as business:
            with self.assertRaisesRegex(ValueError, "没有通过正式预演"):
                executor.apply()
        business.assert_not_called()
        self.assertFalse((self.root / "backups").exists())

    def test_single_and_multiple_counts_are_parsed_once_per_executor_stage(self):
        import services.patch_executor as module

        target = self.root / "a.txt"
        for count, source, expected in (
            ("1", b"A\r\nB", b"X"),
            ("2", b"A\r\nB|A\nB", b"X|X"),
        ):
            with self.subTest(count=count):
                target.write_bytes(source)
                operation = self.exact(count)
                original = deepcopy(operation)
                executor = self.executor(allow_multi=True)
                with (
                    patch.object(
                        module, "parse_required_positive_int",
                        wraps=module.parse_required_positive_int,
                    ) as parse_count,
                    patch.object(
                        module, "locate_replacement",
                        wraps=module.locate_replacement,
                    ) as locate,
                ):
                    report = executor.preview({
                        "version": "3.0", "operations": [operation],
                    })
                    self.assertEqual(report[4], 0, report[0])
                    self.assertEqual(parse_count.call_count, 1)
                    self.assertEqual(locate.call_count, 1)
                    self.assertEqual(locate.call_args.args[2], int(count))
                    self.assertEqual(executor.source_snapshots, {})
                    self.assertEqual(executor.located_operations, [])
                    executor.apply()
                    self.assertEqual(parse_count.call_count, 2)
                    self.assertEqual(locate.call_count, 2)
                    self.assertEqual(locate.call_args.args[2], int(count))
                self.assertEqual(target.read_bytes(), expected)
                self.assertEqual(operation, original)

    def test_multiple_count_permission_rejection_precedes_location(self):
        import services.patch_executor as module

        target = self.root / "a.txt"
        original_bytes = b"A\r\nB|A\nB"
        target.write_bytes(original_bytes)
        operation = self.exact("2")
        original_operation = deepcopy(operation)
        executor = self.executor()
        with (
            patch.object(
                module, "parse_required_positive_int",
                wraps=module.parse_required_positive_int,
            ) as parse_count,
            patch.object(module, "locate_replacement") as locate,
            patch("core.atomic_file.NamedTemporaryFile") as temporary,
        ):
            report = executor.preview({
                "version": "3.0", "operations": [operation],
            })
            self.assertEqual(report[3], 0)
            self.assertEqual(report[4], 1)
            self.assertIn("允许多处精确替换", report[0])
            parse_count.assert_called_once_with("2", "count")
            locate.assert_not_called()
            with self.assertRaises(ValueError):
                executor.apply()
            temporary.assert_not_called()
        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(operation, original_operation)
        self.assertEqual(executor.source_snapshots, {})
        self.assertEqual(executor.located_operations, [])

    def test_invalid_counts_are_rejected_without_mutation_or_location(self):
        import services.patch_executor as module

        target = self.root / "a.txt"
        target.write_bytes(b"A\r\nB")
        for invalid in (None, "", "0", "-1", "bad", "1.5"):
            with self.subTest(count=invalid):
                operation = self.exact(invalid)
                original_operation = deepcopy(operation)
                executor = self.executor(allow_multi=True)
                with (
                    patch.object(
                        module, "parse_required_positive_int",
                        wraps=module.parse_required_positive_int,
                    ) as parse_count,
                    patch.object(module, "locate_replacement") as locate,
                ):
                    report = executor.preview({
                        "version": "3.0", "operations": [operation],
                    })
                self.assertEqual(report[3], 0)
                self.assertEqual(report[4], 1)
                parse_count.assert_called_once_with(invalid, "count")
                locate.assert_not_called()
                self.assertIsNone(executor.plan)
                with self.assertRaises(ValueError):
                    executor.apply()
                self.assertEqual(operation, original_operation)
                self.assertEqual(target.read_bytes(), b"A\r\nB")

    def test_v3_parser_retains_independent_count_format_validation(self):
        import services.patch_parser as module

        for count, valid in (("1", True), ("2", True), ("0", False), ("bad", False)):
            with self.subTest(count=count):
                package = "\n".join((
                    f'<<ZIPATCH_V3 boundary="{BOUNDARY}">>',
                    f'---OP replace_exact id="exact" count="{count}"',
                    "---PATH", "a.txt", BOUNDARY,
                    "---OLD", "A\nB", BOUNDARY,
                    "---NEW", "X", BOUNDARY,
                    "---END_OP", "<</ZIPATCH_V3>>",
                ))
                with patch.object(
                    module, "parse_required_positive_int",
                    wraps=module.parse_required_positive_int,
                ) as parse_count:
                    if valid:
                        parsed = parse_patch_v3(package)
                        self.assertEqual(parsed["operations"][0]["count"], count)
                    else:
                        with self.assertRaises(ValueError):
                            parse_patch_v3(package)
                parse_count.assert_called_once_with(count, "count")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_output_decision_is_once_per_unique_executed_target(self):
        first = self.root / "a.cmd"
        second = self.root / "b.txt"
        skipped = self.root / "skip.cmd"
        first.write_bytes(b"A\r")
        second.write_bytes(b"C\r")
        skipped.write_bytes(b"KEEP\r")
        rules = analyze_rule_list("*.cmd", "file")
        executor = self.executor(rules)
        operations = [
            {"id": "first", "op": "append_text", "path": "a.cmd", "content": "\nB"},
            {"id": "second", "op": "append_text", "path": "a.cmd", "content": ""},
            {"id": "third", "op": "append_text", "path": "b.txt", "content": "\nD"},
            {
                "id": "skip", "op": "write_file", "path": "skip.cmd",
                "content": "UNUSED", "if_exists": "skip",
            },
        ]
        original_operations = deepcopy(operations)
        original_match = type(rules).matches
        matched = []

        def matches(result, parts):
            matched.append(tuple(parts))
            return original_match(result, parts)

        with patch.object(type(rules), "matches", new=matches):
            report = executor.preview({
                "version": "3.0", "operations": operations,
            })
            self.assertEqual(report[4], 0, report[0])
            self.assertEqual(matched, [("a.cmd",), ("b.txt",)])
            self.assertEqual(
                executor.text_newlines,
                {first: "\r\n", second: "\n"},
            )
            executor.apply()
            self.assertEqual(matched, [("a.cmd",), ("b.txt",)])
        self.assertEqual(first.read_bytes(), b"A\r\nB")
        self.assertEqual(second.read_bytes(), b"C\nD")
        self.assertEqual(skipped.read_bytes(), b"KEEP\r")
        self.assertEqual(len(executor.committed_text_operations), 3)
        self.assertTrue(any('[跳过] id="skip"' in line for line in executor.logs))
        self.assertEqual(operations, original_operations)

    def test_preview_releases_and_invalidates_when_location_raises(self):
        target = self.root / "a.txt"
        target.write_bytes(b"A\r\nB")
        executor = self.executor(backup=True)
        original_error = RuntimeError("location defect")

        def fail(snapshot, operation, expected_count):
            self.assertEqual(len(executor.source_snapshots), 1)
            self.assertEqual(snapshot.source, "A\r\nB")
            self.assertEqual(expected_count, 1)
            raise original_error

        with patch(
            "services.patch_executor.locate_replacement", side_effect=fail,
        ):
            with self.assertRaises(RuntimeError) as caught:
                executor.preview({
                    "version": "3.0", "operations": [self.exact()],
                })
        self.assertIs(caught.exception, original_error)
        self.assert_failed_preview_has_no_execution(executor)
        self.assertEqual(target.read_bytes(), b"A\r\nB")

    def test_preview_releases_and_invalidates_when_global_check_raises(self):
        target = self.root / "a.txt"
        target.write_bytes(b"A\r\nB")
        executor = self.executor(backup=True)
        original_error = RuntimeError("global check defect")

        def fail(locations):
            self.assertEqual(len(locations), 1)
            self.assertEqual(len(executor.source_snapshots), 1)
            raise original_error

        with patch(
            "services.patch_executor.collect_content_contract_errors",
            side_effect=fail,
        ):
            with self.assertRaises(RuntimeError) as caught:
                executor.preview({
                    "version": "3.0", "operations": [self.exact()],
                })
        self.assertIs(caught.exception, original_error)
        self.assert_failed_preview_has_no_execution(executor)
        self.assertEqual(target.read_bytes(), b"A\r\nB")

    def test_report_failure_removes_prepared_plan_and_protection(self):
        target = self.root / "a.txt"
        target.write_bytes(b"A\r\nB")
        executor = self.executor(backup=True)
        original_error = RuntimeError("report defect")
        prepared_roots = []

        def fail(**arguments):
            self.assertEqual(len(executor.source_snapshots), 1)
            self.assertEqual(len(executor.located_operations), 1)
            self.assertIsNotNone(executor.plan)
            self.assertEqual(executor.text_newlines, {target: "\n"})
            self.assertIsNotNone(executor.protection)
            self.assertFalse(executor.protection.started)
            self.assertEqual(arguments["backup_root"], executor.backup_root)
            prepared_roots.append(executor.backup_root)
            self.assertFalse(executor.backup_root.exists())
            raise original_error

        with (
            patch("services.patch_executor.format_patch_preview", side_effect=fail),
            patch("core.atomic_file.NamedTemporaryFile") as temporary,
        ):
            with self.assertRaises(RuntimeError) as caught:
                executor.preview({
                    "version": "3.0", "operations": [self.exact()],
                })
            self.assertIs(caught.exception, original_error)
            self.assert_failed_preview_has_no_execution(executor)
            temporary.assert_not_called()
        self.assertEqual(len(prepared_roots), 1)
        self.assertFalse(prepared_roots[0].exists())
        self.assertEqual(target.read_bytes(), b"A\r\nB")
        with self.assertRaisesRegex(ValueError, "不能重新规划或复用"):
            executor.preview({
                "version": "3.0", "operations": [self.exact()],
            })

    def test_successful_preview_releases_work_but_keeps_fixed_execution(self):
        target = self.root / "a.txt"
        target.write_bytes(b"A\r\nB")
        executor = self.executor()
        report = executor.preview({
            "version": "3.0", "operations": [self.exact()],
        })
        self.assertEqual(report[4], 0, report[0])
        self.assertEqual(executor.source_snapshots, {})
        self.assertEqual(executor.located_operations, [])
        self.assertIsNotNone(executor.plan)
        self.assertEqual(executor.text_newlines, {target: "\n"})
        self.assertEqual(executor.preview_text, report[0])
        executor.apply()
        self.assertEqual(target.read_bytes(), b"X")

    def test_restore_panel_and_service_validate_same_rules_once(self):
        source = self.root / "merged.txt"
        target = self.root / "restored"
        source.write_bytes(
            "---第1个文件---\na.txt\nHEADER\nA\nB\nFOOTER\n".encode("utf-8")
        )
        panel = SimpleNamespace(
            root=None,
            config_data={"settings": {"crlf_file_list": ""}},
            prepare=Mock(return_value={
                "source_txt_file": str(source),
                "target_folder": str(target),
                "code_header_line": "HEADER",
                "code_footer_line": "FOOTER",
                "existing_file_policy": "overwrite",
                "open_after_done": False,
            }),
            set_status=Mock(),
            log=Mock(),
            handle_error=Mock(),
            commits=SimpleNamespace(
                metrics={"business_starts": 0},
                prepare_rule=lambda config, field, kind:
                analyze_rule_list(config[field], kind),
            ),
        )
        import services.restore_service as module

        with (
            patch.object(
                module, "validate_file_rules", wraps=module.validate_file_rules,
            ) as validate,
            patch("panels.restore_panel.safe_show_info"),
        ):
            RestorePanel._execute(panel)
        validate.assert_called_once()
        panel.handle_error.assert_not_called()
        self.assertEqual((target / "a.txt").read_bytes(), b"A\nB")

    def test_direct_restore_rejects_invalid_inputs_before_source_read(self):
        for rules, error_type in (
            (None, TypeError),
            (analyze_rule_list("cache", "directory"), TypeError),
            (analyze_rule_list("<bad>", "file"), ValueError),
        ):
            with self.subTest(rules=rules):
                with (
                    patch(
                        "services.restore_service.read_text_with_fallback_encodings",
                    ) as read,
                    patch("core.atomic_file.NamedTemporaryFile") as temporary,
                ):
                    with self.assertRaises(error_type):
                        split_and_restore(
                            rules, self.root / "missing.txt", self.root / "restored",
                            "HEADER", "FOOTER", "overwrite",
                        )
                read.assert_not_called()
                temporary.assert_not_called()
                self.assertFalse((self.root / "restored").exists())

    def test_file_rule_validation_has_generic_domain_messages(self):
        for rules, error_type in (
            (None, TypeError),
            (analyze_rule_list("cache", "directory"), TypeError),
            (analyze_rule_list("<bad>", "file"), ValueError),
        ):
            with self.subTest(rules=rules):
                with self.assertRaises(error_type) as caught:
                    validate_file_rules(rules)
                self.assertNotIn("CRLF", str(caught.exception))
                self.assertNotIn("文本输出", str(caught.exception))
                self.assertIn("文件名单", str(caught.exception))

    def test_window_geometry_uses_existing_window(self):
        window = Mock()
        window.winfo_screenwidth.return_value = 1920
        window.winfo_screenheight.return_value = 1080
        center_window(window, 900, 720)
        self.assertEqual(
            [call[0] for call in window.method_calls],
            [
                "update_idletasks", "winfo_screenwidth",
                "winfo_screenheight", "geometry",
            ],
        )
        window.geometry.assert_called_once_with("900x720+510+120")

    def test_window_errors_propagate_from_each_geometry_step(self):
        for stage in (
            "update_idletasks", "winfo_screenwidth",
            "winfo_screenheight", "geometry",
        ):
            for error in (
                TclError("window operation failed"),
                RuntimeError("geometry defect"),
            ):
                with self.subTest(stage=stage, error=error):
                    window = Mock()
                    window.winfo_screenwidth.return_value = 1920
                    window.winfo_screenheight.return_value = 1080
                    getattr(window, stage).side_effect = error
                    with self.assertRaises(type(error)) as caught:
                        center_window(window, 900, 720)
                    self.assertIs(caught.exception, error)
                    getattr(window, stage).assert_called_once()
                    if stage != "geometry":
                        window.geometry.assert_not_called()


if __name__ == "__main__":
    unittest.main()