# -*- coding: utf-8 -*-

"""列表固定前缀、第一次枚举授权、别名正文去重及真实Tk页面回归。"""

import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import patch

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG, CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.filesystem_changes import execute_filesystem_change, prepare_link_creation
from core.link_paths import LinkSpecification, inspect_link_path
from core.merged_format import parse_merge_blocks
from services.demand_rules import inspect_demand_rules, build_demand_selection
from services.merge_service import merge_files
from services.restore_service import RestoreTask, prepare_restore


@unittest.skipUnless(os.name == "nt", "主体B正式业务使用Windows")
class DemandLinkSubjectBTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zipatch_subject_b_")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.body = self.outside / "body.txt"
        self.body.write_bytes(b"body")
        self.output = self.root / "merged.txt"
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, kind="directory_symlink"):
        if kind == "junction":
            receipt = execute_filesystem_change(prepare_link_creation(
                observe_file(entry),
                LinkSpecification("junction", "\\\\?\\" + str(target), False),
            ))
            self.assertTrue(receipt.succeeded)
        else:
            os.symlink(target, entry, target_is_directory=kind != "file_symlink")
        self.links.append((entry, kind != "file_symlink"))
        return inspect_link_path(entry, follow_final_link=False)

    def merge(self, text, authorize=None, *, mode="list", exclusions=None):
        return merge_files(
            mode, str(self.source), text, str(self.output),
            exclusions or prepare_exclusions("", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False, authorize_merge_scope=authorize,
        )

    def blocks(self):
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
        return text, parse_merge_blocks(text)

    def test_background_reports_pending_without_target_enumeration_or_body(self):
        self.link(self.source / "linked", self.outside)
        original_scandir = os.scandir
        original_open = Path.open

        def scandir(path):
            if Path(path) == self.outside:
                raise AssertionError("后台未授权不得枚举链接实际目录")
            return original_scandir(path)

        def opened(path, *args, **keywords):
            if path == self.body:
                raise AssertionError("后台检查不得读取来源正文")
            return original_open(path, *args, **keywords)

        with patch("os.scandir", new=scandir), patch("pathlib.Path.open", new=opened):
            matches = inspect_demand_rules(
                ((1, "linked/*.txt"),), str(self.source), Event(),
            )
        match = matches[1]
        self.assertTrue(match.requires_authorization)
        self.assertEqual(match.issue, "")
        self.assertEqual(match.files, ())
        selection = build_demand_selection((match,))
        self.assertTrue(selection.complete)
        self.assertFalse(selection.has_errors)
        self.assertTrue(selection.requires_authorization)
        self.assertEqual(selection.files, ())
        self.assertEqual(match.enumeration_accesses[0].actual_path, self.outside)

    def test_symlink_and_junction_prefix_authorize_before_first_actual_enumeration(self):
        for kind in ("directory_symlink", "junction"):
            with self.subTest(kind=kind):
                alias = self.source / kind
                self.link(alias, self.outside, kind)
                authorized = False
                entered = []
                original_scandir = os.scandir

                def scandir(path):
                    if Path(path) == self.outside:
                        self.assertTrue(authorized)
                        entered.append(Path(path))
                    return original_scandir(path)

                def authorize(title, description):
                    nonlocal authorized
                    if title == "合并列表实际目录枚举授权":
                        self.assertEqual(entered, [])
                        self.assertIn(str(alias), description)
                        self.assertIn(str(self.outside), description)
                        authorized = True
                    return True

                with patch("os.scandir", new=scandir):
                    result = self.merge(kind + "/*.txt", authorize)
                self.assertEqual(entered, [self.outside])
                self.assertEqual(result.total_files, 1)
                _, blocks = self.blocks()
                self.assertEqual(blocks[0].path, str(alias / "body.txt"))
                self.assertEqual(blocks[0].body, "body")
                self.assertEqual(self.body.read_bytes(), b"body")

    def test_cancelled_prefix_authorization_publishes_nothing(self):
        self.link(self.source / "linked", self.outside)
        original_scandir = os.scandir

        def scandir(path):
            if Path(path) == self.outside:
                raise AssertionError("取消授权不得开始实际枚举")
            return original_scandir(path)

        with patch("os.scandir", new=scandir):
            with self.assertRaises(ValueError):
                self.merge("linked/*.txt", lambda title, description: False)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.body.read_bytes(), b"body")

    def test_recursive_rule_never_enters_other_directory_link_targets(self):
        nested = self.outside / "nested"
        nested.mkdir()
        (nested / "deep.txt").write_bytes(b"deep")
        private = self.root / "private"
        private.mkdir()
        (private / "secret.txt").write_bytes(b"secret")
        self.link(self.source / "linked", self.outside)
        self.link(self.outside / "other", private)
        original_scandir = os.scandir

        def scandir(path):
            if Path(path) == private:
                raise AssertionError("递归规则不得进入途中其他链接目标")
            return original_scandir(path)

        with patch("os.scandir", new=scandir):
            self.merge("linked/**/*.txt", lambda title, description: True)
        _, blocks = self.blocks()
        self.assertEqual(tuple(block.body for block in blocks), ("body", "deep"))
        self.assertEqual((private / "secret.txt").read_bytes(), b"secret")

    def test_linked_source_base_keeps_nominal_exact_and_wildcard_paths(self):
        alias = self.root / "source-alias"
        self.link(alias, self.outside)
        matches = inspect_demand_rules(
            ((1, "body.txt"), (2, "*.txt")), str(alias), Event(),
        )
        self.assertTrue(matches[1].completed)
        self.assertEqual(matches[1].files, (str(alias / "body.txt"),))
        self.assertTrue(matches[2].requires_authorization)
        approved = inspect_demand_rules(
            ((1, "*.txt"),), str(alias), Event(),
            request_enumeration=lambda access, title, description: True,
        )
        self.assertEqual(approved[1].files, (str(alias / "body.txt"),))
        self.assertEqual(approved[1].accesses[0].actual_path, self.body)

    def test_actual_aliases_and_hardlink_names_decode_body_once(self):
        self.link(self.source / "one", self.outside)
        self.link(self.source / "two", self.outside)
        peer = self.root / "body-peer.txt"
        os.link(self.body, peer)
        from services import merge_service

        original_read = merge_service.read_text_content_for_merge
        reads = []

        def read(path, **keywords):
            reads.append(Path(path))
            return original_read(path, **keywords)

        with patch("services.merge_service.read_text_content_for_merge", side_effect=read):
            result = self.merge(
                "one/*.txt\ntwo/*.txt\n" + str(peer),
                lambda title, description: True,
            )
        self.assertEqual(reads, [self.body])
        self.assertEqual(result.total_files, 1)
        _, blocks = self.blocks()
        self.assertEqual(blocks[0].path, str(self.source / "one" / "body.txt"))
        self.assertEqual(blocks[0].body, "body")

    def test_combined_actual_alias_explicit_list_overrides_exclusion(self):
        ordinary = self.source / "folder"
        ordinary.mkdir()
        body = ordinary / "body.txt"
        body.write_bytes(b"included")
        self.link(self.source / "route", ordinary)
        # 排除链接目录入口结构，列表仍可以明确授权其实际文件范围。
        from services import merge_service
        original_read = merge_service.read_text_content_for_merge
        reads = []

        def read(path, **keywords):
            reads.append(Path(path))
            return original_read(path, **keywords)

        with patch("services.merge_service.read_text_content_for_merge", side_effect=read):
            self.merge(
                "route/*.txt", lambda title, description: True,
                mode="combined", exclusions=prepare_exclusions("route", "*.txt"),
            )
        _, blocks = self.blocks()
        files = tuple(block for block in blocks if block.kind == "file")
        self.assertEqual(len(files), 1)
        # 文件排除现在彻底移出目录来源，不再用被排除名称占据首次位置。
        # 唯一保留来源是显式列表经route选中的名义入口。
        self.assertEqual(files[0].path, str(self.source / "route" / "body.txt"))
        self.assertEqual(files[0].body, "included")
        self.assertEqual(reads, [body])

    def test_retarget_during_enumeration_authorization_stops_before_scandir(self):
        alias = self.source / "linked"
        self.link(alias, self.outside)
        other = self.root / "other"
        other.mkdir()
        (other / "body.txt").write_bytes(b"external")
        original_scandir = os.scandir

        def authorize(title, description):
            os.rmdir(alias)
            os.symlink(other, alias, target_is_directory=True)
            return True

        def scandir(path):
            if Path(path) in (self.outside, other):
                raise AssertionError("改指向后不得进入原范围或新范围")
            return original_scandir(path)

        with patch("os.scandir", new=scandir):
            with self.assertRaises(ValueError):
                self.merge("linked/*.txt", authorize)
        self.assertFalse(self.output.exists())
        self.assertEqual((other / "body.txt").read_bytes(), b"external")

    def test_deduplicated_alias_dependency_is_still_checked_before_publication(self):
        first = self.source / "one"
        second = self.source / "two"
        self.link(first, self.outside)
        self.link(second, self.outside)
        other = self.root / "other"
        other.mkdir()
        external = other / "body.txt"
        external.write_bytes(b"external")
        from services import merge_service
        original_read = merge_service.read_text_content_for_merge

        def read(path, **keywords):
            result = original_read(path, **keywords)
            os.rmdir(second)
            os.symlink(other, second, target_is_directory=True)
            return result

        with patch("services.merge_service.read_text_content_for_merge", side_effect=read):
            with self.assertRaises(ValueError):
                self.merge(
                    "one/*.txt\ntwo/*.txt",
                    lambda title, description: True,
                )
        self.assertFalse(self.output.exists())
        self.assertEqual(self.body.read_bytes(), b"body")
        self.assertEqual(external.read_bytes(), b"external")

    def test_selected_output_actual_alias_is_rejected_before_body_acquisition(self):
        self.link(self.source / "linked", self.outside)
        self.output = self.body
        with self.assertRaises(ValueError):
            self.merge("linked/*.txt", lambda title, description: True)
        self.assertEqual(self.body.read_bytes(), b"body")

    def test_authorized_list_container_splits_through_formal_chain(self):
        self.link(self.source / "linked", self.outside)
        self.merge("linked/*.txt", lambda title, description: True)
        text, blocks = self.blocks()
        destination = self.root / "split"
        destination.mkdir()
        backups = self.root / "backups"
        backups.mkdir()
        plan = prepare_restore(
            None, str(destination),
            "overwrite", source_text=text, backup_dir=str(backups),
        )
        result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        mapped = destination / Path(blocks[0].path).drive[0].upper() / Path(
            *Path(blocks[0].path).parts[1:]
        )
        self.assertEqual(mapped.read_bytes(), b"body")
        self.assertTrue(result.backup_root)
        self.assertEqual(self.body.read_bytes(), b"body")


@unittest.skipUnless(os.name == "nt", "主体B真实Tk页面使用Windows")
class DemandLinkSubjectBPageTests(unittest.TestCase):
    def test_background_save_pending_and_main_page_authorization_are_separate(self):
        import tkinter as tk
        from types import SimpleNamespace
        from ui.config_editing import ConfigCommitController
        from panels.merge_panel import MergePanel

        root = tk.Tk()
        root.withdraw()
        with tempfile.TemporaryDirectory(prefix="zipatch_subject_b_page_") as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            actual = base / "actual"
            actual.mkdir()
            body = actual / "body.txt"
            body.write_bytes(b"body")
            alias = source / "linked"
            os.symlink(actual, alias, target_is_directory=True)
            config = deepcopy(DEFAULT_CONFIG)
            config["merge"].update({
                "source_folder": str(source),
                "output_folder": str(base),
                "output_filename": "merged.txt",
                "demand_file_list_text": "linked/*.txt",
                "open_after_done": False,
                "record_file_mtime": False,
                "preamble_text": "",
                "ending_text": "",
            })
            manager = ConfigSaveManager(
                config, path=base / "config.json", needs_write=True,
            )
            commits = ConfigCommitController(root, manager)
            app = SimpleNamespace(
                root=root, config_data=config, commits=commits,
                log=lambda message: None,
                status_var=tk.StringVar(master=root),
            )
            session = None
            errors = []
            root.report_callback_exception = (
                lambda error_type, error, traceback: errors.append(error)
            )
            original_scandir = os.scandir
            authorized = False
            entered = []
            main_thread = threading.get_ident()
            authorization_threads = []

            def scandir(path):
                if Path(path) == actual:
                    self.assertTrue(authorized, "后台保存不得枚举实际链接目录")
                    entered.append(Path(path))
                return original_scandir(path)

            def authorize(title, description, parent=None, danger=False):
                authorization_threads.append(threading.get_ident())
                if title == "合并列表实际目录枚举授权":
                    self.assertEqual(entered, [])
                    self.assertIn(str(actual), description)
                    nonlocal authorized
                    authorized = True
                return True

            def wait_until(condition):
                deadline = time.monotonic() + 15
                while not condition():
                    if time.monotonic() >= deadline:
                        self.fail("后台名单检查未在限定时间内结束")
                    root.update()
                    time.sleep(0.01)
                root.update()

            try:
                panel = MergePanel(root, app)
                panel.pack()
                with patch("os.scandir", new=scandir):
                    session = panel.open_merge_list_dialog()
                    wait_until(lambda: all(rule.match is not None for rule in session.rules))
                    self.assertTrue(session.rules[0].match.requires_authorization)
                    self.assertEqual(entered, [])
                    self.assertIn("待本次合并", session.result_text.get("1.0", "end-1c"))
                    self.assertTrue(session.apply())
                    wait_until(lambda: not session.busy)
                    self.assertEqual(config["merge"]["demand_file_list_text"], "linked/*.txt")
                    self.assertEqual(entered, [])
                    self.assertIn("未保存业务授权", session.status.get())
                    self.assertTrue(session.cancel())
                    session = None
                    def choose(parent, commits, *, title, introduction, choices, details):
                        nonlocal authorized
                        authorization_threads.append(threading.get_ident())
                        if title == "选择本次实际目录枚举范围":
                            self.assertEqual(entered, [])
                            self.assertIn(str(actual), details)
                            authorized = True
                        return tuple("authorize" for _ in choices)

                    with patch(
                        "ui.message_dialogs.safe_ask_risk_confirm", side_effect=authorize,
                    ), patch("panels.merge_panel.safe_show_info"), patch(
                        "ui.source_scope.select_work_scope", side_effect=choose,
                    ):
                        panel.execute_merge("list")
                self.assertEqual(errors, [])
                self.assertEqual(entered, [actual])
                self.assertTrue(authorization_threads)
                self.assertTrue(all(
                    identifier == main_thread for identifier in authorization_threads
                ))
                with (base / "merged.txt").open(
                    "r", encoding="utf-8", newline="",
                ) as stream:
                    blocks = parse_merge_blocks(stream.read())
                self.assertEqual(blocks[0].body, "body")
                self.assertEqual(body.read_bytes(), b"body")
                self.assertEqual(commits.operation_depth, 0)
            finally:
                if session is not None:
                    session.cancel()
                if os.path.lexists(alias):
                    os.rmdir(alias)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
