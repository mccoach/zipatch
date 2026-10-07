# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import ZipatchApp
from core.config import ConfigSaveManager, SaveResult, SaveStatus, deep_merge_config
from core.constants import DEFAULT_CONFIG
from panels.patch_panel import PatchPanel
from ui.config_editing import ConfigCommitController


class BusinessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.manager = ConfigSaveManager(
            self.config, Path(self.directory.name) / "config.json", needs_write=True,
        )
        self.manager.save()
        self.controller = ConfigCommitController(None, self.manager)

    def make_app(self):
        root = Mock()
        root._zipatch_closing = False
        return SimpleNamespace(
            root=root, commits=self.controller, config_manager=self.manager,
            _scroll_after_id=None,
        )

    def test_exit_cancel_keeps_root_and_restores_exit_flag(self):
        app = self.make_app()
        with (
            patch("app.exit_blocking_popup", return_value=None),
            patch("app.prepare_all_for_exit", return_value=False),
        ):
            ZipatchApp.close_application(app)
        app.root.destroy.assert_not_called()
        self.assertFalse(app.root._zipatch_closing)

    def test_final_save_failure_cancel_keeps_application(self):
        app = self.make_app()
        failed = SaveResult(SaveStatus.FAILED, PermissionError("denied"))
        with (
            patch("app.exit_blocking_popup", return_value=None),
            patch("app.prepare_all_for_exit", return_value=True),
            patch.object(self.manager, "save", return_value=failed),
            patch("app.safe_ask_yes_no", return_value=False),
        ):
            ZipatchApp.close_application(app)
        app.root.destroy.assert_not_called()
        self.assertFalse(app.root._zipatch_closing)

    def test_final_save_failure_explicit_force_exit(self):
        app = self.make_app()
        failed = SaveResult(SaveStatus.FAILED, PermissionError("denied"))
        with (
            patch("app.exit_blocking_popup", return_value=None),
            patch("app.prepare_all_for_exit", return_value=True),
            patch.object(self.manager, "save", return_value=failed),
            patch("app.safe_ask_yes_no", return_value=True),
        ):
            ZipatchApp.close_application(app)
        app.root.destroy.assert_called_once()

    def test_active_operation_blocks_exit_before_draft_processing(self):
        app = self.make_app()
        with (
            self.controller.operation(),
            patch("app.exit_blocking_popup", return_value=None),
            patch("app.safe_show_info"),
            patch("app.prepare_all_for_exit") as prepare,
        ):
            ZipatchApp.close_application(app)
        prepare.assert_not_called()
        app.root.destroy.assert_not_called()

    def test_restore_parameters_include_backup_directory(self):
        cfg = self.config["patch"]
        cfg["patch_mode"] = "restore"
        expected = {
            "project_root", "patch_mode", "backup_enabled", "backup_dir",
            "open_backup_after_done", "restore_source_dir",
            "keep_restore_source_path",
        }
        parameters = {field: cfg[field] for field in expected}
        prepare = Mock(return_value=parameters)
        panel = SimpleNamespace(cfg=cfg, prepare=prepare)
        result = PatchPanel.prepare_parameters(panel)
        self.assertIs(result, parameters)
        prepare.assert_called_once_with(expected)

    def test_business_precondition_failure_returns_false(self):
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.assertFalse(self.controller.prepare_business("scan"))
        self.assertTrue(self.manager.needs_save)

    def test_business_precondition_persists_deferred_session(self):
        self.manager.accept(self.config, {"active_mode": "patch"}, persist=False)
        self.assertTrue(self.controller.prepare_business("patch"))
        self.assertFalse(self.manager._session_pending)

    def test_successful_business_result_failure_has_explicit_message(self):
        display = Mock(side_effect=lambda value: value)
        panel = SimpleNamespace(
            cfg=self.config["patch"], commits=self.controller,
            root=None, display_result=display,
        )
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("panels.patch_panel.safe_show_error") as show,
        ):
            result = PatchPanel.finish_result(
                panel, "result", {"restore_source_dir": ""},
                outcome="备份还原已经完成",
            )
        self.assertEqual(result.status, SaveStatus.FAILED)
        self.assertIn("备份还原已经完成", show.call_args.args[1])
        self.assertEqual(self.config["patch"]["last_result_text"], "result")
        self.assertTrue(self.manager.needs_save)

    def test_result_clear_is_blocked_during_business(self):
        panel = SimpleNamespace(
            commits=self.controller, display_result=Mock(), log=Mock(),
        )
        with self.controller.operation():
            PatchPanel.clear_result(panel)
        panel.display_result.assert_not_called()
        panel.log.assert_not_called()

    def test_patch_apply_scope_cleans_up_after_exception(self):
        def fail():
            raise ValueError("defect")

        panel = SimpleNamespace(commits=self.controller, _apply=fail)
        with self.assertRaises(ValueError):
            PatchPanel.apply(panel)
        self.assertEqual(self.controller.operation_depth, 0)


if __name__ == "__main__":
    unittest.main()