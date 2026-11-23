# -*- coding: utf-8 -*-

"""不同链接访问同一正文文件时，真实Tk只展示一个共同目标。"""

import os
import tempfile
import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core.config import ConfigSaveManager
from core.constants import DEFAULT_CONFIG
from services.patch_file_plan import TEXT_WRITE_OPS
from services.patch_scope import (
    prepare_operation_scope, apply_patch_target_decisions,
)
from ui.config_editing import ConfigCommitController
from ui.patch_link_authorization import select_patch_scope


@unittest.skipUnless(os.name == "nt", "Windows真实链接及Tk授权窗口")
class PatchCommonTargetUiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.body = self.base / "actual.py"
        self.body.write_bytes(b"first second")
        self.aliases = tuple(
            self.project / name for name in ("one.py", "two.py")
        )
        for alias in self.aliases:
            os.symlink(self.body, alias)
        # 本专项明确需要真实显示环境；初始化失败不转成通过或跳过。
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = (
            lambda kind, error, traceback: self.errors.append(error)
        )
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(
            self.config, path=self.base / "config.json", needs_write=True,
        )
        self.commits = ConfigCommitController(self.root, self.manager)
        self.scopes = tuple(
            prepare_operation_scope(
                {
                    "op": "replace_exact",
                    "id": identifier,
                    "path": alias.name,
                    "old": old,
                    "new": old.upper(),
                    "count": "1",
                },
                alias,
                position=lambda expression: self.project / expression,
                text_ops=TEXT_WRITE_OPS,
                enumerate_directories=False,
            )
            for alias, identifier, old in zip(
                self.aliases, ("first001", "second001"), ("first", "second"),
            )
        )

    def tearDown(self):
        try:
            if hasattr(self, "root"):
                self.root.destroy()
            for alias in getattr(self, "aliases", ()):
                if os.path.lexists(alias):
                    os.unlink(alias)
        finally:
            self.temporary.cleanup()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def run_choice(self, action):
        original_wait = self.root.wait_window
        original_open = Path.open
        before = deepcopy(self.config)
        observed = []

        def opened(path, *arguments, **keywords):
            if path == self.body or path in self.aliases:
                raise AssertionError("授权窗口不得读取实际正文或链接正文")
            return original_open(path, *arguments, **keywords)

        def wait(window):
            def perform():
                try:
                    self.assertEqual(len(window._scope_variables), 1)
                    checks = tuple(
                        child for child in self.descendants(window)
                        if isinstance(child, tk.Checkbutton)
                    )
                    self.assertEqual(len(checks), 1)
                    label = str(checks[0].cget("text"))
                    self.assertIn(str(self.body), label)
                    self.assertIn('id="first001"', label)
                    self.assertIn('id="second001"', label)
                    observed.append(True)
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
                    if action == "cancel":
                        buttons["取消任务"].invoke()
                    else:
                        window._scope_variables[0].set(action == "authorize")
                        buttons["按所选继续"].invoke()
                finally:
                    if window.winfo_exists():
                        window.tk.call(window.protocol("WM_DELETE_WINDOW"))

            window.after_idle(perform)
            return original_wait(window)

        with patch("pathlib.Path.open", new=opened), patch.object(
            self.root, "wait_window", side_effect=wait,
        ):
            decision = select_patch_scope(
                self.root, self.commits, self.scopes, self.project,
            )
        self.assertEqual(self.errors, [])
        self.assertEqual(observed, [True])
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(self.config, before)
        self.assertFalse(self.manager.path.exists())
        self.assertEqual(self.body.read_bytes(), b"first second")
        self.assertTrue(all(os.path.lexists(alias) for alias in self.aliases))
        if decision is not None:
            self.assertEqual(
                apply_patch_target_decisions(self.scopes, decision), decision,
            )
        return decision

    def test_authorize_one_displayed_target_retains_both_operations(self):
        self.assertEqual(
            self.run_choice("authorize"), ("authorize", "authorize"),
        )

    def test_uncheck_one_displayed_target_skips_both_operations(self):
        self.assertEqual(self.run_choice("skip"), ("skip", "skip"))

    def test_cancel_task_is_not_common_skip(self):
        self.assertIsNone(self.run_choice("cancel"))


if __name__ == "__main__":
    unittest.main()