# -*- coding: utf-8 -*-

"""结果历史只追加最终记录；组合预演展示不重复持久化。"""

import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from core.config import ConfigSaveManager, deep_merge_config
from core.constants import DEFAULT_CONFIG
from panels.patch_panel import PatchPanel
from ui.config_editing import ConfigCommitController


class ReportHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = deep_merge_config(DEFAULT_CONFIG, {})
        self.config["patch"]["last_result_text"] = "OLD HISTORY"
        self.manager = ConfigSaveManager(
            self.config, Path(self.temporary.name) / "config.json",
            needs_write=True,
        )
        self.assertTrue(self.manager.save().persisted)
        self.commits = ConfigCommitController(None, self.manager)

    def panel(self):
        cfg = dict(self.config["patch"])
        cfg.update(project_root="project", patch_text="package")
        preview = SimpleNamespace(
            preview_text="PREVIEW", failed_count=0,
            valid_patch={"operations": [{"id": "one"}]},
        )
        shown = []
        panel = SimpleNamespace(
            cfg=self.config["patch"], root=None, commits=self.commits,
            prepare_parameters=Mock(return_value=cfg),
            display_result=lambda text: shown.append(text) or text,
            set_status=Mock(), log=Mock(),
            run_preview=Mock(return_value=preview),
            result_section=lambda title, text, parameters: title + "\n" + text,
            confirm_apply_execution=Mock(return_value=True),
            failure_message=Mock(return_value="REJECTED"),
        )
        # 直接绑定真实方法，测试与生产共享完整参数契约。
        panel.finish_result = MethodType(PatchPanel.finish_result, panel)
        return panel, preview, shown

    def test_combined_preview_and_execution_keep_prefix_without_duplicate_preview(self):
        panel, preview, shown = self.panel()
        writes = self.manager.metrics["successful_replaces"]

        def apply(accepted):
            self.assertIs(accepted, preview)
            self.assertEqual(
                shown, ["OLD HISTORY\n\nDry Run 预演\nPREVIEW"],
            )
            self.assertEqual(panel.cfg["last_result_text"], "OLD HISTORY")
            self.assertEqual(self.manager.metrics["successful_replaces"], writes)
            return SimpleNamespace(log_text="EXECUTED", backup_root="")

        with (
            patch("panels.patch_panel.apply_patch", side_effect=apply),
            patch("panels.patch_panel.safe_show_info"),
        ):
            PatchPanel._apply(panel)
        expected = (
            "OLD HISTORY\n\nDry Run 预演\nPREVIEW"
            "\n\n执行结果\nEXECUTED"
        )
        self.assertEqual(shown[-1], expected)
        self.assertEqual(panel.cfg["last_result_text"], expected)
        self.assertEqual(expected.count("Dry Run 预演\nPREVIEW"), 1)
        self.assertEqual(self.manager.metrics["successful_replaces"] - writes, 1)
        self.assertTrue(all(value.startswith("OLD HISTORY") for value in shown))

    def test_confirmation_cancel_retains_old_history_and_current_preview(self):
        panel, preview, shown = self.panel()
        panel.confirm_apply_execution.return_value = False
        with patch("panels.patch_panel.apply_patch") as apply:
            PatchPanel._apply(panel)
        apply.assert_not_called()
        expected = "OLD HISTORY\n\nDry Run 预演\nPREVIEW"
        self.assertEqual(panel.cfg["last_result_text"], expected)
        self.assertEqual(shown, [expected, expected])

    def test_execution_failure_retains_history_preview_and_failure_record(self):
        panel, preview, shown = self.panel()
        with (
            patch("panels.patch_panel.apply_patch", side_effect=OSError("FAILED OP")),
            patch("panels.patch_panel.safe_show_error"),
        ):
            PatchPanel._apply(panel)
        result = panel.cfg["last_result_text"]
        self.assertTrue(result.startswith("OLD HISTORY\n\nDry Run 预演\nPREVIEW"))
        self.assertIn("执行失败\nFAILED OP", result)
        self.assertEqual(result.count("Dry Run 预演\nPREVIEW"), 1)
        self.assertEqual(shown[-1], result)

    def test_independent_preview_does_not_clear_history(self):
        panel, preview, shown = self.panel()
        with patch("panels.patch_panel.safe_show_info"):
            PatchPanel._preview(panel)
        expected = "OLD HISTORY\n\nDry Run 预演\nPREVIEW"
        self.assertEqual(shown, [expected])
        self.assertEqual(panel.cfg["last_result_text"], expected)

    def test_preview_failure_appends_without_clearing_existing_record(self):
        panel, preview, shown = self.panel()
        panel.run_preview.side_effect = ValueError("INVALID PACKAGE")
        panel.preview_failure = lambda error, cfg: PatchPanel.preview_failure(
            panel, error, cfg,
        )
        with patch("panels.patch_panel.safe_show_error"):
            PatchPanel._preview(panel)
        result = panel.cfg["last_result_text"]
        self.assertTrue(result.startswith("OLD HISTORY\n\nDry Run 失败\n"))
        self.assertIn("INVALID PACKAGE", result)
        self.assertEqual(shown, [result])

    def test_failed_precondition_does_not_change_display_or_history(self):
        panel, preview, shown = self.panel()
        panel.prepare_parameters.return_value = None
        for entry in (PatchPanel._preview, PatchPanel._apply):
            entry(panel)
        self.assertEqual(shown, [])
        self.assertEqual(panel.cfg["last_result_text"], "OLD HISTORY")
        panel.run_preview.assert_not_called()

    def test_open_backup_from_history_uses_latest_record(self):
        panel = SimpleNamespace(
            last_backup_root="",
            cfg={"last_result_text": (
                "备份目录：OLD BACKUP\n"
                "旧执行记录\n"
                "备份目录：LATEST BACKUP\n"
                "新执行记录\n"
            )},
            root=None,
        )
        with patch("panels.patch_panel.open_path_with_default_app") as opener:
            PatchPanel.open_last_backup_dir(panel)
        opener.assert_called_once_with("LATEST BACKUP", None)

    def test_bound_finish_result_accepts_updates_without_second_business_run(self):
        panel, preview, shown = self.panel()
        updates = {"restore_source_dir": "accepted source"}
        saved = panel.finish_result(
            "FINAL RECORD", updates, outcome="业务已经完成",
        )
        self.assertTrue(saved.persisted)
        self.assertEqual(panel.cfg["restore_source_dir"], "accepted source")
        self.assertEqual(
            panel.cfg["last_result_text"], "OLD HISTORY\n\nFINAL RECORD",
        )
        self.assertEqual(shown, ["OLD HISTORY\n\nFINAL RECORD"])
        panel.run_preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()