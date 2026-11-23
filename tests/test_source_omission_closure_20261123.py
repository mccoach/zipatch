# -*- coding: utf-8 -*-

"""目录未选报告、多别名完整展示及部分保留不误折叠。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.link_paths import inspect_link_path
from core.merged_format import parse_merge_blocks
from services.merge_service import merge_files
from services.source_selection import (
    unselected_merge_records, source_choice_groups,
)
from ui.source_scope import make_merge_selector


@unittest.skipUnless(os.name == "nt", "Windows正式来源边界")
class SourceOmissionClosureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.actual = self.root / "actual"
        self.actual.mkdir()
        for name in ("a.py", "b.py"):
            (self.actual / name).write_bytes(name.encode("ascii"))
        self.aliases = tuple(self.source / name for name in ("one", "two"))
        for alias in self.aliases:
            os.symlink(self.actual, alias, target_is_directory=True)
        self.output = self.root / "merged.txt"

    def tearDown(self):
        try:
            for alias in self.aliases:
                if os.path.lexists(alias):
                    os.rmdir(alias)
        finally:
            self.temporary.cleanup()

    def access(self, alias, name):
        return inspect_link_path(alias / name, follow_final_link=False)

    def test_formal_group_skip_tail_lists_directory_only(self):
        with patch("ui.source_scope.select_work_scope", return_value=("skip",)):
            result = merge_files(
                "list", str(self.source), "one/*.py", str(self.output),
                prepare_exclusions("", ""), "", "",
                CODE_HEADER_LINE, CODE_FOOTER_LINE,
                record_file_mtime=False,
                select_merge_enumeration=lambda accesses: ("authorize",),
                select_merge_scope=make_merge_selector(None, None),
            )
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
        self.assertEqual(parse_merge_blocks(text), [])
        self.assertIn(str(self.aliases[0]), text)
        self.assertNotIn(str(self.aliases[0] / "a.py"), text)
        self.assertNotIn(str(self.aliases[0] / "b.py"), text)
        self.assertEqual(result.excluded_count, 1)

    def test_partial_custom_selection_does_not_report_whole_directory_skipped(self):
        accesses = tuple(
            self.access(self.aliases[0], name) for name in ("a.py", "b.py")
        )
        prepared = tuple(
            ({"path": str(access.nominal_path), "merge_content": True}, access)
            for access in accesses
        )
        records = unselected_merge_records(prepared, accesses, (0,))
        self.assertEqual(records, ((
            str(self.aliases[0] / "b.py"), "file", "本次来源未授权",
        ),))

    def test_multi_alias_group_displays_all_directory_boundaries_and_definitions(self):
        accesses = tuple(self.access(alias, "a.py") for alias in self.aliases)
        prepared = ((
            {"path": str(accesses[0].nominal_path), "merge_content": True},
            accesses[0],
        ),)
        output = observe_file(self.output, include_digest=True)

        def choose(parent, commits, *, title, introduction, choices, details):
            self.assertEqual(len(choices), 1)
            for alias in self.aliases:
                self.assertIn(str(alias), choices[0].label)
                self.assertIn(str(alias), details)
            self.assertNotIn(str(self.aliases[0] / "a.py"), details)
            self.assertIn(str(self.actual), details)
            return ("skip",)

        with patch("ui.source_scope.select_work_scope", side_effect=choose):
            self.assertEqual(
                make_merge_selector(None, None)(prepared, accesses, output),
                ("skip",),
            )
        records = unselected_merge_records(prepared, accesses, ())
        self.assertEqual(
            {(path, kind) for path, kind, reason in records},
            {(str(alias), "directory") for alias in self.aliases},
        )

    def test_alias_bridge_projects_one_decision_to_all_related_members(self):
        one_a = self.access(self.aliases[0], "a.py")
        two_a = self.access(self.aliases[1], "a.py")
        two_b = self.access(self.aliases[1], "b.py")
        groups = source_choice_groups(((one_a, two_a), (two_b,)))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][1], (0, 1))

    def test_nested_directory_link_boundary_uses_outer_access_without_file_expansion(self):
        nested = self.actual / "nested"
        nested.mkdir()
        body = nested / "deep.py"
        body.write_bytes(b"deep")
        access = inspect_link_path(
            self.aliases[0] / "nested" / "deep.py", follow_final_link=False,
        )
        groups = source_choice_groups(((access,),))
        self.assertEqual(groups, ((self.aliases[0], (0,)),))
        records = unselected_merge_records(
            (({"path": str(access.nominal_path)}, access),), (access,), (),
        )
        self.assertEqual(records[0][:2], (str(self.aliases[0]), "directory"))


if __name__ == "__main__":
    unittest.main()