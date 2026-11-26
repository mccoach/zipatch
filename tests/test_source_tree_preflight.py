# -*- coding: utf-8 -*-

"""完整来源预扫描、一次选择及真实树形窗口的定向回归。

真实文件系统仅使用隔离临时目录；生产正文、扫描和发布不mock。
软链接权限不足明确skip，不登记为对应场景已验证。
"""

import os
import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from core.exclusion_rules import prepare_exclusions
from core.merged_format import parse_file_blocks
from core.work_selection import (
    change_hierarchical_selection, validate_hierarchical_decisions,
)
from services.demand_rules import inspect_demand_rules
from services.merge_service import merge_files
from services.scan_service import panoramic_scan
from services.source_traversal import SourceTraversal, SourceSelectionCancelled
from ui.config_editing import ConfigCommitController
from ui.source_scope import select_source_tree


class HierarchicalSelectionTests(unittest.TestCase):
    def test_parent_off_child_on_and_parent_on(self):
        parents = (None, 0, 1, None)
        states = change_hierarchical_selection(
            (True, True, True, True), parents, 0, False,
        )
        self.assertEqual(states, (False, False, False, True))
        states = change_hierarchical_selection(states, parents, 2, True)
        self.assertEqual(states, (True, True, True, True))
        states = change_hierarchical_selection(states, parents, 1, False)
        self.assertEqual(states, (True, False, False, True))
        states = change_hierarchical_selection(states, parents, 0, True)
        self.assertEqual(states, (True, True, True, True))

    def test_invalid_parent_and_inconsistent_decision_are_rejected(self):
        with self.assertRaises(ValueError):
            change_hierarchical_selection((True,), (0,), 0, False)
        with self.assertRaisesRegex(ValueError, "父链接"):
            validate_hierarchical_decisions(
                ("skip", "authorize"), (None, 0), description="test",
            )


@unittest.skipUnless(os.name == "nt", "来源链路使用本项目Windows路径观察")
class WindowsSourcePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="ZipatchSourceTree_")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.outer = self.root / "outer"
        self.inner = self.root / "inner"
        for folder in (self.source, self.outer, self.inner):
            folder.mkdir()
        self.payload = self.root / "payload.txt"
        self.payload.write_bytes(b"original\r\nbody\n")
        self.first = self.source / "route"
        self.second = self.outer / "nested"
        self.last = self.inner / "code.txt"
        self.link(self.first, self.outer, True)
        self.link(self.second, self.inner, True)
        self.link(self.last, self.payload, False)
        self.output = self.root / "merged.txt"

    def tearDown(self):
        self.temporary.cleanup()

    def link(self, entry, target, directory):
        try:
            os.symlink(str(target), entry, target_is_directory=directory)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("缺少Windows软链接权限，本场景未验证")
            raise

    def merge(self, selector, *, mode="directory", files=""):
        return merge_files(
            mode=mode, relative_base_folder=str(self.source),
            directory_list_text=str(self.source), list_text=files,
            output_file=str(self.output), exclusions=prepare_exclusions("", ""),
            preamble_text="", ending_text="",
            code_header_line="--begin--", code_footer_line="--end--",
            record_file_mtime=False, select_source_links=selector,
        )

    def test_all_layers_in_one_decision_and_no_body_before_selection(self):
        original = Path.open
        selected = False
        calls = []

        def guarded(path, *args, **kwargs):
            if Path(path) == self.payload and not selected:
                raise AssertionError("选择前不得读取普通文件正文")
            return original(path, *args, **kwargs)

        def select(nodes):
            nonlocal selected
            calls.append(nodes)
            self.assertEqual(len(nodes), 3)
            self.assertEqual(tuple(node.parent_index for node in nodes), (None, 0, 1))
            self.assertEqual(
                tuple(node.definition.entry_path for node in nodes),
                (self.first, self.second, self.last),
            )
            selected = True
            return ("authorize",) * len(nodes)

        with patch.object(Path, "open", guarded):
            result = self.merge(select)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.total_files, 1)
        blocks = parse_file_blocks(self.output.read_bytes().decode("utf-8"))
        self.assertEqual(blocks, [
            (str(self.first / "nested" / "code.txt"), "original\r\nbody\n"),
        ])
        self.assertEqual(self.payload.read_bytes(), b"original\r\nbody\n")

    def test_parent_denial_reads_no_body_and_reports_only_outer_cancel(self):
        original = Path.open
        seen = []

        def guarded(path, *args, **kwargs):
            if Path(path) == self.payload:
                raise AssertionError("未选路线不得读取正文")
            return original(path, *args, **kwargs)

        def select(nodes):
            seen.extend(nodes)
            return ("skip",) * len(nodes)

        with patch.object(Path, "open", guarded):
            result = self.merge(select)
        self.assertEqual(len(seen), 3)
        self.assertEqual(result.total_files, 0)
        text = self.output.read_bytes().decode("utf-8")
        self.assertEqual(parse_file_blocks(text), [])
        self.assertIn(str(self.first), text)
        self.assertNotIn(str(self.first / "nested"), text)

    def test_cancel_stops_after_complete_scan_without_publication(self):
        calls = []

        def select(nodes):
            calls.append(nodes)
            return None

        with self.assertRaises(SourceSelectionCancelled):
            self.merge(select)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 3)
        self.assertFalse(self.output.exists())

    def test_alias_routes_independent_and_actual_names_enumerated_once(self):
        alias = self.source / "alternate"
        self.link(alias, self.outer, True)
        traversal = SourceTraversal(selector=lambda nodes: tuple(
            "skip" if str(node.nominal_path).startswith(str(self.first))
            else "authorize"
            for node in nodes
        ))
        root = traversal.resolve(self.source)
        files = [access for access, _ in traversal.walk(root) if access.kind == "file"]
        # source、outer、inner各枚举一次，两个名义入口共用实际名称。
        self.assertEqual(traversal.enumeration.directory_reads, 3)
        retained = traversal.finalize()
        from core.recovery_paths import identity
        selected = [
            access for access in files if identity(access.nominal_path) in retained
        ]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].nominal_path, alias / "nested" / "code.txt")
        self.assertEqual(traversal.enumeration.directory_reads, 3)
        with self.assertRaises(ValueError):
            traversal.finalize()
        with self.assertRaises(ValueError):
            traversal.resolve(self.source)

    def test_new_member_during_window_rejects_without_body_or_output(self):
        def select(nodes):
            (self.inner / "external.txt").write_bytes(b"external")
            return ("authorize",) * len(nodes)

        with self.assertRaisesRegex(ValueError, "结构变化"):
            self.merge(select)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.inner / "external.txt").read_bytes(), b"external")

    def test_background_still_stops_before_deeper_definition(self):
        original = os.readlink

        def guarded(path, *args, **kwargs):
            if Path(path) in (self.second, self.last):
                raise AssertionError("后台不得探查深层链接")
            return original(path, *args, **kwargs)

        with patch("core.link_paths.os.readlink", side_effect=guarded):
            matches = inspect_demand_rules(
                ((1, "route\\**\\*.txt"),), str(self.source), Event(),
            )
        self.assertTrue(matches[1].requires_authorization)

    def test_excluded_directory_never_reads_its_link_definitions(self):
        original = os.readlink

        def guarded(path, *args, **kwargs):
            if Path(path) in (self.first, self.second, self.last):
                raise AssertionError("排除文件夹不得读取链接定义")
            return original(path, *args, **kwargs)

        with patch("core.link_paths.os.readlink", side_effect=guarded):
            result = merge_files(
                mode="directory", relative_base_folder="",
                directory_list_text=str(self.source), list_text="",
                output_file=str(self.output),
                exclusions=prepare_exclusions("route", ""),
                preamble_text="", ending_text="",
                code_header_line="--begin--", code_footer_line="--end--",
                record_file_mtime=False,
                select_source_links=lambda nodes: self.fail("无链接不得弹窗"),
            )
        self.assertEqual(result.total_files, 0)

    def test_scan_uses_same_single_selection_and_never_reads_payload(self):
        original = Path.open
        calls = []

        def guarded(path, *args, **kwargs):
            if Path(path) == self.payload:
                raise AssertionError("扫描不得读取正文")
            return original(path, *args, **kwargs)

        def select(nodes):
            calls.append(nodes)
            return ("authorize",) * len(nodes)

        with patch.object(Path, "open", guarded):
            result = panoramic_scan(
                str(self.source), str(self.output), prepare_exclusions("", ""),
                "", "", include_size=True, include_date=True,
                select_source_links=select,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 3)
        self.assertEqual(result.file_count, 1)
        self.assertEqual(result.link_count, 3)

    def test_list_and_combined_modes_select_once(self):
        for mode in ("list", "combined"):
            with self.subTest(mode=mode):
                calls = []

                def select(nodes):
                    calls.append(nodes)
                    return ("authorize",) * len(nodes)

                result = self.merge(
                    select, mode=mode,
                    files="route\\**\\*.txt\nroute\\nested\\code.txt",
                )
                self.assertEqual(len(calls), 1)
                self.assertEqual(result.total_files, 1)

    def test_real_tree_window_parent_child_and_cancel(self):
        traversal = SourceTraversal(selector=lambda nodes: ("authorize",) * len(nodes))
        root_access = traversal.resolve(self.source)
        list(traversal.walk(root_access))
        root = tk.Tk()
        root.withdraw()
        manager = ConfigSaveManager(
            deepcopy(DEFAULT_CONFIG),
            path=self.root / "ui_config.json", needs_write=True,
        )
        commits = ConfigCommitController(root, manager)
        observed = []

        def act():
            dialog = next(
                child for child in root.winfo_children()
                if isinstance(child, tk.Toplevel)
                and hasattr(child, "_source_tree")
            )
            dialog._source_tree_toggle(0)
            observed.append(dialog._source_tree_states)
            dialog._source_tree_toggle(2)
            observed.append(dialog._source_tree_states)
            self.assertEqual(dialog._source_tree.parent("1"), "0")
            self.assertEqual(dialog._source_tree.parent("2"), "1")
            from ui.window_manager import close_registered_popup
            close_registered_popup(dialog)

        root.after(30, act)
        try:
            decision = select_source_tree(root, commits, traversal.nodes)
        finally:
            root.destroy()
        self.assertIsNone(decision)
        self.assertEqual(observed, [
            (False, False, False), (True, True, True),
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)