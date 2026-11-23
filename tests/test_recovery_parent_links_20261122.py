# -*- coding: utf-8 -*-

"""主动恢复父链授权、固定实际范围、强制保护及失败回滚回归。"""

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from core.file_observation import observe_file
from core.filesystem_changes import execute_filesystem_change, prepare_link_creation
from core.link_paths import (
    LinkSpecification, inspect_link_path, specification_from_link,
)
from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)
from core.recovery_protection import (
    RecoveryProtectionSession, read_recovery_evidence,
)
from services.patch_restore import BackupRestoreExecutionError
from services.patch_service import (
    apply_backup_restore, confirm_backup_restore, preview_backup_restore,
)
from services.recovery import plan_restore


@unittest.skipUnless(os.name == "nt", "恢复父链正式业务使用Windows")
class RecoveryParentLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_recovery_parent_",
        )
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.nominal_parent = self.project / "folder"
        self.nominal_parent.mkdir()
        self.nominal_body = self.nominal_parent / "body.txt"
        self.nominal_body.write_bytes(b"original")
        original = observe_file(
            self.nominal_body, include_bytes=True,
            include_digest=True, include_mtime=True,
        )
        projection = build_protection_projection(
            (FixedUnit(
                ("edit001",),
                (FixedEffect(original, state_from_bytes(b"changed")),),
            ),),
            forbidden_roots=(self.project,),
        )
        self.session = RecoveryProtectionSession(
            projection, self.backups / "source-task",
            "source-task", "patch", material_base=self.project,
        )
        self.session.protect_all()
        self.actual_parent = self.root / "actual"
        self.nominal_parent.rename(self.actual_parent)
        self.body = self.actual_parent / "body.txt"
        self.body.write_bytes(b"current-before-restore")
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link_parent(self, kind="directory_symlink", target=None):
        target = self.actual_parent if target is None else target
        if kind == "junction":
            receipt = execute_filesystem_change(prepare_link_creation(
                observe_file(self.nominal_parent),
                LinkSpecification(
                    "junction", "\\\\?\\" + str(target), False,
                ),
            ))
            self.assertTrue(receipt.succeeded)
        else:
            os.symlink(
                target, self.nominal_parent, target_is_directory=True,
            )
        self.links.append((self.nominal_parent, True))
        return inspect_link_path(
            self.nominal_parent, follow_final_link=False,
        )

    def preview(self, authorize=None):
        return preview_backup_restore(
            str(self.session.task_root), str(self.backups),
            backup_project_root=str(self.project),
            authorize_restore_scope=authorize,
        )

    def test_unapproved_parent_reads_no_actual_old_body(self):
        self.link_parent()
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == self.body:
                raise AssertionError("未授权不能读取实际旧目标正文")
            return original_open(path, *arguments, **keywords)

        with patch("pathlib.Path.open", new=opened):
            with self.assertRaises(ValueError):
                self.preview()
        self.assertEqual(self.body.read_bytes(), b"current-before-restore")
        self.assertEqual(
            {entry.name for entry in self.backups.iterdir()},
            {"source-task"},
        )

    def test_cancelled_parent_access_creates_no_protection_task(self):
        self.link_parent()
        calls = []

        def authorize(title, description):
            calls.append((title, description))
            self.assertIn(str(self.nominal_body), description)
            self.assertIn(str(self.body), description)
            return False

        with self.assertRaises(ValueError):
            self.preview(authorize)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.body.read_bytes(), b"current-before-restore")
        self.assertEqual(
            {entry.name for entry in self.backups.iterdir()},
            {"source-task"},
        )

    def test_symlink_and_junction_authorized_restore_protect_actual_body(self):
        for kind in ("directory_symlink", "junction"):
            with self.subTest(kind=kind):
                self.body.write_bytes(b"current-before-restore")
                parent = self.link_parent(kind)
                authorized = False
                original_open = Path.open

                def opened(path, *arguments, **keywords):
                    if path == self.body:
                        self.assertTrue(authorized)
                    return original_open(path, *arguments, **keywords)

                def authorize(title, description):
                    nonlocal authorized
                    self.assertEqual(title, "备份恢复实际目标访问授权")
                    self.assertIn(str(self.nominal_body), description)
                    self.assertIn(str(self.body), description)
                    authorized = True
                    return True

                with patch("pathlib.Path.open", new=opened):
                    preview = self.preview(authorize)
                    action = preview.execution.plan.actions[0]
                    self.assertEqual(action.access.nominal_path, self.nominal_body)
                    self.assertEqual(action.target, self.body)
                    self.assertEqual(
                        Path(action.original["target"]), self.nominal_body,
                    )
                    self.assertEqual(
                        tuple(
                            member.target
                            for member in preview.execution.execution.projection.objects
                        ),
                        (self.body,),
                    )
                    confirm_backup_restore(preview)
                    result = apply_backup_restore(preview)

                self.assertTrue(result.complete_original_state)
                self.assertEqual(self.body.read_bytes(), b"original")
                self.assertEqual(
                    inspect_link_path(
                        self.nominal_parent, follow_final_link=False,
                    ),
                    parent,
                )
                protected = read_recovery_evidence(result.backup_root)
                self.assertEqual(
                    Path(protected.document.value["objects"][0]["target"]),
                    self.body,
                )
                self.assertEqual(
                    protected.materials["o000001"].source_bytes,
                    b"current-before-restore",
                )
                self.assertNotEqual(
                    protected.document.value["objects"][0]["target"],
                    str(self.nominal_parent),
                )
                os.rmdir(self.nominal_parent)

    def test_retarget_during_authorization_reads_neither_actual_body(self):
        self.link_parent()
        other = self.root / "other"
        other.mkdir()
        external = other / "body.txt"
        external.write_bytes(b"external")
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path in (self.body, external):
                raise AssertionError("改指向后不能获取原目标或新目标正文")
            return original_open(path, *arguments, **keywords)

        def authorize(title, description):
            os.rmdir(self.nominal_parent)
            os.symlink(
                other, self.nominal_parent, target_is_directory=True,
            )
            return True

        with patch("pathlib.Path.open", new=opened):
            with self.assertRaises(ValueError):
                self.preview(authorize)
        self.assertEqual(self.body.read_bytes(), b"current-before-restore")
        self.assertEqual(external.read_bytes(), b"external")

    def test_retarget_after_preview_stops_before_protection_or_work(self):
        self.link_parent()
        preview = self.preview(lambda title, description: True)
        other = self.root / "other"
        other.mkdir()
        external = other / "body.txt"
        external.write_bytes(b"external")
        os.rmdir(self.nominal_parent)
        os.symlink(other, self.nominal_parent, target_is_directory=True)
        confirm_backup_restore(preview)
        with self.assertRaises(BackupRestoreExecutionError) as caught:
            apply_backup_restore(preview)
        self.assertEqual(caught.exception.executor.execution.results, ())
        self.assertEqual(self.body.read_bytes(), b"current-before-restore")
        self.assertEqual(external.read_bytes(), b"external")
        self.assertFalse(preview.execution.backup_root.exists())
        self.assertTrue(preview.execution.consumed)

    def test_failed_completion_fact_rolls_back_actual_body_without_second_backup(self):
        original_parent = self.link_parent()
        preview = self.preview(lambda title, description: True)
        confirm_backup_restore(preview)
        before = set(self.backups.iterdir())

        with patch.object(
            RecoveryProtectionSession, "complete",
            side_effect=OSError("injected recovery completed fact failure"),
        ):
            with self.assertRaises(BackupRestoreExecutionError) as caught:
                apply_backup_restore(preview)

        executor = caught.exception.executor
        self.assertEqual(executor.rollback_status, "completed")
        self.assertEqual(self.body.read_bytes(), b"current-before-restore")
        self.assertEqual(
            set(self.backups.iterdir()) - before,
            {executor.backup_root},
        )
        self.assertEqual(
            inspect_link_path(
                self.nominal_parent, follow_final_link=False,
            ),
            original_parent,
        )
        read_recovery_evidence(self.session.task_root)
        read_recovery_evidence(executor.backup_root)

    def test_retarget_after_work_blocks_rollback_and_keeps_both_backups(self):
        self.link_parent()
        preview = self.preview(lambda title, description: True)
        confirm_backup_restore(preview)
        other = self.root / "other"
        other.mkdir()
        external = other / "body.txt"
        external.write_bytes(b"external")

        def retarget_and_fail(session, unit_id, results):
            os.rmdir(self.nominal_parent)
            os.symlink(
                other, self.nominal_parent, target_is_directory=True,
            )
            raise OSError("injected access drift after work")

        with patch.object(
            RecoveryProtectionSession, "complete", new=retarget_and_fail,
        ):
            with self.assertRaises(BackupRestoreExecutionError) as caught:
                apply_backup_restore(preview)

        executor = caught.exception.executor
        self.assertEqual(executor.rollback_status, "failed")
        self.assertEqual(self.body.read_bytes(), b"original")
        self.assertEqual(external.read_bytes(), b"external")
        self.assertTrue(executor.consumed)
        read_recovery_evidence(self.session.task_root)
        read_recovery_evidence(executor.backup_root)

    def test_actual_target_aliases_are_rejected_before_old_body_read(self):
        # 两个最初独立目标后来成为同一实际位置，不能恢复任意一个子集。
        second_parent = self.project / "second"
        second_parent.mkdir()
        second_body = second_parent / "body.txt"
        second_body.write_bytes(b"second-original")
        original = observe_file(
            second_body, include_bytes=True,
            include_digest=True, include_mtime=True,
        )
        # 这是另一份独立备份任务。先准备它的真实初始现场，
        # 再取得自己的固定观察；不能复用setUp中写入前的旧mtime。
        self.actual_parent.rename(self.nominal_parent)
        self.nominal_body.write_bytes(b"original")
        first_original = observe_file(
            self.nominal_body, include_bytes=True,
            include_digest=True, include_mtime=True,
        )
        projection = build_protection_projection(
            (
                FixedUnit(
                    ("first001",),
                    (FixedEffect(first_original, state_from_bytes(b"first-new")),),
                ),
                FixedUnit(
                    ("second001",),
                    (FixedEffect(original, state_from_bytes(b"second-new")),),
                ),
            ),
            forbidden_roots=(self.project,),
        )
        session = RecoveryProtectionSession(
            projection, self.backups / "two-targets",
            "two-targets", "patch", material_base=self.project,
        )
        session.protect_all()
        self.nominal_parent.rename(self.actual_parent)
        second_body.unlink()
        second_parent.rmdir()
        self.link_parent()
        os.symlink(
            self.actual_parent, second_parent, target_is_directory=True,
        )
        self.links.append((second_parent, True))
        evidence = read_recovery_evidence(session.task_root)
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == self.body:
                raise AssertionError("实际目标冲突应先于旧正文获取")
            return original_open(path, *arguments, **keywords)

        with patch("pathlib.Path.open", new=opened):
            with self.assertRaisesRegex(ValueError, "实际恢复范围"):
                plan_restore(
                    evidence,
                    authorize_restore_scope=lambda title, description: True,
                )

    def test_independent_recovery_page_uses_shared_access_authorization(self):
        import threading
        import tkinter as tk
        from types import SimpleNamespace

        from panels.backup_restore_panel import BackupRestorePanel
        from ui.config_editing import ConfigCommitController

        self.link_parent()
        self.body.write_bytes(b"current-before-restore")
        main_thread = threading.get_ident()
        root = tk.Tk()
        root.withdraw()
        errors = []
        root.report_callback_exception = (
            lambda error_type, error, traceback: errors.append(error)
        )
        config = deepcopy(DEFAULT_CONFIG)
        config["backup_restore"].update({
            "backup_dir": str(self.backups),
            "restore_source_dir": str(self.session.task_root),
            "keep_restore_source_path": True,
            "open_backup_after_done": False,
        })
        manager = ConfigSaveManager(
            config, path=self.root / "recovery-page-config.json", needs_write=True,
        )
        commits = ConfigCommitController(root, manager)
        app = SimpleNamespace(
            root=root, config_data=config, commits=commits,
            log=lambda message: None,
            status_var=tk.StringVar(master=root),
        )
        authorization_threads = []

        def authorize(parent, commits, *, title, introduction, choices, details):
            authorization_threads.append(threading.get_ident())
            self.assertIn(str(self.body), details)
            self.assertIn("读取当前正文", introduction)
            self.assertEqual(len(choices), 1)
            return ("authorize",)

        def confirm(parent, preview, **keywords):
            confirm_backup_restore(preview)
            return True

        try:
            panel = BackupRestorePanel(root, app)
            panel.pack()
            with patch(
                "ui.restore_scope.select_work_scope", side_effect=authorize,
            ), patch(
                "ui.backup_restore.select_backup_restore", side_effect=confirm,
            ), patch(
                "ui.backup_restore.safe_show_info",
            ), patch(
                "ui.backup_restore.safe_show_error",
            ) as show_error:
                panel.run(True)
            show_error.assert_not_called()
            self.assertEqual(errors, [])
            self.assertEqual(authorization_threads, [main_thread])
            self.assertEqual(self.body.read_bytes(), b"original")
            self.assertTrue(panel.last_backup_root)
            read_recovery_evidence(panel.last_backup_root)
            self.assertEqual(commits.operation_depth, 0)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()