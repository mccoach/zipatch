# -*- coding: utf-8 -*-

"""真实Tk控件的坐标、五个动作、刷新及设置草稿测试。

没有可用Tk显示环境时明确跳过，不伪装为界面验证通过。
"""

import tkinter as tk
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.config import ConfigSaveManager, SaveResult, SaveStatus
from core.constants import DEFAULT_CONFIG
from core.text_search import DEFAULT_BOUNDARY_CODEPOINTS
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.global_settings import GlobalSettingsDialog
from ui.text_editor import replace_text_keep_undo


class SearchUiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"没有可用Tk显示环境：{error}")
        self.root.withdraw()
        self.directory = TemporaryDirectory()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.manager = ConfigSaveManager(
            self.config,
            path=Path(self.directory.name) / "config.json",
            needs_write=True,
        )
        self.commits = ConfigCommitController(self.root, self.manager)
        self.frame = tk.Frame(self.root)
        self.frame.pack()
        self.text = create_managed_text_box(
            self.frame, "测试", "Test test TEST",
            commits=self.commits, enable_favorites=False,
            preserve_trailing_newlines=True,
        )
        self.controller = self.text._text_editor_controller
        self.controller.show_search_bar()

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "directory"):
            self.directory.cleanup()

    def set_body(self, value):
        replace_text_keep_undo(self.text, value)
        self.root.update_idletasks()
        self.controller.refresh_matches()

    def test_five_actions_and_loop(self):
        controller = self.controller
        controller.find_var.set("test")
        self.assertEqual(controller.match_status.get(), "0 / 3")
        self.assertEqual(len(controller.action_buttons), 5)
        controller.action_buttons[1].invoke()
        self.assertEqual(controller.match_status.get(), "1 / 3")
        controller.action_buttons[1].invoke()
        controller.action_buttons[1].invoke()
        self.assertEqual(controller.match_status.get(), "3 / 3")
        controller.action_buttons[1].invoke()
        self.assertEqual(controller.match_status.get(), "1 / 3")
        controller.action_buttons[0].invoke()
        self.assertEqual(controller.match_status.get(), "3 / 3")
        controller.replace_var.set("new")
        controller.action_buttons[2].invoke()
        self.assertEqual(self.text.get("1.0", "end-1c"), "Test test new")
        controller.action_buttons[3].invoke()
        self.assertEqual(self.text.get("1.0", "end-1c"), "new new new")
        self.assertEqual(controller.match_status.get(), "无匹配")
        controller.action_buttons[4].invoke()
        self.assertEqual(controller.search_bar.winfo_manager(), "")
        self.assertTrue(self.text.bind("<Control-f>"))
        self.assertTrue(self.text.bind("<Control-h>"))
        self.assertTrue(controller.find_entry.bind("<Return>"))
        self.assertTrue(controller.find_entry.bind("<Shift-Return>"))
        self.assertTrue(controller.find_entry.bind("<Escape>"))

    def test_case_toggle_and_mode_retention(self):
        controller = self.controller
        controller.find_var.set("Test")
        controller.find_next()
        controller.mode_buttons[0][0].invoke()
        self.assertEqual(controller.match_status.get(), "1 / 1")
        self.assertEqual(controller.mode_buttons[0][0].cget("relief"), "sunken")
        self.assertIn("已开启", controller.mode_buttons[0][0]._tooltip.text)
        controller.hide_search_bar()
        controller.show_search_bar()
        self.assertTrue(controller.case_sensitive.get())

    def test_no_result_empty_invalid_and_edit_refresh(self):
        controller = self.controller
        controller.find_var.set("missing")
        before = self.text.index("insert")
        controller.find_next()
        controller.find_prev()
        controller.replace_var.set("bad")
        controller.replace_current()
        controller.replace_all()
        self.assertEqual(self.text.index("insert"), before)
        self.assertEqual(self.text.get("1.0", "end-1c"), "Test test TEST")
        self.assertEqual(controller.match_status.get(), "无匹配")
        controller.find_var.set("")
        self.assertEqual(controller.match_status.get(), "—")
        controller.wildcard.set(True)
        controller.find_var.set("***")
        self.assertEqual(controller.match_status.get(), "查询不完整")
        controller.find_var.set("new")
        self.set_body("new new")
        self.assertEqual(controller.match_status.get(), "0 / 2")

    def test_unicode_tk_positions_and_literal_replace(self):
        self.set_body("😀 file😀.py\nfile1.py")
        controller = self.controller
        controller.wildcard.set(True)
        controller.find_var.set("file?.py")
        controller.find_next()
        start, end = controller.current_match_range("file?.py")
        self.assertEqual(self.text.get(start, end), "file😀.py")
        self.assertEqual(self.text.get("1.0", start), "😀 ")
        controller.replace_var.set("new*.py")
        controller.replace_all()
        self.assertEqual(
            self.text.get("1.0", "end-1c"), "😀 new*.py\nnew*.py",
        )

    def test_query_relocation(self):
        self.set_body("Test test TEST")
        controller = self.controller
        controller.find_var.set("test")
        controller.find_next()
        controller.find_next()
        controller.case_sensitive.set(True)
        controller.refresh_matches(relocate=True)
        self.assertEqual(controller._current_offsets(), (5, 9))
        controller.find_var.set("TEST")
        self.assertEqual(controller._current_offsets(), (10, 14))

    def test_settings_cancel_reset_save_and_refresh(self):
        self.set_body("my_test")
        controller = self.controller
        controller.whole_word.set(True)
        controller.find_var.set("test")
        self.assertEqual(controller.match_status.get(), "无匹配")
        original = list(self.config["search"]["boundary_codepoints"])
        draft = GlobalSettingsDialog(self.root, self.commits)
        draft.load_draft([ord("_")])
        self.assertEqual(self.config["search"]["boundary_codepoints"], original)
        draft.close()
        self.assertEqual(self.config["search"]["boundary_codepoints"], original)

        draft = GlobalSettingsDialog(self.root, self.commits)
        draft.load_draft([ord("_")])
        self.assertTrue(draft.save())
        self.assertEqual(self.config["search"]["boundary_codepoints"], [ord("_")])
        self.assertEqual(controller.match_status.get(), "0 / 1")

        draft = GlobalSettingsDialog(self.root, self.commits)
        draft.restore_defaults()
        self.assertEqual(self.config["search"]["boundary_codepoints"], [ord("_")])
        self.assertEqual(
            draft.values()["boundary_codepoints"],
            list(DEFAULT_BOUNDARY_CODEPOINTS),
        )
        self.assertTrue(draft.save())
        self.assertEqual(controller.match_status.get(), "无匹配")

    def test_settings_home_and_layered_popup_close(self):
        from ui.global_settings import open_global_settings
        from ui.window_manager import close_top_popup

        original = list(self.config["search"]["boundary_codepoints"])
        home = open_global_settings(self.root, self.commits)
        self.assertEqual(home.dialog.title(), "全局设置")
        self.assertIsNone(home.boundary_dialog)
        draft = home.open_boundaries()
        self.assertEqual(draft.dialog.title(), "全词匹配边界")
        self.assertIs(home.open_boundaries(), draft)
        draft.load_draft([ord("_")])
        self.assertTrue(draft.dialog.bind("<Escape>"))
        self.assertTrue(home.dialog.bind("<Escape>"))
        self.assertTrue(close_top_popup(self.root))
        self.assertTrue(draft.closed)
        self.assertFalse(home.closed)
        self.assertIs(self.root.grab_current(), home.dialog)
        self.assertEqual(
            self.config["search"]["boundary_codepoints"], original,
        )
        self.assertTrue(close_top_popup(self.root))
        self.assertTrue(home.closed)

    def test_boundary_mousewheel_scrolls_from_child_widget(self):
        # 合成鼠标事件需要已显示的目标；隐藏主窗口不能证明事件送达。
        self.root.deiconify()
        self.root.update()
        draft = GlobalSettingsDialog(self.root, self.commits)
        try:
            draft._add_extra(0x200B)
            draft.visible_text.delete("1.0", "end")
            draft.visible_text.insert(
                "1.0", "\n".join("测试字符" for _ in range(80)),
            )
            self.root.update()
            checkbox = draft.extra_frame.winfo_children()[0]
            self.assertTrue(checkbox.winfo_ismapped())
            self.assertTrue(draft.visible_text.winfo_ismapped())
            # 显示和布局完成后固定外层范围，不依赖机器字体及屏幕高度。
            draft.canvas.configure(scrollregion=(0, 0, 700, 4000))
            draft.canvas.yview_moveto(0)
            draft.visible_text.yview_moveto(0)
            outer_before = draft.canvas.yview()
            inner_before = draft.visible_text.yview()

            checkbox.event_generate("<MouseWheel>", delta=-120)
            self.assertGreater(draft.canvas.yview()[0], outer_before[0])
            self.assertEqual(draft.visible_text.yview(), inner_before)
            checkbox.event_generate("<MouseWheel>", delta=120)
            self.assertAlmostEqual(draft.canvas.yview()[0], outer_before[0])
            self.assertEqual(draft.visible_text.yview(), inner_before)
        finally:
            draft.close()

    def test_visible_character_mousewheel_does_not_scroll_outer_page(self):
        self.root.deiconify()
        self.root.update()
        draft = GlobalSettingsDialog(self.root, self.commits)
        try:
            draft.visible_text.delete("1.0", "end")
            draft.visible_text.insert(
                "1.0", "\n".join("测试字符" for _ in range(80)),
            )
            self.root.update()
            self.assertTrue(draft.visible_text.winfo_ismapped())
            draft.canvas.configure(scrollregion=(0, 0, 700, 4000))
            draft.canvas.yview_moveto(0)
            draft.visible_text.yview_moveto(0)
            outer_before = draft.canvas.yview()
            inner_before = draft.visible_text.yview()

            draft.visible_text.event_generate("<MouseWheel>", delta=-120)
            self.assertGreater(draft.visible_text.yview()[0], inner_before[0])
            self.assertEqual(draft.canvas.yview(), outer_before)
            draft.visible_text.event_generate("<MouseWheel>", delta=120)
            self.assertAlmostEqual(
                draft.visible_text.yview()[0], inner_before[0],
            )
            self.assertEqual(draft.canvas.yview(), outer_before)

            # 鼠标位于内层滚动条时仍只滚动内层。
            inner = draft.visible_text.master
            scrollbar = next(
                child for child in inner.winfo_children()
                if isinstance(child, tk.Scrollbar)
                and str(child.cget("orient")) == "vertical"
            )
            scrollbar.event_generate("<MouseWheel>", delta=-120)
            self.assertGreater(draft.visible_text.yview()[0], inner_before[0])
            self.assertEqual(draft.canvas.yview(), outer_before)

            # 内层到达底部或顶部，不把剩余滚轮动作交给外层。
            draft.visible_text.yview_moveto(1)
            inner_bottom = draft.visible_text.yview()
            draft.visible_text.event_generate("<MouseWheel>", delta=-120)
            self.assertEqual(draft.visible_text.yview(), inner_bottom)
            self.assertEqual(draft.canvas.yview(), outer_before)
            draft.visible_text.yview_moveto(0)
            inner_top = draft.visible_text.yview()
            draft.visible_text.event_generate("<MouseWheel>", delta=120)
            self.assertEqual(draft.visible_text.yview(), inner_top)
            self.assertEqual(draft.canvas.yview(), outer_before)
        finally:
            draft.close()

    def test_settings_failure_keeps_draft_and_effective_boundary(self):
        draft = GlobalSettingsDialog(self.root, self.commits)
        original = list(self.config["search"]["boundary_codepoints"])
        draft.load_draft([ord("_")])
        with (
            patch.object(
                self.manager, "save",
                return_value=SaveResult(
                    SaveStatus.FAILED, error=OSError("测试保存故障"),
                ),
            ),
            patch("ui.config_editing.safe_show_error"),
        ):
            self.assertFalse(draft.save())
        self.assertFalse(draft.closed)
        self.assertEqual(self.config["search"]["boundary_codepoints"], original)
        self.assertEqual(draft.values()["boundary_codepoints"], [ord("_")])
        draft.close()

    def test_ordinary_edit_refresh_does_not_move_cursor(self):
        controller = self.controller
        controller.find_var.set("test")
        controller.find_next()
        self.text.mark_set("insert", "1.0")
        self.text.insert("insert", "x")
        expected_cursor = self.text.index("insert")
        with patch.object(self.text, "see", wraps=self.text.see) as see:
            self.root.update_idletasks()
        self.assertEqual(self.text.index("insert"), expected_cursor)
        see.assert_not_called()
        self.assertEqual(len(controller.matches.ranges), 3)
        self.assertEqual(controller.match_status.get(), "0 / 3")

    def test_hidden_search_defers_snapshot_and_keeps_cursor(self):
        controller = self.controller
        controller.find_var.set("test")
        controller.find_next()
        controller.hide_search_bar()
        self.text.mark_set("insert", "1.0")
        with patch.object(
            controller, "_read_search_source",
            wraps=controller._read_search_source,
        ) as read:
            self.text.insert("insert", "prefix ")
            expected_cursor = self.text.index("insert")
            self.root.update_idletasks()
            read.assert_not_called()
            self.assertEqual(self.text.index("insert"), expected_cursor)
            self.assertTrue(controller._source_dirty)
            controller.show_search_bar()
            self.assertEqual(read.call_count, 1)
        self.assertEqual(len(controller.matches.ranges), 3)

    def test_empty_query_does_not_read_search_snapshot(self):
        controller = self.controller
        controller.find_var.set("")
        with patch.object(
            controller, "_read_search_source",
            wraps=controller._read_search_source,
        ) as read:
            self.text.insert("end-1c", " new")
            self.root.update_idletasks()
            controller.refresh_matches()
            read.assert_not_called()
        self.assertEqual(controller.match_status.get(), "—")
        self.assertTrue(controller._source_dirty)

    def test_readonly_search_refresh_and_no_replace_actions(self):
        text = create_managed_text_box(
            self.frame, "只读测试", "Test test",
            commits=self.commits, readonly=True, enable_favorites=False,
        )
        controller = text._text_editor_controller
        controller.show_search_bar()
        controller.find_var.set("test")
        controller.find_next()
        self.assertEqual(len(controller.action_buttons), 3)
        self.assertIsNone(controller.replace_entry)
        controller.replace_var.set("bad")
        controller.replace_current()
        controller.replace_all()
        self.assertEqual(text.get("1.0", "end-1c"), "Test test")
        text.configure(state="normal")
        text.insert("end-1c", " TEST")
        text.configure(state="disabled")
        expected_cursor = text.index("insert")
        with patch.object(text, "see", wraps=text.see) as see:
            self.root.update_idletasks()
        self.assertEqual(text.index("insert"), expected_cursor)
        see.assert_not_called()
        self.assertEqual(len(controller.matches.ranges), 3)
        self.assertEqual(str(text.cget("state")), "disabled")

    def test_list_proxy_search_replacement_and_undo(self):
        from ui.list_text_editing import ListTextEditing

        self.set_body("aa\n\n")
        editing = ListTextEditing(
            self.text, self.commits, lambda content: None,
        )
        controller = self.controller
        controller.find_var.set("aa")
        controller.replace_var.set('"bb"')
        callback_errors = []

        def report_error(error_type, error, traceback):
            callback_errors.append((error_type, error))

        with patch.object(self.root, "report_callback_exception", report_error):
            controller.replace_all()
            self.root.update_idletasks()
            self.assertEqual(editing.complete(), "bb\n\n")
            self.assertEqual(controller.match_status.get(), "无匹配")
            controller.undo()
            self.root.update_idletasks()
            self.assertEqual(editing.complete(), "aa\n\n")
            self.assertEqual(len(controller.matches.ranges), 1)
            controller.redo()
            self.root.update_idletasks()
            self.assertEqual(editing.complete(), "bb\n\n")
            self.text.destroy()
        self.assertEqual(callback_errors, [])

    def test_manual_cursor_movement_overrides_previous_match(self):
        self.set_body("test A test B test C test")
        controller = self.controller
        controller.find_var.set("test")
        self.text.mark_set("insert", "1.0")
        controller.find_next()
        self.assertEqual(controller._current_offsets(), (0, 4))
        controller.find_next()
        self.assertEqual(controller._current_offsets(), (7, 11))

        self.text.mark_set("insert", "1.18")
        self.assertIsNone(controller.current_index)
        self.assertEqual(controller.match_status.get(), "0 / 4")
        self.assertEqual(len(self.text.tag_ranges("search_current")), 0)
        self.assertEqual(len(self.text.tag_ranges("search_match")), 8)
        with patch.object(
            controller, "_read_search_source",
            wraps=controller._read_search_source,
        ) as read:
            controller.find_next()
            read.assert_not_called()
        self.assertEqual(controller._current_offsets(), (21, 25))
        self.assertEqual(controller.match_status.get(), "4 / 4")

        self.text.mark_set("insert", "1.13")
        controller.find_prev()
        self.assertEqual(controller._current_offsets(), (7, 11))
        controller.find_prev()
        self.assertEqual(controller._current_offsets(), (0, 4))

    def test_live_cursor_navigation_retains_existing_wrap(self):
        self.set_body("test A test B test")
        controller = self.controller
        controller.find_var.set("test")
        self.text.mark_set("insert", "end-1c")
        controller.find_next()
        self.assertEqual(controller._current_offsets(), (0, 4))
        self.text.mark_set("insert", "1.0")
        controller.find_prev()
        self.assertEqual(controller._current_offsets(), (14, 18))
        controller.find_next()
        self.assertEqual(controller._current_offsets(), (0, 4))

    def test_selection_uses_actual_insert_position(self):
        self.set_body("test A test B test C test")
        controller = self.controller
        controller.find_var.set("test")
        self.text.mark_set("insert", "1.0")
        controller.find_next()
        self.text.tag_add("sel", "1.7", "1.18")
        self.text.mark_set("insert", "1.18")
        controller.find_next()
        self.assertEqual(controller._current_offsets(), (21, 25))
        self.text.mark_set("insert", "1.7")
        controller.find_next()
        self.assertEqual(controller._current_offsets(), (7, 11))

    def test_replace_current_after_manual_cursor_move(self):
        self.set_body("test A test B test")
        controller = self.controller
        controller.find_var.set("test")
        self.text.mark_set("insert", "1.0")
        controller.find_next()
        self.text.mark_set("insert", "1.12")
        controller.replace_var.set("new")
        controller.replace_current()
        self.assertEqual(
            self.text.get("1.0", "end-1c"), "test A test B new",
        )
        self.assertEqual(len(controller.matches.ranges), 2)

    def test_same_cursor_position_does_not_cancel_match(self):
        controller = self.controller
        controller.find_var.set("test")
        self.text.mark_set("insert", "1.0")
        controller.find_next()
        selected = controller._current_offsets()
        self.text.mark_set("insert", self.text.index("insert"))
        self.assertEqual(controller._current_offsets(), selected)
        self.assertEqual(controller.match_status.get(), "1 / 3")
        self.assertFalse(controller._moving_search_cursor)

    def test_undo_redo_refresh_and_tail_preservation(self):
        self.set_body("aaaa\n\n")
        controller = self.controller
        controller.find_var.set("aa")
        controller.replace_var.set("x")
        controller.replace_all()
        self.assertEqual(self.text.get("1.0", "end-1c"), "xx\n\n")
        controller.undo()
        self.root.update_idletasks()
        self.assertEqual(self.text.get("1.0", "end-1c"), "aaaa\n\n")
        self.assertEqual(len(controller.matches.ranges), 2)
        controller.redo()
        self.root.update_idletasks()
        self.assertEqual(self.text.get("1.0", "end-1c"), "xx\n\n")


if __name__ == "__main__":
    unittest.main()
