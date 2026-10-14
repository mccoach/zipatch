# -*- coding: utf-8 -*-

import tempfile
import time
import tkinter as tk
import unittest
from concurrent.futures import Future
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app import ZipatchApp
from core.config import ConfigSaveManager, deep_merge_config, load_user_config
from core.constants import DEFAULT_CONFIG
from services.demand_rules import (
    DemandCheckCancelled, DemandMatch, build_demand_selection,
    inspect_demand_rule, inspect_demand_rules,
)
from services.merge_service import build_demand_merge_plan, merge_demand_files
from ui.demand_editor import CheckRequest


class DemandFinalRuleAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("docs/a.md", "docs/sub/b.md", "other/unrelated.txt"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(name, encoding="utf-8")
        self.cancel = Event()

    def inspect(self, expression):
        return inspect_demand_rule(expression, str(self.root), self.cancel)

    def test_fixed_prefix_does_not_enumerate_unrelated_source_root(self):
        import services.demand_rules as module
        original = module.os.scandir
        opened = []

        def enumerate_directory(path):
            opened.append(Path(path))
            if Path(path) == self.root:
                raise PermissionError("unrelated root enumeration denied")
            return original(path)

        with patch("services.demand_rules.os.scandir", side_effect=enumerate_directory):
            result = self.inspect("docs\\*.md")
        self.assertTrue(result.completed, result.issue)
        self.assertEqual(opened, [self.root / "docs"])

    def test_multiple_double_stars_enumerate_each_directory_once(self):
        import services.demand_rules as module
        original = module.os.scandir
        opened = []

        def enumerate_directory(path):
            opened.append(str(path))
            return original(path)

        with patch("services.demand_rules.os.scandir", side_effect=enumerate_directory):
            result = self.inspect("docs\\**\\**\\*.md")
        self.assertTrue(result.completed, result.issue)
        self.assertEqual(len(result.files), 2)
        self.assertEqual(len(opened), len(set(opened)))

    def test_source_base_is_checked_once_per_task(self):
        import services.demand_rules as module
        with patch(
            "services.demand_rules._source_directory",
            wraps=module._source_directory,
        ) as source:
            results = inspect_demand_rules(
                ((1, "docs\\a.md"), (2, "docs\\sub\\b.md")),
                str(self.root), self.cancel,
            )
        self.assertTrue(all(result.completed for result in results.values()))
        source.assert_called_once()

    def test_cancel_during_enumeration_closes_resource_and_returns_no_partial(self):
        class Enumerator:
            def __enter__(owner):
                return owner

            def __exit__(owner, *args):
                owner.closed = True

            def __next__(owner):
                self.cancel.set()
                raise StopIteration

        enumerator = Enumerator()
        enumerator.closed = False
        with patch("services.demand_rules.os.scandir", return_value=enumerator):
            with self.assertRaises(DemandCheckCancelled):
                self.inspect("docs\\*.md")
        self.assertTrue(enumerator.closed)

    def test_attribute_failure_rejects_partial_selection(self):
        class Entry:
            name = "bad.md"
            path = "bad.md"

            def stat(owner, **kwargs):
                raise PermissionError("attributes denied")

        class Enumerator:
            def __enter__(owner):
                owner.iterator = iter((Entry(),))
                return owner

            def __exit__(owner, *args):
                owner.closed = True

            def __next__(owner):
                return next(owner.iterator)

        enumerator = Enumerator()
        enumerator.closed = False
        with patch("services.demand_rules.os.scandir", return_value=enumerator):
            result = self.inspect("docs\\*.md")
        self.assertFalse(result.completed)
        self.assertEqual(result.files, ())
        self.assertIn("attributes denied", result.issue)
        self.assertTrue(enumerator.closed)

    def test_finished_red_is_saveable_but_not_successful(self):
        result = build_demand_selection([
            DemandMatch(issue="指定文件不存在。"),
        ])
        self.assertTrue(result.complete)
        self.assertTrue(result.has_errors)
        self.assertFalse(result.duplicates[0].determined)

    def test_unfinished_result_is_not_complete(self):
        self.assertFalse(
            build_demand_selection([DemandMatch()]).complete
        )

    def test_trailing_separator_never_selects_a_file(self):
        self.assertFalse(self.inspect("docs\\a.md\\").completed)
        self.assertFalse(self.inspect("docs\\*.md\\").completed)

    def test_plan_preserves_selected_path_expression(self):
        path = str(self.root / "docs" / ".." / "docs" / "a.md")
        self.assertEqual(build_demand_merge_plan((path,))[0]["path"], path)

    def test_real_merge_consumes_selection_order_and_duplicate_removal(self):
        first = self.inspect("docs\\sub\\b.md")
        second = self.inspect("docs\\**\\*.md")
        selection = build_demand_selection([first, second])
        self.assertEqual(selection.duplicates[1].duplicates, 1)
        output = self.root / "merged.txt"
        result = merge_demand_files(
            selection.files, output, "", "", "HEADER", "FOOTER",
        )
        text = output.read_text(encoding="utf-8")
        self.assertEqual(result.total_files, 2)
        self.assertLess(
            text.index(str(self.root / "docs/sub/b.md")),
            text.index(str(self.root / "docs/a.md")),
        )


class DemandFinalGuiAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.addCleanup(self.cleanup_root)
        self.errors = []
        self.root.report_callback_exception = (
            lambda kind, error, traceback: self.errors.append(error)
        )
        self.source = Path(self.directory.name)
        (self.source / "a.md").write_text("A", encoding="utf-8")
        (self.source / "b.md").write_text("B", encoding="utf-8")
        self.path = self.source / "config.json"
        config = deep_merge_config(DEFAULT_CONFIG, {})
        config["merge"]["source_folder"] = str(self.source)
        config["merge"]["demand_file_list_text"] = "a.md\nb.md"
        manager = ConfigSaveManager(config, self.path, needs_write=True)
        self.assertTrue(manager.save().persisted)
        with patch("app.raise_and_focus"):
            self.app = ZipatchApp(self.root, config_path=self.path)
        self.panel = self.app.current_panel
        self.session = self.panel.open_demand_merge_dialog()
        self.wait_until(lambda: all(
            rule.match is not None for rule in self.session.rules
        ))

    def cleanup_root(self):
        if hasattr(self, "session"):
            self.session.destroy()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [])

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "测试等待期限内任务未完成")
        self.root.update()

    def replace_rules(self, content):
        text = self.session.rule_text
        text.delete("1.0", "end")
        text.insert("1.0", content)
        self.root.update()

    def test_move_reuses_all_unchanged_matches(self):
        previous = {rule.raw: rule for rule in self.session.rules}
        self.replace_rules("b.md\na.md")
        self.assertIs(self.session.rules[0], previous["b.md"])
        self.assertIs(self.session.rules[1], previous["a.md"])
        self.assertIsNone(self.session.pending)

    def test_duplicate_order_updates_without_rescanning(self):
        self.replace_rules("a.md\na.md")
        self.wait_until(lambda: all(
            rule.match is not None for rule in self.session.rules
        ))
        selection = self.session.render()
        self.assertEqual(selection.duplicates[1].duplicates, 1)
        self.assertTrue(self.session.rule_text.tag_ranges("demand_warning"))

    def test_final_start_reads_each_draft_once(self):
        before = self.app.commits.metrics["text_reads"]
        self.assertTrue(self.session.start_final("save"))
        self.assertEqual(self.app.commits.metrics["text_reads"] - before, 2)
        self.session.cancel_final()
        self.wait_until(lambda: not self.session.busy)

    def test_save_preserves_trailing_empty_rule_lines(self):
        self.replace_rules('"a.md"\n\n')
        self.assertTrue(self.session.start_final("save"))
        self.wait_until(lambda: not self.session.busy)
        loaded, _ = load_user_config(self.path)
        self.assertEqual(loaded["merge"]["demand_file_list_text"], "a.md\n\n")
        self.assertEqual(
            self.session.rule_text.get("1.0", "end-1c"), "a.md\n\n",
        )
        self.assertTrue(self.session.prepare_close())

    def test_final_rechecks_all_rules_after_external_deletion(self):
        (self.source / "a.md").unlink()
        with (
            patch("ui.demand_editor.safe_show_error"),
            patch.object(self.panel, "execute_demand_merge") as execute,
        ):
            self.assertTrue(self.session.start_final("execute"))
            self.wait_until(lambda: not self.session.busy)
        execute.assert_not_called()
        self.assertTrue(self.session.rule_text.tag_ranges("demand_error"))

    def test_final_cancel_before_start_never_saves(self):
        original = self.path.read_bytes()
        self.replace_rules("b.md")
        self.assertTrue(self.session.start_final("save"))
        self.assertTrue(self.session.cancel_final())
        self.assertFalse(self.session.busy)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.app.commits.operation_depth, 0)

    def test_late_cancelled_result_cannot_replace_current_match(self):
        previous = self.session.rules[0].match
        future = Future()
        future.set_result({
            self.session.rules[0].identifier: DemandMatch(issue="STALE"),
        })
        cancel = Event()
        cancel.set()
        self.session.running = (
            CheckRequest(self.session.version, self.session.source, (), 0),
            cancel, future,
        )
        self.session.poll()
        self.assertIs(self.session.rules[0].match, previous)
        self.assertNotIn("STALE", self.session.result_text.get("1.0", "end-1c"))

    def test_explicit_poll_replaces_pending_callback_and_destroy_cancels_it(self):
        def callback_ids():
            return set(self.root.tk.splitlist(
                self.root.tk.call("after", "info")
            ))

        previous = self.session._poll_id
        self.assertIn(previous, callback_ids())
        self.session.poll()
        current = self.session._poll_id
        self.assertNotEqual(previous, current)
        self.assertNotIn(previous, callback_ids())
        self.assertIn(current, callback_ids())

        self.session.poll()
        final = self.session._poll_id
        self.assertNotIn(current, callback_ids())
        self.assertIn(final, callback_ids())
        self.session.destroy()
        self.assertNotIn(final, callback_ids())
        self.assertIsNone(self.session._poll_id)
        self.root.update()

    def test_locked_shortcuts_do_not_modify_rule_text(self):
        self.assertTrue(self.session.start_final("save"))
        text = self.session.rule_text
        original = text.get("1.0", "end-1c")
        tool = text._text_editor_controller
        tool.find_var.set("a.md")
        tool.replace_var.set("b.md")
        tool.undo()
        tool.redo()
        tool.replace_all()
        tool.replace_current()
        self.assertEqual(text.get("1.0", "end-1c"), original)
        self.assertFalse(self.session.start_final("execute"))
        self.session.cancel_final()
        self.wait_until(lambda: not self.session.busy)

    def test_save_failure_keeps_previous_config_and_unlocked_draft(self):
        original = self.path.read_bytes()
        self.replace_rules("b.md")
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.assertTrue(self.session.start_final("save"))
            self.wait_until(lambda: not self.session.busy)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.panel.cfg["demand_file_list_text"], "a.md\nb.md")
        self.assertEqual(str(self.session.rule_text.cget("state")), "normal")
        self.assertEqual(self.app.commits.operation_depth, 0)

    def test_exit_preparation_save_does_not_destroy_draft(self):
        self.replace_rules("b.md")
        self.root._zipatch_closing = True
        try:
            with patch("ui.demand_editor.messagebox.askyesnocancel", return_value=True):
                self.assertFalse(self.session.prepare_close())
        finally:
            self.root._zipatch_closing = False
        self.wait_until(lambda: not self.session.busy)
        self.assertTrue(self.session.dialog.winfo_exists())
        self.assertTrue(self.session.prepare_close())

    def test_direct_destroy_disposes_session(self):
        self.session.dialog.destroy()
        self.assertTrue(self.session._disposed)
        self.assertIsNone(self.session.pending)
        self.assertIsNone(self.session._poll_id)
        self.assertEqual(self.session._source_bindings, [])
        self.assertEqual(self.app.commits.operation_depth, 0)


if __name__ == "__main__":
    unittest.main()