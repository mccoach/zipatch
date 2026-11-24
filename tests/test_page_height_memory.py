# -*- coding: utf-8 -*-

import unittest
from unittest.mock import Mock

from app import ZipatchApp
from core.config import deep_merge_config
from core.constants import DEFAULT_CONFIG


class PageHeightMemoryTests(unittest.TestCase):
    def application(self):
        application = Mock()
        application._height_drag_page = None
        application.current_panel.config_key = "merge"
        application.commits.operation_depth = 0
        application.root._zipatch_closed = False
        application.workspace.winfo_height.return_value = 800
        application.workspace.sash_coord.return_value = (0, 560)
        application.config_data = {
            "ui": {"workspace_sash_positions": {"scan": 300000}},
        }

        def accept(target, values):
            changed = any(target.get(key) != value for key, value in values.items())
            target.update(values)
            return changed

        application.config_manager.accept.side_effect = accept
        return application

    def test_only_actual_sash_press_starts_drag(self):
        application = self.application()
        event = Mock(x=1, y=2)
        application.workspace.identify.return_value = ()
        ZipatchApp.begin_height_drag(application, event)
        self.assertIsNone(application._height_drag_page)

        application.workspace.identify.return_value = (0, "sash")
        ZipatchApp.begin_height_drag(application, event)
        self.assertEqual(application._height_drag_page, "merge")

    def test_release_saves_current_page_once_and_preserves_other_page(self):
        application = self.application()
        application._height_drag_page = "merge"
        ZipatchApp.finish_height_drag(application)
        self.assertEqual(
            application.config_data["ui"]["workspace_sash_positions"],
            {"scan": 300000, "merge": 700000},
        )
        application.refresh_layout_protection.assert_called_once_with()
        application.commits.finish.assert_called_once_with(application.root)

        ZipatchApp.finish_height_drag(application)
        application.commits.finish.assert_called_once_with(application.root)

    def test_same_position_does_not_request_another_save(self):
        application = self.application()
        application.config_data["ui"]["workspace_sash_positions"]["merge"] = 700000
        application._height_drag_page = "merge"
        ZipatchApp.finish_height_drag(application)
        application.commits.finish.assert_not_called()

    def test_changed_page_or_business_operation_does_not_save(self):
        application = self.application()
        application._height_drag_page = "scan"
        ZipatchApp.finish_height_drag(application)
        application.config_manager.accept.assert_not_called()

        application._height_drag_page = "merge"
        application.commits.operation_depth = 1
        ZipatchApp.finish_height_drag(application)
        application.config_manager.accept.assert_not_called()

    def test_saved_page_positions_survive_config_reconstruction(self):
        source = {
            "ui": {"workspace_sash_positions": {
                "merge": 700000, "scan": 300000,
            }},
        }
        loaded = deep_merge_config(DEFAULT_CONFIG, source)
        self.assertEqual(
            loaded["ui"]["workspace_sash_positions"],
            {"merge": 700000, "scan": 300000},
        )
        loaded["ui"]["workspace_sash_positions"]["merge"] = 600000
        self.assertEqual(source["ui"]["workspace_sash_positions"]["merge"], 700000)


if __name__ == "__main__":
    unittest.main(verbosity=2)