# -*- coding: utf-8 -*-

"""真实Tk勾选窗口：全选、局部选择、全不选、取消及模态释放。"""

import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from ui.config_editing import ConfigCommitController
from ui.patch_link_authorization import (
    make_patch_scope_authorizer, select_patch_scope,
)


class PatchLinkAuthorizationUiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"没有可用Tk显示环境：{error}")
        self.root.withdraw()
        self.directory = TemporaryDirectory()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(
            self.config, path=Path(self.directory.name) / "config.json",
            needs_write=True,
        )
        self.commits = ConfigCommitController(self.root, self.manager)
        self.errors = []
        self.root.report_callback_exception = (
            lambda error_type, error, traceback: self.errors.append(error)
        )
        self.scopes = tuple(
            SimpleNamespace(
                operation={"id": identifier, "op": "delete_file", "path": identifier},
                fixed_skip=identifier == "third",
                accesses=(), observations=(),
            )
            for identifier in ("first", "second", "third")
        )

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "directory"):
            self.directory.cleanup()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def run_window(self, action):
        wait = self.root.wait_window
        observed = {}

        def perform_wait(window):
            def perform():
                controls = next(
                    child for child in window.winfo_children()
                    if child.winfo_manager() == "pack"
                    and child.pack_info().get("side") == "bottom"
                )
                buttons = {
                    str(child.cget("text")): child
                    for child in controls.winfo_children()
                    if isinstance(child, tk.Button)
                }
                observed["buttons"] = set(buttons)
                observed["grab"] = self.root.grab_current()
                checks = tuple(
                    child for child in self.descendants(window)
                    if isinstance(child, tk.Checkbutton)
                )
                observed["check_count"] = len(checks)
                observed["defaults"] = tuple(
                    variable.get() for variable in window._scope_variables
                )
                if action == "close":
                    window.tk.call(window.protocol("WM_DELETE_WINDOW"))
                elif action == "escape":
                    self.assertTrue(window.bind("<Escape>"))
                    window.tk.call("event", "generate", window._w, "<Escape>")
                elif action == "selected":
                    buttons["全不选"].invoke()
                    window._scope_variables[1].set(True)
                    buttons["按所选继续"].invoke()
                elif action == "none":
                    buttons["全不选"].invoke()
                    buttons["按所选继续"].invoke()
                elif action == "all":
                    buttons["全不选"].invoke()
                    buttons["全选"].invoke()
                    buttons["按所选继续"].invoke()
                else:
                    buttons[action].invoke()

            self.root.after_idle(perform)
            return wait(window)

        before = deepcopy(self.config)
        with patch(
            "ui.patch_link_authorization.format_package_scope",
            return_value="first完整范围\nsecond不同范围\nthird固定跳过",
        ), patch(
            "ui.patch_link_authorization.patch_scope_group",
            return_value="文件软链接",
        ), patch.object(self.root, "wait_window", side_effect=perform_wait):
            result = select_patch_scope(
                self.root, self.commits, self.scopes, Path(self.directory.name),
            )
        self.assertEqual(self.errors, [])
        self.assertEqual(observed["buttons"], {
            "全选", "全不选", "查看详情", "取消任务", "按所选继续",
        })
        self.assertEqual(observed["check_count"], 3)
        self.assertEqual(observed["defaults"], (True, True, False))
        self.assertIsNotNone(observed["grab"])
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(self.config, before)
        self.assertFalse(self.manager.path.exists())
        self.assertFalse(any(
            isinstance(child, tk.Toplevel) for child in self.root.winfo_children()
        ))
        return result

    def test_real_window_authorizes_all_displayed_non_skipped_operations(self):
        self.assertEqual(self.run_window("all"), ("authorize", "authorize", "skip"))

    def test_real_window_selected_operations_prune_other_scopes(self):
        self.assertEqual(self.run_window("selected"), ("skip", "authorize", "skip"))

    def test_all_unselected_is_not_cancel(self):
        self.assertEqual(self.run_window("none"), ("skip", "skip", "skip"))

    def test_real_window_cancel_returns_no_decision(self):
        self.assertIsNone(self.run_window("取消任务"))

    def test_title_close_cancels_and_releases_modal_control(self):
        self.assertIsNone(self.run_window("close"))

    def test_factory_consumes_one_complete_package(self):
        authorizer = make_patch_scope_authorizer(self.root, self.commits)
        root = Path(self.directory.name)
        with patch(
            "ui.patch_link_authorization.select_patch_scope",
            return_value=("authorize", "skip", "skip"),
        ) as selector:
            self.assertEqual(authorizer(self.scopes, root), ("authorize", "skip", "skip"))
        selector.assert_called_once_with(self.root, self.commits, self.scopes, root)

    def test_new_package_requires_new_decision(self):
        authorizer = make_patch_scope_authorizer(self.root, self.commits)
        root = Path(self.directory.name)
        with patch(
            "ui.patch_link_authorization.select_patch_scope",
            side_effect=(("authorize", "authorize", "skip"), None),
        ) as selector:
            self.assertIsNotNone(authorizer(self.scopes, root))
            self.assertIsNone(authorizer(self.scopes, root))
        self.assertEqual(selector.call_count, 2)


if __name__ == "__main__":
    unittest.main()