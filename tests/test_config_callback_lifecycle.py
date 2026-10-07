# -*- coding: utf-8 -*-

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import TclError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import ZipatchApp
from core.config import (
    ConfigSaveManager, SaveResult, SaveStatus, deep_merge_config,
)
from core.constants import DEFAULT_CONFIG
from panels.merge_panel import MergePanel
from panels.restore_panel import RestorePanel
from panels.scan_panel import ScanPanel
from ui.config_editing import ConfigCommitController
from ui.favorites import FavoriteTextBoxController
from ui.window_manager import (
    register_popup, unregister_popup, validate_temporary_popups,
)


class CallbackLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.manager = ConfigSaveManager(
            self.config, Path(self.directory.name) / "config.json",
            needs_write=True,
        )
        self.manager.save()
        self.controller = ConfigCommitController(None, self.manager)

    def test_error_message_protects_caller_until_return(self):
        self.manager.accept(self.config["scan"], {"include_date": True})
        observed = []

        def report(*args, **kwargs):
            observed.append(self.controller.operation_depth)

        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error", side_effect=report),
        ):
            result = self.controller.finish()
        self.assertEqual(result.status, SaveStatus.FAILED)
        self.assertEqual(observed, [1])
        self.assertEqual(self.controller.operation_depth, 0)

    def test_error_message_exception_restores_operation_depth(self):
        self.manager.accept(self.config["scan"], {"include_date": True})
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch(
                "ui.config_editing.safe_show_error",
                side_effect=RuntimeError("message defect"),
            ),
        ):
            with self.assertRaises(RuntimeError):
                self.controller.finish()
        self.assertEqual(self.controller.operation_depth, 0)
        self.assertTrue(self.manager.needs_save)

    def test_successful_save_does_not_create_lifecycle_scope(self):
        self.manager.accept(self.config["scan"], {"include_date": True})
        with patch.object(
            self.controller, "operation", side_effect=AssertionError("unexpected scope"),
        ):
            self.assertTrue(self.controller.finish().persisted)

    def test_three_business_entries_share_lifecycle_rule(self):
        entries = (
            (ScanPanel.execute, "_execute"),
            (MergePanel.execute_regular_merge, "_execute_regular_merge"),
            (RestorePanel.execute, "_execute"),
        )
        for entry, implementation_name in entries:
            with self.subTest(entry=entry.__qualname__):
                observed = []

                def execute():
                    observed.append(self.controller.operation_depth)
                    return "completed"

                panel = SimpleNamespace(commits=self.controller)
                setattr(panel, implementation_name, execute)
                self.assertEqual(entry(panel), "completed")
                self.assertEqual(observed, [1])
                self.assertEqual(self.controller.operation_depth, 0)

    def test_three_business_entries_reject_reentrant_execution(self):
        entries = (
            (ScanPanel.execute, "_execute"),
            (MergePanel.execute_regular_merge, "_execute_regular_merge"),
            (RestorePanel.execute, "_execute"),
        )
        for entry, implementation_name in entries:
            with self.subTest(entry=entry.__qualname__):
                execute = Mock()
                panel = SimpleNamespace(commits=self.controller)
                setattr(panel, implementation_name, execute)
                with self.controller.operation():
                    entry(panel)
                execute.assert_not_called()
                self.assertEqual(self.controller.operation_depth, 0)

    def test_business_exception_restores_lifecycle(self):
        def execute():
            raise ValueError("business failure")

        panel = SimpleNamespace(commits=self.controller, _execute=execute)
        with self.assertRaises(ValueError):
            ScanPanel.execute(panel)
        self.assertEqual(self.controller.operation_depth, 0)

    def test_exit_during_configuration_error_does_not_destroy_application(self):
        root = Mock()
        root._zipatch_closing = False
        app = SimpleNamespace(
            root=root, commits=self.controller, config_manager=self.manager,
            _scroll_after_id=None,
        )
        self.manager.accept(self.config["scan"], {"include_date": True})

        def report(*args, **kwargs):
            ZipatchApp.close_application(app)

        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error", side_effect=report),
            patch("app.exit_blocking_popup", return_value=None),
            patch("app.safe_show_info"),
            patch("app.prepare_all_for_exit") as prepare,
        ):
            self.controller.finish()
        root.destroy.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(self.controller.operation_depth, 0)

    def test_favorite_deferred_request_refreshes_view(self):
        parent = Mock()
        controller = Mock()
        controller.submit_values.return_value = SaveResult(SaveStatus.DEFERRED)
        controller.manager.config_data = self.config
        favorite = SimpleNamespace(
            commits=controller, parent=parent, favorite_key="patch_text",
            refresh_popup=Mock(),
        )
        result = FavoriteTextBoxController.submit(favorite, [])
        self.assertTrue(result.request_satisfied)
        self.assertFalse(result.persisted)
        favorite.refresh_popup.assert_called_once()

    def test_favorite_failed_request_does_not_refresh_as_saved(self):
        controller = Mock()
        controller.submit_values.return_value = SaveResult(
            SaveStatus.FAILED, PermissionError("denied"),
        )
        controller.manager.config_data = self.config
        favorite = SimpleNamespace(
            commits=controller, parent=Mock(), favorite_key="patch_text",
            refresh_popup=Mock(),
        )
        FavoriteTextBoxController.submit(favorite, [])
        favorite.refresh_popup.assert_not_called()


class PopupParentLifecycleTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.destroy_root)
        self.errors = []
        self.root.report_callback_exception = self.report_error

    def report_error(self, error_type, error, traceback):
        self.errors.append(error)

    def destroy_root(self):
        try:
            self.root.destroy()
        except TclError:
            pass

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [])

    def test_parent_popup_survives_registered_child(self):
        parent = tk.Toplevel(self.root)
        child = tk.Toplevel(parent)
        unrelated = tk.Entry(self.root)
        register_popup(
            parent, close_on_focus_out=True, focus_on_register=False,
        )
        register_popup(child, focus_on_register=False)

        with patch("ui.window_manager.get_focus_owner", return_value=unrelated):
            validate_temporary_popups(self.root)
        self.assertTrue(parent.winfo_exists())
        self.assertTrue(child.winfo_exists())

        child.destroy()
        with patch("ui.window_manager.get_focus_owner", return_value=unrelated):
            validate_temporary_popups(self.root)
        self.assertFalse(parent.winfo_exists())

    def test_unregistered_child_does_not_leave_stale_registered_record(self):
        parent = tk.Toplevel(self.root)
        child = tk.Toplevel(parent)
        unrelated = tk.Entry(self.root)
        register_popup(
            parent, close_on_focus_out=True, focus_on_register=False,
        )
        register_popup(child, focus_on_register=False)
        unregister_popup(child)
        child.destroy()
        with patch("ui.window_manager.get_focus_owner", return_value=unrelated):
            validate_temporary_popups(self.root)
        self.assertFalse(parent.winfo_exists())


if __name__ == "__main__":
    unittest.main()