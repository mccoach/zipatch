# -*- coding: utf-8 -*-

"""主体A正式服务、授权顺序、强制备份、停止政策及页面接入回归。"""

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import patch

from core.config import ConfigSaveManager
from core.constants import (
    CODE_HEADER_LINE, CODE_FOOTER_LINE, DEFAULT_CONFIG,
)
from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.filesystem_changes import execute_filesystem_change, prepare_link_creation
from core.link_paths import (
    LinkSpecification, inspect_link_path, specification_from_link,
)
from core.merged_format import (
    CONTAINER_PREFIX, OutputSettingsError, encode_container_declaration,
    encode_structure_line, parse_merge_blocks, validate_markers,
)
from core.recovery_protection import RecoveryProtectionSession, read_recovery_evidence
from panels.base_panel import publication_failure_title
from panels.restore_panel import RestorePanel, format_split_preview
from services.merge_service import merge_files
from services.patch_scope import inspect_scope_tree, materialize_scope_observation
from services.patch_service import preview_patch
from services.restore_service import (
    RestoreExecutionError, RestoreTask, prepare_restore,
)


@unittest.skipUnless(os.name == "nt", "主体A正式业务使用Windows")
class StructureSplitSubjectATests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zipatch_subject_a_")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "destination"
        self.destination.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.private = self.outside / "private"
        self.private.write_bytes(b"private")
        self.output = self.root / "merged.txt"
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, raw, kind):
        if kind == "junction":
            result = execute_filesystem_change(prepare_link_creation(
                observe_file(entry),
                LinkSpecification(kind, "\\\\?\\" + str(raw), False),
            ))
            self.assertTrue(result.succeeded)
        else:
            os.symlink(raw, entry, target_is_directory=kind != "file_symlink")
        self.links.append((entry, kind != "file_symlink"))
        return inspect_link_path(entry, follow_final_link=False)

    def block(self, path, body="", kind="file", specification=None, index=1):
        token = "4a9e268a78574ffcb19ed7b2c23d0568"
        lines = [f"---第{index}个文件--- [{token}]", str(path)]
        if kind != "file":
            lines.append(encode_structure_line(
                kind, specification, entry_path=path,
            ))
        lines.append(CODE_HEADER_LINE + " [" + token + "]")
        return (
            "\r\n".join(lines) + "\r\n" + body + "\r\n"
            + CODE_FOOTER_LINE + " [" + token + "]\r\n"
        )

    def plan(self, text, *, mode="directory", base=None, authorize=None, policy="overwrite"):
        if not text.startswith(CONTAINER_PREFIX):
            token = "4a9e268a78574ffcb19ed7b2c23d0568"
            text = (
                encode_container_declaration(
                    token, CODE_HEADER_LINE, CODE_FOOTER_LINE,
                ) + "\r\n" + text
                + "ZIPATCH_MERGED_END:" + token + "\r\n"
            )
        return prepare_restore(
            None, str(base or self.destination),
            policy,
            source_text=text, target_mode=mode,
            backup_dir=str(self.backups),
            authorize_restore_scope=authorize,
        )

    def merged(self):
        merge_files(
            "directory", str(self.source), "", str(self.output),
            prepare_exclusions("", ""), "", "",
            CODE_HEADER_LINE, CODE_FOOTER_LINE,
            record_file_mtime=False,
            authorize_merge_scope=lambda title, description: True,
        )
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

    def mapped(self, path):
        return self.destination / path.drive[0].upper() / Path(*path.parts[1:])

    def test_plain_directory_empty_directory_and_exact_body_roundtrip(self):
        (self.source / "empty").mkdir()
        (self.source / "nested").mkdir()
        body = "正文\r\n尾\n\n"
        original = self.source / "nested" / "body"
        original.write_bytes(body.encode("utf-8"))
        text = self.merged()
        plan = self.plan(text)
        self.assertEqual(len(plan.directories), 2)
        result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        self.assertEqual(self.mapped(original).read_bytes(), body.encode("utf-8"))
        self.assertTrue(self.mapped(self.source / "empty").is_dir())
        self.assertEqual(len(result.completed_files), 1)
        self.assertEqual(result.required_directory_count, 2)
        read_recovery_evidence(result.backup_root)

    def test_mixed_three_kinds_restore_entities_without_creating_links(self):
        (self.source / "empty").mkdir()
        (self.source / "body").write_bytes(b"body")
        for name, raw, kind in (
            ("file", "missing", "file_symlink"),
            ("directory", self.outside, "directory_symlink"),
            ("junction", self.outside, "junction"),
        ):
            self.link(self.source / name, raw, kind)
        text = self.merged()
        plan = self.plan(text, authorize=lambda title, description: True)
        original_open = Path.open
        original_scandir = os.scandir

        def guard_open(path, *args, **keywords):
            if path == self.private:
                raise AssertionError("拆分不得补读来源定义引用的目标正文")
            return original_open(path, *args, **keywords)

        def guard_scandir(path):
            if Path(path) == self.outside:
                raise AssertionError("拆分不得因来源目录定义枚举引用目标")
            return original_scandir(path)

        with patch("pathlib.Path.open", new=guard_open), patch(
            "os.scandir", new=guard_scandir,
        ), patch(
            "core.filesystem_changes.prepare_link_creation",
            side_effect=AssertionError("拆分不得创建链接"),
        ):
            result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        self.assertEqual(len(result.completed_files), 1)
        self.assertFalse(os.path.lexists(self.mapped(self.source / "file")))
        self.assertEqual(result.skipped_count, 1)
        for name in ("directory", "junction"):
            actual = inspect_link_path(
                self.mapped(self.source / name), follow_final_link=False,
            )
            self.assertEqual(actual.kind, "directory")
            self.assertFalse(actual.has_links)
        self.assertEqual(self.private.read_bytes(), b"private")
        evidence = read_recovery_evidence(result.backup_root)
        self.assertTrue(all(
            member["original"]["kind"] == "absent"
            for member in evidence.document.value["objects"]
        ))

    def test_in_place_file_definition_is_not_rebuilt_or_used_as_body(self):
        target = self.destination / "alias"
        specification = LinkSpecification("file_symlink", "body", True)
        text = (
            self.block(self.destination / "empty", kind="directory")
            + self.block(target, kind="file_symlink", specification=specification, index=2)
            + self.block(self.destination / "body", "正文\r\n", index=3)
        )
        descriptions = []
        plan = self.plan(
            text, mode="in_place",
            authorize=lambda title, description:
            descriptions.append(description) or True,
        )
        result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        self.assertFalse(os.path.lexists(target))
        self.assertEqual(
            (self.destination / "body").read_bytes(),
            "正文\r\n".encode("utf-8"),
        )
        self.assertEqual(result.skipped_count, 1)
        self.assertIn("没有普通文件正文", descriptions[0])
        self.assertIn("来源保存原定义", descriptions[0])

    def test_directory_record_preserves_unlisted_member_and_only_protects_work(self):
        folder = self.destination / "folder"
        folder.mkdir()
        extra = folder / "extra"
        extra.write_bytes(b"keep-extra")
        body = folder / "body"
        body.write_bytes(b"old")
        text = (
            self.block("folder", kind="directory")
            + self.block("folder/body", "new", index=2)
        )
        plan = self.plan(text)
        self.assertEqual(tuple(member.target for member in plan.projection.objects), (body,))
        result = RestoreTask(plan).execute()
        self.assertEqual(extra.read_bytes(), b"keep-extra")
        self.assertEqual(body.read_bytes(), b"new")
        evidence = read_recovery_evidence(result.backup_root)
        self.assertEqual(evidence.materials["o000001"].source_bytes, b"old")

    def test_parent_leaf_duplicate_and_actual_alias_conflicts_reject_before_work(self):
        bad = (
            self.block("same", "first") + self.block("same", "second", index=2),
            self.block("leaf", "first") + self.block("leaf/child", "second", index=2),
            self.block(
                "link", kind="file_symlink",
                specification=LinkSpecification("file_symlink", "missing", True),
            ) + self.block("link/child", "second", index=2),
        )
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.plan(text, authorize=lambda title, description: True)
        self.assertEqual(tuple(self.backups.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())

        body = self.outside / "body"
        body.write_bytes(b"old")
        self.link(self.destination / "one", body, "file_symlink")
        self.link(self.destination / "two", body, "file_symlink")
        with self.assertRaises(ValueError):
            self.plan(
                self.block("one", "new") + self.block("two", "new", index=2),
                authorize=lambda title, description: True,
            )
        self.assertEqual(body.read_bytes(), b"old")

    def test_first_old_body_read_requires_authorization_and_hardlink_writeback(self):
        body = self.outside / "body"
        body.write_bytes(b"old")
        peer = self.root / "peer"
        os.link(body, peer)
        parent = self.destination / "parent"
        self.link(parent, self.outside, "directory_symlink")
        before = observe_file(body)
        authorized = False
        original_open = Path.open

        def guard(path, *args, **keywords):
            if path == body:
                self.assertTrue(authorized, "第一次旧正文读取必须先授权")
            return original_open(path, *args, **keywords)

        def authorize(title, description):
            nonlocal authorized
            self.assertIn(str(body), description)
            authorized = True
            return True

        with patch("pathlib.Path.open", new=guard):
            plan = self.plan(self.block("parent/body", "new"), authorize=authorize)
            result = RestoreTask(plan).execute()
        self.assertEqual(body.read_bytes(), b"new")
        self.assertEqual(peer.read_bytes(), b"new")
        self.assertEqual(observe_file(body).object_identity, before.object_identity)
        self.assertEqual(result.results[-1].mechanism, "in_place")
        self.assertEqual(plan.projection.objects[0].target, body)

    def test_cancelled_authorization_reads_no_old_body_and_creates_nothing(self):
        body = self.outside / "body"
        body.write_bytes(b"old")
        self.link(self.destination / "parent", self.outside, "directory_symlink")
        original_open = Path.open

        def guard(path, *args, **keywords):
            if path == body:
                raise AssertionError("取消授权不得读取旧正文")
            return original_open(path, *args, **keywords)

        with patch("pathlib.Path.open", new=guard):
            with self.assertRaises(ValueError):
                self.plan(
                    self.block("parent/body", "new"),
                    authorize=lambda title, description: False,
                )
        self.assertEqual(tuple(self.backups.iterdir()), ())
        self.assertEqual(body.read_bytes(), b"old")

    def test_source_definition_never_replaces_existing_target_link(self):
        target = self.destination / "alias"
        original = self.link(target, "missing", "file_symlink")
        for raw in ("missing", "other"):
            with self.subTest(raw=raw):
                plan = self.plan(
                    self.block(
                        "alias", kind="file_symlink",
                        specification=LinkSpecification(
                            "file_symlink", raw, True,
                        ),
                    ),
                    authorize=lambda title, description: True,
                )
                self.assertFalse(plan.entries)
                self.assertIsNone(plan.backup_root)
                self.assertIn("没有普通文件正文", plan.skipped[0][1])
                self.assertEqual(
                    inspect_link_path(target, follow_final_link=False),
                    original,
                )

    def test_new_external_entry_after_preview_stops_before_backup_or_work(self):
        plan = self.plan(self.block("new-body", "prepared"))
        target = self.destination / "new-body"
        target.write_bytes(b"external")
        task = RestoreTask(plan)
        with self.assertRaises(RestoreExecutionError) as caught:
            task.execute()
        self.assertEqual(caught.exception.result.results, ())
        self.assertEqual(target.read_bytes(), b"external")
        self.assertTrue(task.consumed)
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_backup_barrier_precedes_work_and_failure_stops_without_rollback(self):
        first = self.destination / "first"
        second = self.destination / "second"
        first.write_bytes(b"old-first")
        plan = self.plan(
            self.block("first", "new-first") + self.block("second", "new-second", index=2)
        )
        task = RestoreTask(plan)
        original_publish = task._perform_member

        def publish(member, effect):
            self.assertTrue(task.protection.backup_completed)
            read_recovery_evidence(task.backup_root)
            if member.target == second:
                raise PermissionError("injected second member failure")
            return original_publish(member, effect)

        with patch.object(task, "_perform_member", side_effect=publish):
            with self.assertRaises(RestoreExecutionError) as caught:
                task.execute()
        self.assertEqual(first.read_bytes(), b"new-first")
        self.assertFalse(second.exists())
        self.assertEqual(caught.exception.result.completed_files, (first,))
        self.assertFalse(caught.exception.result.work_completed)
        self.assertTrue(task.backup_root.is_dir())
        evidence = read_recovery_evidence(task.backup_root)
        self.assertEqual(evidence.materials["o000001"].source_bytes, b"old-first")
        with self.assertRaises(ValueError):
            task.execute()

    def test_plain_existing_and_missing_targets_use_explicit_content_access(self):
        existing = self.destination / "existing"
        missing = self.destination / "missing"
        existing.write_bytes(b"old")
        from services import restore_service

        original_observe = restore_service.observe_accessed_file
        accesses = []

        def observe(access, **keywords):
            self.assertTrue(access.follow_final_link)
            self.assertIsNone(access.entry_link)
            self.assertIn(access.kind, ("file", "absent"))
            accesses.append(access)
            return original_observe(access, **keywords)

        with patch(
            "services.restore_service.observe_accessed_file",
            side_effect=observe,
        ):
            plan = self.plan(
                self.block("existing", "new-existing")
                + self.block("missing", "new-missing", index=2)
            )
        self.assertEqual(
            tuple(access.actual_path for access in accesses),
            (existing, missing),
        )
        self.assertEqual(tuple(access.kind for access in accesses), ("file", "absent"))
        self.assertTrue(all(
            member.expected.access is access
            for member, access in zip(plan.entries, accesses)
        ))
        self.assertEqual(existing.read_bytes(), b"old")
        self.assertFalse(missing.exists())
        self.assertEqual(tuple(self.backups.iterdir()), ())
        result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        self.assertEqual(existing.read_bytes(), b"new-existing")
        self.assertEqual(missing.read_bytes(), b"new-missing")
        read_recovery_evidence(result.backup_root)

    def test_existing_file_skip_does_not_enter_content_acquisition(self):
        target = self.destination / "body"
        target.write_bytes(b"old")
        with patch(
            "services.restore_service.observe_accessed_file",
            side_effect=AssertionError("固定跳过不得获取旧正文"),
        ):
            plan = self.plan(self.block("body", "new"), policy="skip")
        self.assertEqual(plan.entries, ())
        self.assertEqual(len(plan.skipped), 1)
        self.assertIsNone(plan.backup_root)
        self.assertEqual(plan.projection.objects, ())
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_backup_failure_cleans_only_task_before_work(self):
        target = self.destination / "body"
        target.write_bytes(b"old")
        task = RestoreTask(self.plan(self.block("body", "new")))
        with patch(
            "core.recovery_protection.prepare_internal_material",
            side_effect=OSError("injected backup failure"),
        ):
            with self.assertRaises(RestoreExecutionError):
                task.execute()
        self.assertEqual(target.read_bytes(), b"old")
        self.assertFalse(task.backup_root.exists())
        self.assertIn("已清理", task.protection.backup_cleanup_report)
        self.assertEqual(task.results, ())

    def test_completed_fact_failure_preserves_entity_work_and_reports_state(self):
        target = self.destination / "body"
        task = RestoreTask(self.plan(self.block("body", "prepared")))
        with patch.object(
            RecoveryProtectionSession, "complete",
            side_effect=OSError("injected completed fact failure"),
        ):
            with self.assertRaises(RestoreExecutionError) as caught:
                task.execute()
        self.assertEqual(target.read_bytes(), b"prepared")
        self.assertEqual(caught.exception.result.completed_files, (target,))
        self.assertFalse(caught.exception.result.work_completed)
        self.assertIsNotNone(publication_failure_title(caught.exception))
        read_recovery_evidence(task.backup_root)

    def test_patch_actual_directory_first_enumeration_has_separate_authorization(self):
        tree = self.outside / "tree"
        tree.mkdir()
        (tree / "body").write_bytes(b"body")
        parent = self.destination / "parent"
        self.link(parent, self.outside, "directory_symlink")
        boundary = "ZIPATCH_BOUNDARY_SUBJECT_A_TEST_20261121122538_R4K8"
        package = "\n".join((
            f'<<ZIPATCH_V3 boundary="{boundary}">>',
            '---OP delete_dir id="delete001"',
            "---PATH", "parent/tree", boundary,
            "---END_OP", "<</ZIPATCH_V3>>",
        ))
        original_scandir = os.scandir
        authorized = False
        entered = []

        def guard(path):
            if Path(path) == tree:
                self.assertTrue(authorized)
                entered.append(Path(path))
            return original_scandir(path)

        with patch("os.scandir", new=guard):
            rejected = preview_patch(
                None, self.destination, package,
                allow_delete=True, normalize_text_newlines=False,
                backup_dir=str(self.backups),
                authorize_patch_scope=lambda scopes, root: ("authorize",),
            )
        self.assertTrue(rejected.has_errors)
        self.assertEqual(entered, [])

        def authorize(title, description):
            nonlocal authorized
            self.assertIn(str(tree), description)
            self.assertEqual(entered, [])
            authorized = True
            return True

        with patch("os.scandir", new=guard):
            accepted = preview_patch(
                None, self.destination, package,
                allow_delete=True, normalize_text_newlines=False,
                backup_dir=str(self.backups),
                authorize_patch_scope=lambda scopes, root: ("authorize",),
                authorize_patch_enumeration=authorize,
            )
        self.assertFalse(accepted.has_errors, accepted.preview_text)
        self.assertEqual(entered, [tree])
        self.assertEqual((tree / "body").read_bytes(), b"body")

    def test_materialization_consumes_fixed_members_without_second_enumeration(self):
        folder = self.source / "tree"
        folder.mkdir()
        (folder / "body").write_bytes(b"body")
        metadata = inspect_scope_tree(folder)
        with patch("os.scandir", side_effect=AssertionError("不得二次枚举选择范围")):
            actual = materialize_scope_observation(metadata)
        self.assertEqual(dict(actual.files)["body"].source_bytes, b"body")

    def test_marker_structure_prefix_and_late_source_change_are_rejected(self):
        with self.assertRaises(OutputSettingsError):
            validate_markers('ZIPATCH_ENTRY:{"kind":"directory"}', CODE_FOOTER_LINE)
        first = self.source / "a"
        second = self.source / "b"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        from services import merge_service
        original_read = merge_service.read_text_content_for_merge

        def read(path, **keywords):
            result = original_read(path, **keywords)
            if Path(path) == second:
                first.write_bytes(b"changed")
            return result

        with patch("services.merge_service.read_text_content_for_merge", side_effect=read):
            with self.assertRaises(ValueError):
                self.merged()
        self.assertFalse(self.output.exists())
        self.assertEqual(first.read_bytes(), b"changed")


@unittest.skipUnless(os.name == "nt", "主体A真实页面使用Windows")
class StructureSplitPageTests(unittest.TestCase):
    def test_real_restore_page_prepares_and_executes_structure_task(self):
        import tkinter as tk
        from types import SimpleNamespace
        from ui.config_editing import ConfigCommitController

        # 本轮需要真实Tk证据；初始化失败直接失败，不用跳过冒充通过。
        root = tk.Tk()
        root.withdraw()
        with tempfile.TemporaryDirectory(prefix="zipatch_subject_a_page_") as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir()
            backups = base / "backups"
            backups.mkdir()
            config = deepcopy(DEFAULT_CONFIG)
            config["restore"].update({
                "target_folder": str(target),
                "backup_dir": str(backups),
                "open_after_done": False,
                "source_text": (
                    encode_container_declaration(
                        "4a9e268a78574ffcb19ed7b2c23d0568",
                        CODE_HEADER_LINE, CODE_FOOTER_LINE,
                    ) + "\r\n"
                    "---第1个文件--- [4a9e268a78574ffcb19ed7b2c23d0568]\r\nempty\r\n"
                    'ZIPATCH_ENTRY:{"kind":"directory"}\r\n'
                    + CODE_HEADER_LINE + " [4a9e268a78574ffcb19ed7b2c23d0568]\r\n\r\n"
                    + CODE_FOOTER_LINE + " [4a9e268a78574ffcb19ed7b2c23d0568]\r\n"
                    "---第2个文件--- [4a9e268a78574ffcb19ed7b2c23d0568]\r\nalias\r\n"
                    'ZIPATCH_ENTRY:{"kind":"file_symlink","raw_target":"missing","relative":true}\r\n'
                    + CODE_HEADER_LINE + " [4a9e268a78574ffcb19ed7b2c23d0568]\r\n\r\n"
                    + CODE_FOOTER_LINE + " [4a9e268a78574ffcb19ed7b2c23d0568]\r\n"
                    "ZIPATCH_MERGED_END:4a9e268a78574ffcb19ed7b2c23d0568\r\n"
                ),
            })
            manager = ConfigSaveManager(
                config, path=base / "config.json", needs_write=True,
            )
            commits = ConfigCommitController(root, manager)
            app = SimpleNamespace(
                root=root, config_data=config, commits=commits,
                log=lambda message: None, status_var=tk.StringVar(master=root),
            )
            panel = None
            try:
                panel = RestorePanel(root, app)
                panel.pack()
                with patch(
                    "panels.restore_panel.safe_ask_risk_confirm", return_value=True,
                ), patch("panels.restore_panel.safe_show_info"), patch(
                    "ui.restore_scope.select_work_scope",
                    return_value=("authorize", "authorize"),
                ):
                    panel.run_split(True)
                self.assertTrue((target / "empty").is_dir())
                self.assertFalse(os.path.lexists(target / "alias"))
                report = panel.result_text.get("1.0", "end-1c")
                self.assertIn("没有普通文件正文", report)
                self.assertIn("未创建或移除链接", report)
                self.assertIn("普通目录要求已满足：1", report)
                self.assertTrue(panel.last_backup_root)
                read_recovery_evidence(panel.last_backup_root)
            finally:
                if os.path.lexists(target / "alias"):
                    os.unlink(target / "alias")
                root.destroy()


if __name__ == "__main__":
    unittest.main()
