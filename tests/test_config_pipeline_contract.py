# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.config import ConfigSaveManager, SaveStatus, deep_merge_config
from core.constants import DEFAULT_CONFIG
from panels.patch_panel import PatchPanel
from ui.config_editing import ConfigCommitController


class PipelineContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.manager = ConfigSaveManager(
            self.config, Path(self.directory.name) / "config.json",
            needs_write=True,
        )
        self.assertTrue(self.manager.save().persisted)
        self.controller = ConfigCommitController(None, self.manager)

    def test_parameters_are_fixed_after_acceptance_before_save(self):
        events = []

        def accept():
            events.append("accept")
            self.manager.accept(
                self.config["scan"], {"source_folder": "accepted"},
            )
            return True

        editor = SimpleNamespace(
            page="scan", field="source_folder", commit=accept,
        )
        original_save = self.manager.save

        def save(*args, **kwargs):
            events.append("save")
            self.manager.accept(
                self.config["scan"], {"source_folder": "later"},
            )
            return original_save(*args, **kwargs)

        with (
            patch.object(self.controller, "select", return_value=[editor]),
            patch.object(self.manager, "save", side_effect=save),
        ):
            parameters = self.controller.prepare_business(
                "scan", {"source_folder"},
            )
        self.assertEqual(events, ["accept", "save"])
        self.assertEqual(parameters, {"source_folder": "accepted"})
        self.assertEqual(self.config["scan"]["source_folder"], "later")

    def test_failure_returns_no_execution_parameters(self):
        self.manager.accept(
            self.config["scan"], {"source_folder": "accepted"},
        )
        with (
            patch("core.config.os.replace", side_effect=PermissionError("denied")),
            patch("ui.config_editing.safe_show_error"),
        ):
            parameters = self.controller.prepare_business(
                "scan", {"source_folder"},
            )
        self.assertIsNone(parameters)
        self.assertTrue(self.manager.needs_save)

    def test_preparation_excludes_unrelated_fields(self):
        parameters = self.controller.prepare_business(
            "merge", {"output_folder", "output_filename"},
        )
        self.assertEqual(
            set(parameters), {"output_folder", "output_filename"},
        )
        self.assertNotIn("demand_file_list_text", parameters)

    def test_mutable_parameters_do_not_share_configuration_objects(self):
        self.manager.accept(
            self.config["scan"], {"sample_paths": ["first"]},
        )
        parameters = self.controller.prepare_business(
            "scan", {"sample_paths"},
        )
        parameters["sample_paths"].append("second")
        self.assertEqual(self.config["scan"]["sample_paths"], ["first"])

    def test_preview_calculation_does_not_change_panel_state(self):
        cfg = {
            "project_root": "project",
            "patch_mode": "apply",
            "patch_text": "body",
            "allow_delete": False,
            "allow_multi_replace_exact": False,
            "backup_enabled": True,
            "backup_dir": "backup",
        }
        panel = SimpleNamespace()
        result = SimpleNamespace(preview_text="report")
        with patch(
            "panels.patch_panel.preview_patch", return_value=result,
        ) as service:
            self.assertIs(PatchPanel.run_preview(panel, cfg), result)
        self.assertEqual(vars(panel), {})
        service.assert_called_once_with(
            project_root="project", patch_text="body",
            allow_delete=False, allow_multi_replace_exact=False,
            backup_enabled=True, backup_dir="backup",
        )

    def make_apply_panel(self, preview):
        cfg = {
            "project_root": "project",
            "patch_mode": "apply",
            "patch_text": "body",
            "allow_delete": False,
            "allow_multi_replace_exact": False,
            "backup_enabled": True,
            "backup_dir": "backup",
            "open_backup_after_done": False,
        }
        panel = SimpleNamespace(
            root=None,
            commits=self.controller,
            prepare_parameters=Mock(return_value=cfg),
            display_result=Mock(side_effect=lambda text: text),
            set_status=Mock(),
            log=Mock(),
            run_preview=Mock(return_value=preview),
            result_section=Mock(
                side_effect=lambda title, text, parameters: title + "\n" + text,
            ),
            finish_result=Mock(return_value=SimpleNamespace(persisted=True)),
            confirm_apply_execution=Mock(return_value=True),
            failure_message=Mock(return_value="failed"),
        )
        return panel, cfg

    def test_combined_execution_passes_local_preview_without_middle_save(self):
        valid_patch = {"operations": [{"id": "op001"}]}
        preview = SimpleNamespace(
            preview_text="preview report",
            failed_count=0,
            valid_patch=valid_patch,
        )
        panel, cfg = self.make_apply_panel(preview)

        def apply(**kwargs):
            panel.finish_result.assert_not_called()
            self.assertIs(kwargs["patch"], valid_patch)
            self.assertEqual(kwargs["preview_text"], "preview report")
            self.assertEqual(kwargs["project_root"], cfg["project_root"])
            return SimpleNamespace(log_text="completed", backup_root="backup")

        with (
            patch("panels.patch_panel.apply_patch", side_effect=apply),
            patch("panels.patch_panel.safe_show_info"),
        ):
            PatchPanel._apply(panel)
        panel.confirm_apply_execution.assert_called_once_with(cfg, valid_patch)
        panel.finish_result.assert_called_once()
        self.assertNotIn("preview_apply_patch", vars(panel))
        self.assertNotIn("preview_snapshot", vars(panel))

    def test_confirmation_rejection_saves_report_without_execution(self):
        preview = SimpleNamespace(
            preview_text="preview report",
            failed_count=0,
            valid_patch={"operations": [{"id": "op001"}]},
        )
        panel, cfg = self.make_apply_panel(preview)
        panel.confirm_apply_execution.return_value = False
        with patch("panels.patch_panel.apply_patch") as apply:
            PatchPanel._apply(panel)
        apply.assert_not_called()
        panel.finish_result.assert_called_once_with(
            "Dry Run 预演\npreview report",
        )

    def test_precondition_failure_does_not_clear_existing_result(self):
        panel = SimpleNamespace(
            prepare_parameters=Mock(return_value=None),
            display_result=Mock(),
        )
        PatchPanel._apply(panel)
        panel.display_result.assert_not_called()

    def test_result_save_failure_does_not_repeat_business(self):
        preview = SimpleNamespace(
            preview_text="preview report",
            failed_count=0,
            valid_patch={"operations": [{"id": "op001"}]},
        )
        panel, cfg = self.make_apply_panel(preview)
        panel.finish_result.return_value = SimpleNamespace(persisted=False)
        with (
            patch(
                "panels.patch_panel.apply_patch",
                return_value=SimpleNamespace(
                    log_text="completed", backup_root="backup",
                ),
            ) as apply,
            patch("panels.patch_panel.safe_show_info") as show,
        ):
            PatchPanel._apply(panel)
        apply.assert_called_once()
        panel.finish_result.assert_called_once()
        show.assert_not_called()


if __name__ == "__main__":
    unittest.main()