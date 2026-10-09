# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core.config import (
    ConfigSaveManager, SaveStatus, deep_merge_config, load_user_config,
)
from core.constants import DEFAULT_CONFIG
from ui.config_editing import ConfigCommitController


class ConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.manager = ConfigSaveManager(self.config, self.path, needs_write=True)
        self.assertTrue(self.manager.save().persisted)

    def test_unchanged_has_no_serialization_or_write(self):
        before = dict(self.manager.metrics)
        self.assertEqual(self.manager.save().status, SaveStatus.UNCHANGED)
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.manager.metrics[key], before[key])

    def test_changed_save_serializes_and_writes_once(self):
        before = dict(self.manager.metrics)
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        self.assertEqual(self.manager.save().status, SaveStatus.SAVED)
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.manager.metrics[key] - before[key], 1)

    def test_repeated_accept_is_noop(self):
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        self.manager.save()
        before = self.manager.metrics["write_attempts"]
        self.assertFalse(self.manager.accept(self.config["scan"], {"source_folder": "new"}))
        self.manager.save()
        self.assertEqual(self.manager.metrics["write_attempts"], before)

    def test_return_to_previous_state_requires_save(self):
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        self.manager.save()
        self.manager.accept(self.config["scan"], {"source_folder": ""})
        self.assertEqual(self.manager.save().status, SaveStatus.SAVED)

    def test_replace_failure_preserves_file_and_snapshot(self):
        original = self.path.read_bytes()
        snapshot = self.manager._saved_snapshot
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        with patch("core.config.os.replace", side_effect=PermissionError("denied")):
            result = self.manager.save()
        self.assertEqual(result.status, SaveStatus.FAILED)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.manager._saved_snapshot, snapshot)
        self.assertTrue(self.manager.needs_save)
        self.assertEqual(self.manager.save().status, SaveStatus.SAVED)

    def test_fsync_failure_preserves_file(self):
        original = self.path.read_bytes()
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        with patch("core.config.os.fsync", side_effect=OSError("failed")):
            self.assertEqual(self.manager.save().status, SaveStatus.FAILED)
        self.assertEqual(self.path.read_bytes(), original)

    def test_temporary_open_failure_preserves_file(self):
        original = self.path.read_bytes()
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        with patch("core.config.Path.open", side_effect=PermissionError("denied")):
            self.assertEqual(self.manager.save().status, SaveStatus.FAILED)
        self.assertEqual(self.path.read_bytes(), original)

    def test_cleanup_failure_does_not_hide_original_error(self):
        original_error = PermissionError("replace denied")
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        with (
            patch("core.config.os.replace", side_effect=original_error),
            patch("core.config.Path.unlink", side_effect=OSError("cleanup failed")),
        ):
            result = self.manager.save()
        self.assertIs(result.error, original_error)
        self.assertIsNotNone(self.manager.last_cleanup_error)

    def test_restoring_saved_state_retains_error_history(self):
        self.manager.accept(self.config["scan"], {"source_folder": "new"})
        with patch("core.config.os.replace", side_effect=PermissionError("denied")):
            self.manager.save()
        error = self.manager.last_save_error
        self.manager.accept(self.config["scan"], {"source_folder": ""})
        self.assertEqual(self.manager.save().status, SaveStatus.UNCHANGED)
        self.assertFalse(self.manager.needs_save)
        self.assertIs(self.manager.last_save_error, error)

    def test_required_creation_failure_remains_retryable(self):
        path = Path(self.directory.name) / "missing.json"
        manager = ConfigSaveManager(deep_merge_config(DEFAULT_CONFIG, {}), path, needs_write=True)
        with patch("core.config.os.replace", side_effect=PermissionError("denied")):
            self.assertEqual(manager.save().status, SaveStatus.FAILED)
        self.assertIsNone(manager._saved_snapshot)
        self.assertTrue(manager.required_write)
        self.assertTrue(manager.save().persisted)

    def test_nested_batch_merges_save_requests(self):
        before = self.manager.metrics["successful_replaces"]
        with self.manager.batch():
            self.manager.accept(self.config["scan"], {"include_date": True})
            self.assertEqual(self.manager.save().status, SaveStatus.QUEUED)
            with self.manager.batch():
                self.manager.accept(self.config["scan"], {"include_size": True})
                self.manager.save()
        self.assertEqual(self.manager.metrics["successful_replaces"] - before, 1)
        self.assertEqual(self.manager._batch_depth, 0)

    def test_failed_batch_blocks_later_save_until_explicit_recovery(self):
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            with self.manager.batch():
                self.manager.accept(self.config["scan"], {"include_date": True})
                self.manager.save()
                raise ValueError("interrupted")
        self.assertEqual(self.manager._batch_depth, 0)
        self.assertFalse(self.manager._save_requested)
        self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(RuntimeError):
            self.manager.save()
        with self.assertRaises(RuntimeError):
            self.manager.accept(self.config["scan"], {"include_size": True})
        self.assertTrue(self.manager.recover_aborted_batch())
        self.assertFalse(self.config["scan"]["include_date"])
        self.manager.accept(self.config["scan"], {"include_size": True})
        self.manager.save()
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(disk["scan"]["include_date"])
        self.assertTrue(disk["scan"]["include_size"])

    def test_caught_nested_failure_cannot_save_partial_batch(self):
        with self.assertRaises(RuntimeError):
            with self.manager.batch():
                self.manager.accept(self.config["scan"], {"include_date": True})
                try:
                    with self.manager.batch():
                        self.manager.accept(self.config["scan"], {"include_size": True})
                        raise ValueError("nested failure")
                except ValueError:
                    pass
        self.assertEqual(self.manager._batch_depth, 0)
        self.assertTrue(self.manager.recover_aborted_batch())
        self.assertFalse(self.config["scan"]["include_date"])
        self.assertFalse(self.config["scan"]["include_size"])

    def test_recovery_preserves_changes_pending_before_batch(self):
        self.manager.accept(self.config["scan"], {"source_folder": "before"})
        with self.assertRaises(ValueError):
            with self.manager.batch():
                self.manager.accept(self.config["scan"], {"source_folder": "partial"})
                raise ValueError("failed")
        self.manager.recover_aborted_batch()
        self.assertEqual(self.config["scan"]["source_folder"], "before")
        self.assertTrue(self.manager.needs_save)
        self.assertTrue(self.manager.save().persisted)

    def test_session_only_change_has_explicit_deferred_result(self):
        before = dict(self.manager.metrics)
        self.manager.accept(self.config, {"active_mode": "patch"}, persist=False)
        result = self.manager.save()
        self.assertEqual(result.status, SaveStatus.DEFERRED)
        self.assertFalse(result.persisted)
        self.assertTrue(result.request_satisfied)
        self.assertEqual(
            self.manager.metrics["json_generations"], before["json_generations"],
        )
        self.assertTrue(self.manager.save(include_session=True).persisted)

    def test_session_change_joins_next_real_save(self):
        self.manager.accept(self.config, {"active_mode": "patch"}, persist=False)
        self.manager.accept(self.config["scan"], {"include_date": True})
        self.assertTrue(self.manager.save().persisted)
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["active_mode"], "patch")
        self.assertFalse(self.manager._session_pending)

    def test_defaults_and_instances_are_independent(self):
        original = deepcopy(DEFAULT_CONFIG)
        other = deep_merge_config(DEFAULT_CONFIG, {})
        self.config["favorites"]["patch_text"].append(
            {"name": "test", "content": "body"},
        )
        self.config["text_wrap"]["patch.patch_text"] = True
        self.assertEqual(DEFAULT_CONFIG, original)
        self.assertEqual(other["favorites"]["patch_text"], [])
        self.assertFalse(other["text_wrap"]["patch.patch_text"])

    def test_valid_load_preserves_open_history_and_favorites(self):
        self.manager.accept(
            self.config["entry_history"], {"custom.history": ["first", "second"]},
        )
        self.manager.accept(
            self.config["favorites"],
            {"patch_text": [{"name": "sample", "content": "body"}]},
        )
        self.manager.save()
        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(loaded["entry_history"]["custom.history"], ["first", "second"])
        self.assertEqual(
            loaded["favorites"]["patch_text"],
            [{"name": "sample", "content": "body"}],
        )

    def test_missing_creates_defaults_but_corrupt_load_preserves_file(self):
        missing = Path(self.directory.name) / "missing.json"
        _, needed = load_user_config(missing)
        self.assertTrue(needed)
        missing.write_text("{broken", encoding="utf-8")
        original = missing.read_bytes()
        with self.assertRaises(ValueError):
            load_user_config(missing)
        self.assertEqual(missing.read_bytes(), original)

    def test_long_result_is_saved_and_loaded_without_truncation(self):
        result = "【完整结果开始】\n" + "结果正文" * 150_000 + "\n【完整结果结束】\n"
        self.assertGreater(len(result), 500_000)
        self.manager.accept(
            self.config["patch"], {"last_result_text": result},
        )
        before = dict(self.manager.metrics)
        self.assertEqual(self.manager.save().status, SaveStatus.SAVED)
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.manager.metrics[key] - before[key], 1)

        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["patch"]["last_result_text"], result)
        original_file = self.path.read_bytes()
        loaded, needs_write = load_user_config(self.path)
        self.assertFalse(needs_write)
        self.assertEqual(loaded["patch"]["last_result_text"], result)
        self.assertEqual(self.path.read_bytes(), original_file)

        self.assertEqual(self.manager.save().status, SaveStatus.UNCHANGED)
        for key in ("json_generations", "write_attempts", "successful_replaces"):
            self.assertEqual(self.manager.metrics[key] - before[key], 1)

    def test_draft_failure_restores_fields_before_error_message(self):
        controller = ConfigCommitController(None, self.manager)
        previous = self.config["merge"]["preamble_text"]
        observed = []
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch(
                "ui.config_editing.safe_show_error",
                side_effect=lambda *args, **kwargs: observed.append(
                    self.config["merge"]["preamble_text"]
                ),
            ),
        ):
            self.assertFalse(controller.accept_draft(
                self.config["merge"], {"preamble_text": "abandoned"}, None,
            ))
        self.assertEqual(observed, [previous])
        self.manager.accept(self.config["scan"], {"include_date": True})
        self.manager.save()
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["merge"]["preamble_text"], previous)

    def test_draft_exception_restores_fields_and_propagates(self):
        controller = ConfigCommitController(None, self.manager)
        previous = self.config["merge"]["preamble_text"]
        with patch.object(self.manager, "save", side_effect=RuntimeError("defect")):
            with self.assertRaises(RuntimeError):
                controller.accept_draft(
                    self.config["merge"], {"preamble_text": "candidate"}, None,
                )
        self.assertEqual(self.config["merge"]["preamble_text"], previous)

    def test_operation_depth_cleans_up_after_exception(self):
        controller = ConfigCommitController(None, self.manager)
        with self.assertRaises(ValueError):
            with controller.operation():
                with controller.operation():
                    self.assertEqual(controller.operation_depth, 2)
                    raise ValueError("failed")
        self.assertEqual(controller.operation_depth, 0)


if __name__ == "__main__":
    unittest.main()