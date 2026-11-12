# -*- coding: utf-8 -*-

"""草稿应用、保存、取消及名单检查取消的明确生命周期契约。"""

import unittest
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ui.dialogs import TextDraftSession
from ui.demand_editor import DemandDraftSession


class DraftCommits:
    def __init__(self):
        self.operation_depth = 0
        self.accept_draft = Mock(return_value=True)

    @contextmanager
    def operation(self):
        self.operation_depth += 1
        try:
            yield
        finally:
            self.operation_depth -= 1

    def read_text(self, widget):
        return widget.get("1.0", "end-1c")


class DraftActionTests(unittest.TestCase):
    def make_session(self):
        dialog = Mock()
        widget = Mock()
        widget.get.return_value = "original"
        commits = DraftCommits()
        session = TextDraftSession(
            dialog, commits, {"text": "original"}, {"text": widget},
        )
        session.destroy = Mock()
        return session, commits, widget

    def test_apply_saves_and_updates_baseline_without_closing(self):
        session, commits, widget = self.make_session()
        widget.get.return_value = "applied"
        self.assertTrue(session.apply())
        commits.accept_draft.assert_called_once_with(
            session.config, {"text": "applied"}, session.dialog,
        )
        self.assertEqual(session.baseline, {"text": "applied"})
        self.assertTrue(session.saved)
        session.destroy.assert_not_called()
        self.assertEqual(commits.operation_depth, 0)

    def test_save_closes_only_after_success(self):
        session, commits, widget = self.make_session()
        widget.get.return_value = "saved"
        self.assertTrue(session.save_and_close())
        self.assertEqual(session.baseline, {"text": "saved"})
        session.destroy.assert_called_once_with()
        self.assertEqual(commits.operation_depth, 0)

    def test_failed_apply_and_save_keep_window_and_original_baseline(self):
        for action in ("apply", "save_and_close"):
            with self.subTest(action=action):
                session, commits, widget = self.make_session()
                widget.get.return_value = "draft"
                commits.accept_draft.return_value = False
                self.assertFalse(getattr(session, action)())
                self.assertEqual(session.baseline, {"text": "original"})
                self.assertFalse(session.saved)
                session.destroy.assert_not_called()
                self.assertEqual(commits.operation_depth, 0)
                self.assertFalse(session._close_in_progress)

    def test_cancel_does_not_accept_unsaved_draft(self):
        session, commits, widget = self.make_session()
        widget.get.return_value = "discarded"
        self.assertTrue(session.cancel())
        commits.accept_draft.assert_not_called()
        session.destroy.assert_called_once_with()

    def test_cancel_after_apply_does_not_revert_applied_baseline(self):
        session, commits, widget = self.make_session()
        widget.get.return_value = "applied"
        self.assertTrue(session.apply())
        widget.get.return_value = "later draft"
        self.assertTrue(session.cancel())
        self.assertEqual(session.baseline, {"text": "applied"})
        self.assertEqual(commits.accept_draft.call_count, 1)
        session.destroy.assert_called_once_with()

    def test_actions_refuse_reentry_during_commit(self):
        for action in ("apply", "save_and_close", "cancel"):
            with self.subTest(action=action):
                session, commits, _ = self.make_session()
                commits.operation_depth = 1
                self.assertFalse(getattr(session, action)())
                commits.accept_draft.assert_not_called()
                session.destroy.assert_not_called()

    def test_unhandled_defect_preserves_context_and_releases_operation(self):
        session, commits, _ = self.make_session()
        defect = RuntimeError("explicit test defect")
        commits.accept_draft.side_effect = defect
        with self.assertRaises(RuntimeError) as caught:
            session.save_and_close()
        self.assertIs(caught.exception, defect)
        self.assertEqual(commits.operation_depth, 0)
        self.assertFalse(session._close_in_progress)
        session.destroy.assert_not_called()


class DemandActionTests(unittest.TestCase):
    def make_session(self, *, busy=False, finished=False):
        session = DemandDraftSession.__new__(DemandDraftSession)
        session._destroyed = False
        session._close_in_progress = False
        session._final_check_finished = finished
        session._operation_scope = object() if busy else None
        session.commits = SimpleNamespace(operation_depth=int(busy))
        session.pending = object()
        cancel = Event()
        session.running = (object(), cancel, Mock())
        session.release_final = Mock()
        session.destroy = Mock()
        return session, cancel

    def test_apply_and_save_select_distinct_final_actions(self):
        session, _ = self.make_session()
        session.start_final = Mock(return_value=True)
        self.assertTrue(session.apply())
        self.assertTrue(session.save_and_close())
        self.assertEqual(
            [call.args for call in session.start_final.call_args_list],
            [("apply",), ("save",)],
        )

    def test_cancel_closes_during_automatic_check_without_accepting(self):
        session, cancel = self.make_session()
        self.assertTrue(session.cancel())
        self.assertTrue(cancel.is_set())
        self.assertIsNone(session.pending)
        session.release_final.assert_not_called()
        session.destroy.assert_called_once_with()

    def test_cancel_closes_during_final_check_and_releases_lock(self):
        session, cancel = self.make_session(busy=True)
        self.assertTrue(session.cancel())
        self.assertTrue(cancel.is_set())
        self.assertIsNone(session.pending)
        session.release_final.assert_called_once_with()
        session.destroy.assert_called_once_with()

    def test_cancel_refuses_to_interrupt_configuration_commit(self):
        session, cancel = self.make_session(busy=True, finished=True)
        self.assertFalse(session.cancel())
        self.assertFalse(cancel.is_set())
        session.release_final.assert_not_called()
        session.destroy.assert_not_called()

    def test_cancel_refuses_unrelated_active_operation(self):
        session, cancel = self.make_session()
        session.commits.operation_depth = 1
        self.assertFalse(session.cancel())
        self.assertFalse(cancel.is_set())
        session.destroy.assert_not_called()

    def make_finished_session(self):
        session = DemandDraftSession.__new__(DemandDraftSession)
        session._operation_scope = object()
        session._final_check_finished = False
        session._final_values = {"demand_file_list_text": "README.md"}
        session.cancel_button = None
        session.rules = [SimpleNamespace(raw="README.md")]
        session.rule_text = Mock()
        session.status = Mock()
        session.dialog = Mock()
        session._writing_text = False
        session.render = Mock()
        session.destroy = Mock()

        def release():
            session._operation_scope = None
            session._final_values = None
            session._final_check_finished = False

        session.release_final = Mock(side_effect=release)
        selection = SimpleNamespace(complete=True, has_errors=False)
        return session, selection

    def test_successful_final_apply_stays_and_save_closes(self):
        for action, closes in (("apply", False), ("save", True)):
            with self.subTest(action=action):
                session, selection = self.make_finished_session()
                with patch.object(
                    TextDraftSession, "accept", return_value=True,
                ) as accept:
                    session.finish_final(action, selection)
                accept.assert_called_once_with(
                    {"demand_file_list_text": "README.md"},
                )
                self.assertEqual(session.destroy.call_count, int(closes))
                session.release_final.assert_called_once_with()
                self.assertFalse(session.busy)

    def test_failed_final_save_does_not_close(self):
        session, selection = self.make_finished_session()
        with patch.object(
            TextDraftSession, "accept", return_value=False,
        ):
            session.finish_final("save", selection)
        session.destroy.assert_not_called()
        session.release_final.assert_called_once_with()
        self.assertFalse(session.busy)

    def test_incomplete_final_check_does_not_accept_or_close(self):
        session, selection = self.make_finished_session()
        selection.complete = False
        with patch.object(TextDraftSession, "accept") as accept:
            session.finish_final("save", selection)
        accept.assert_not_called()
        session.destroy.assert_not_called()
        session.release_final.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
