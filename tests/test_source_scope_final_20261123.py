# -*- coding: utf-8 -*-

"""目录授权归并、决定投影、遗漏例外及同边界复核性能对照。"""

import os
import tempfile
import unittest
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.link_paths import inspect_link_path, recheck_link_path
from core.merged_format import parse_merge_blocks
from services.merge_service import merge_files, format_merge_omissions
from services.source_recheck import recheck_source_paths
from ui.source_scope import (
    source_choice_groups, project_source_choices, make_merge_selector,
)


@unittest.skipUnless(os.name == "nt", "Windows真实目录授权与复核")
class SourceScopeFinalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.actual = self.root / "actual"
        self.actual.mkdir()
        self.first = self.actual / "a.py"
        self.second = self.actual / "b.py"
        self.first.write_bytes(b"first")
        self.second.write_bytes(b"second")
        self.alias = self.source / "route"
        os.symlink(self.actual, self.alias, target_is_directory=True)
        self.output = self.root / "merged.txt"

    def tearDown(self):
        try:
            if os.path.lexists(self.alias):
                os.rmdir(self.alias)
        finally:
            self.temporary.cleanup()

    def accesses(self):
        return tuple(
            inspect_link_path(self.alias / name, follow_final_link=False)
            for name in ("a.py", "b.py")
        )

    def test_directory_routes_are_one_choice_and_project_to_all_members(self):
        accesses = self.accesses()
        groups = source_choice_groups(tuple((access,) for access in accesses))
        self.assertEqual(groups, ((self.alias, (0, 1)),))
        self.assertEqual(
            project_source_choices(groups, ("skip",), 3),
            ("skip", "skip", "authorize"),
        )
        self.assertEqual(
            project_source_choices(groups, ("authorize",), 3),
            ("authorize", "authorize", "authorize"),
        )
        with self.assertRaisesRegex(ValueError, "已取消"):
            project_source_choices(groups, None, 3)

    def test_real_merge_selector_presents_directory_not_descendant_file_list(self):
        accesses = self.accesses()
        prepared = tuple(
            ({"path": str(access.nominal_path), "merge_content": True}, access)
            for access in accesses
        )

        def choose(parent, commits, *, title, introduction, choices, details):
            self.assertEqual(len(choices), 1)
            self.assertIn(str(self.alias), choices[0].label)
            self.assertNotIn("a.py", choices[0].label)
            self.assertNotIn("b.py", choices[0].label)
            self.assertNotIn(str(self.alias / "a.py"), details)
            self.assertIn(str(self.actual), details)
            return ("skip",)

        from core.file_observation import observe_file

        output = observe_file(self.output, include_digest=True)
        with patch("ui.source_scope.select_work_scope", side_effect=choose):
            selected = make_merge_selector(None, None)(
                prepared, accesses, output,
            )
        self.assertEqual(selected, ("skip", "skip"))

    def test_formal_merge_group_skip_reads_no_body_and_reports_directory(self):
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path in (self.first, self.second):
                raise AssertionError("共同未授权目录不得读取正文")
            return original_open(path, *arguments, **keywords)

        with patch("pathlib.Path.open", new=opened), patch(
            "ui.source_scope.select_work_scope", return_value=("skip",),
        ):
            result = merge_files(
                "list", str(self.source), "route/*.py", str(self.output),
                prepare_exclusions("", ""), "", "",
                CODE_HEADER_LINE, CODE_FOOTER_LINE,
                record_file_mtime=False,
                select_merge_enumeration=lambda accesses: ("authorize",),
                select_merge_scope=make_merge_selector(None, None),
            )
        self.assertEqual(result.total_files, 0)
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            self.assertEqual(parse_merge_blocks(stream.read()), [])

    def test_ignored_directory_reports_successful_supplement_without_expanding(self):
        blocked = self.source / "blocked"
        successful = blocked / "a.py"
        text = format_merge_omissions(
            ((str(blocked), "directory", "排除目录名单"),), (),
            successful_paths=(str(successful),),
        )
        self.assertIn("另有 1 项", text)
        self.assertIn("并非整目录全部未输出", text)
        self.assertNotIn(str(successful), text)

    def test_formal_excluded_directory_supplement_has_body_and_exception_notice(self):
        blocked = self.source / "blocked"
        blocked.mkdir()
        body = blocked / "included.py"
        body.write_bytes(b"included")
        merge_files(
            "combined", str(self.source), "blocked/included.py",
            str(self.output), prepare_exclusions("blocked\nroute", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
        )
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
        blocks = parse_merge_blocks(text)
        self.assertEqual(tuple(block.body for block in blocks), ("included",))
        self.assertIn("另有 1 项", text)
        self.assertEqual(text.count(str(body)), 1)

    def test_batch_recheck_refuses_retarget_without_opening_or_enumerating(self):
        accesses = self.accesses()
        other = self.root / "other"
        other.mkdir()
        (other / "a.py").write_bytes(b"external")
        os.rmdir(self.alias)
        os.symlink(other, self.alias, target_is_directory=True)
        with patch(
            "pathlib.Path.open", side_effect=AssertionError("不读正文"),
        ), patch(
            "os.scandir", side_effect=AssertionError("不枚举"),
        ), patch(
            "os.readlink", side_effect=AssertionError("不追踪新定义"),
        ):
            with self.assertRaises(ValueError):
                recheck_source_paths(accesses)

    def test_batch_recheck_checks_shared_directory_once_and_prints_local_comparison(self):
        accesses = self.accesses()
        original_lstat = Path.lstat
        counts = {"reference": 0, "batch": 0}
        stage = "reference"

        def lstat(path, *arguments, **keywords):
            if path == self.actual:
                counts[stage] += 1
            return original_lstat(path, *arguments, **keywords)

        with patch("pathlib.Path.lstat", new=lstat):
            started = perf_counter()
            for access in accesses:
                recheck_link_path(access)
            reference_seconds = perf_counter() - started
            stage = "batch"
            started = perf_counter()
            recheck_source_paths(accesses)
            batch_seconds = perf_counter() - started
        self.assertEqual(counts["batch"], 1)
        self.assertGreater(counts["reference"], counts["batch"])
        print(
            "\nLOCAL_SOURCE_RECHECK "
            f"members={len(accesses)} "
            f"reference_shared_directory_queries={counts['reference']} "
            f"batch_shared_directory_queries={counts['batch']} "
            f"reference_seconds={reference_seconds:.6f} "
            f"batch_seconds={batch_seconds:.6f}",
            flush=True,
        )


if __name__ == "__main__":
    unittest.main()