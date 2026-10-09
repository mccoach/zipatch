# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.constants import MESSAGE_FILE_EXCLUDED
from core.exclusion_rules import prepare_exclusions
from core.file_walk import iter_project_entries
from panels.merge_panel import MergePanel
from panels.scan_panel import ScanPanel
from services.merge_service import merge_project_files
from services.scan_service import panoramic_scan


class ExclusionBusinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "project"
        self.root.mkdir()
        for relative in (
            "README",
            "main.py",
            "report.log",
            "src/cache/private.txt",
            "src/cache/sub/deep.txt",
            "a/src/cache/private.txt",
            "src/other/cache/kept.txt",
            "cache_old/kept.txt",
            "tools/sub/file.txt",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("SOURCE:" + relative, encoding="utf-8")

    def test_actual_directory_pruning_and_file_flags(self):
        import core.file_walk as module

        output = self.root / "output.txt"
        output.write_text("OLD_OUTPUT", encoding="utf-8")
        exclusions = prepare_exclusions("src\\cache", "*.log\n:")
        visited = []
        original_walk = module.os.walk

        def observed_walk(*args, **kwargs):
            for folder, directories, files in original_walk(*args, **kwargs):
                visited.append(Path(folder).relative_to(self.root).as_posix())
                yield folder, directories, files

        with patch("core.file_walk.os.walk", side_effect=observed_walk):
            entries = list(iter_project_entries(self.root, exclusions, output))

        relative = {
            Path(path).relative_to(self.root).as_posix(): (is_directory, excluded)
            for path, is_directory, excluded in entries
        }
        self.assertNotIn("src/cache", visited)
        self.assertNotIn("a/src/cache", visited)
        self.assertNotIn("src/cache/sub/deep.txt", relative)
        self.assertNotIn("output.txt", relative)
        self.assertEqual(relative["report.log"], (False, True))
        self.assertEqual(relative["README"], (False, True))
        self.assertEqual(relative["main.py"], (False, False))
        self.assertIn("src/other/cache/kept.txt", relative)
        self.assertIn("cache_old/kept.txt", relative)
        self.assertNotIn(".", relative)

    def test_rooted_directory_rule_keeps_nested_counterpart(self):
        entries = list(
            iter_project_entries(
                self.root, prepare_exclusions("\\src\\cache", ""),
            )
        )
        relative = {
            Path(path).relative_to(self.root).as_posix()
            for path, _, _ in entries
        }
        self.assertNotIn("src/cache/private.txt", relative)
        self.assertIn("a/src/cache/private.txt", relative)

    def test_scan_excluded_files_are_not_output_or_counted(self):
        output = Path(self.directory.name) / "scan.txt"
        exclusions = prepare_exclusions("src\\cache", "*.log\n:")
        expected = list(iter_project_entries(self.root, exclusions, output))
        result = panoramic_scan(
            source_folder=self.root,
            output_file=output,
            exclusions=exclusions,
            preamble_text="",
            ending_text="",
        )
        self.assertEqual(
            result.file_count,
            sum(not is_directory and not excluded for _, is_directory, excluded in expected),
        )
        self.assertEqual(
            result.dir_count,
            sum(is_directory for _, is_directory, _ in expected),
        )
        text = output.read_text(encoding="utf-8")
        self.assertNotIn(str(self.root / "report.log"), text)
        self.assertNotIn(str(self.root / "README"), text)
        self.assertNotIn(str(self.root / "src/cache/private.txt"), text)
        self.assertIn(str(self.root / "main.py"), text)

    def test_merge_preserves_excluded_sections_without_reading_content(self):
        import services.merge_service as module

        output = Path(self.directory.name) / "merge.txt"
        original_reader = module.read_text_content_for_merge
        read_paths = []

        def observed_reader(path, **kwargs):
            read_paths.append(Path(path).relative_to(self.root).as_posix())
            return original_reader(path, **kwargs)

        with patch(
            "services.merge_service.read_text_content_for_merge",
            side_effect=observed_reader,
        ):
            result = merge_project_files(
                source_folder=self.root,
                output_file=output,
                exclusions=prepare_exclusions("src\\cache", "*.log\n:"),
                preamble_text="",
                ending_text="",
                code_header_line="---以下是源代码---",
                code_footer_line="---源代码结束---",
            )
        text = output.read_text(encoding="utf-8")
        self.assertIn(str(self.root / "report.log"), text)
        self.assertIn(str(self.root / "README"), text)
        self.assertIn(MESSAGE_FILE_EXCLUDED, text)
        self.assertNotIn("SOURCE:report.log", text)
        self.assertNotIn("SOURCE:README", text)
        self.assertNotIn("report.log", read_paths)
        self.assertNotIn("README", read_paths)
        self.assertNotIn("src/cache/private.txt", read_paths)
        self.assertNotIn(str(self.root / "src/cache/private.txt"), text)
        self.assertEqual(text.count("---以下是源代码---"), result.total_files)
        self.assertEqual(text.count("---源代码结束---"), result.total_files)

    def test_invalid_rules_block_panels_before_output_conflict_or_service(self):
        cfg = {
            "source_folder": str(self.root),
            "output_folder": self.directory.name,
            "output_filename": "existing.txt",
            "exclude_folders": "",
            "exclude_files": "\n\nbad|file",
        }
        output = Path(self.directory.name) / "existing.txt"
        output.write_text("KEEP_ORIGINAL", encoding="utf-8")
        for panel_class, entry_name, module_name, service_name in (
            (ScanPanel, "_execute", "panels.scan_panel", "panoramic_scan"),
            (
                MergePanel, "_execute_regular_merge",
                "panels.merge_panel", "merge_project_files",
            ),
        ):
            with self.subTest(panel=panel_class.__name__):
                panel = SimpleNamespace(
                    prepare=Mock(return_value=cfg),
                    handle_error=Mock(),
                    root=None,
                    resolve_common_output_and_markers=Mock(),
                )
                with (
                    patch(module_name + ".resolve_output_file_conflict") as conflict,
                    patch(module_name + "." + service_name) as service,
                ):
                    getattr(panel_class, entry_name)(panel)
                conflict.assert_not_called()
                service.assert_not_called()
                panel.resolve_common_output_and_markers.assert_not_called()
                panel.handle_error.assert_called_once()
                self.assertIn(
                    "文件名单，第 3 行",
                    str(panel.handle_error.call_args.args[1]),
                )
                self.assertEqual(output.read_text(encoding="utf-8"), "KEEP_ORIGINAL")

    def test_output_itself_is_skipped_for_scan_and_merge(self):
        scan_output = self.root / "scan-output.txt"
        scan_output.write_text("OLD_SCAN", encoding="utf-8")
        scanned = panoramic_scan(
            self.root, scan_output, prepare_exclusions("", ""), "", "",
        )
        self.assertNotIn(
            str(scan_output), scan_output.read_text(encoding="utf-8"),
        )
        merge_output = self.root / "merge-output.txt"
        merge_output.write_text("OLD_MERGE", encoding="utf-8")
        merged = merge_project_files(
            self.root, merge_output, prepare_exclusions("", ""),
            "", "", "---以下是源代码---", "---源代码结束---",
        )
        self.assertNotIn(
            str(merge_output), merge_output.read_text(encoding="utf-8"),
        )
        self.assertGreater(scanned.file_count, 0)
        self.assertGreater(merged.total_files, 0)


if __name__ == "__main__":
    unittest.main()