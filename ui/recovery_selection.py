# -*- coding: utf-8 -*-

"""已固定所选恢复范围的执行确认；对象选择在当前状态获取前完成。"""

import tkinter as tk

from core.constants import THEME
from services.patch_service import confirm_backup_restore, invalidate_preview
from ui.dialogs import create_managed_text_box
from ui.message_dialogs import safe_show_error
from ui.theme import styled_button, styled_frame
from ui.window_manager import (
    center_window, register_popup, take_modal_control, unregister_popup,
)


def select_backup_restore(parent, preview, *, keep_source_path, commits):
    dialog = None
    accepted = False
    try:
        dialog = tk.Toplevel(parent)
        dialog.withdraw()
        dialog.title("确认所选范围恢复")
        dialog.configure(bg=THEME["bg"])
        dialog.transient(parent)
        controls = styled_frame(dialog)
        controls.pack(side="bottom", fill="x", padx=16, pady=14)
        body = styled_frame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        notice = (
            "\n\n成功后保留来源路径，不删除备份。"
            if keep_source_path else
            "\n\n只有整份备份完整恢复成功才清空来源；"
            "部分恢复、取消或失败保留来源，不删除备份。"
        )
        create_managed_text_box(
            body, "恢复范围与影响", preview.preview_text + notice,
            height=26, readonly=True, commits=commits,
            enable_favorites=False, enable_clear=False,
            enable_wrap_toggle=False, enable_copy=True,
        )

        def cancel():
            invalidate_preview(preview)
            unregister_popup(dialog)
            dialog.destroy()
            return True

        def confirm():
            nonlocal accepted
            try:
                confirm_backup_restore(preview)
            except ValueError as error:
                safe_show_error("恢复未开始", str(error), dialog)
                return
            accepted = True
            unregister_popup(dialog)
            dialog.destroy()

        cancel_button = styled_button(controls, "取消", cancel, width=12)
        cancel_button.pack(side="right", padx=(8, 0))
        styled_button(
            controls, "确认所选范围恢复", confirm, width=18, danger=True,
        ).pack(side="right")
        center_window(
            dialog, 1000, min(740, dialog.winfo_screenheight() - 100),
        )
        take_modal_control(dialog)
        register_popup(
            dialog, cancel, exit_blocker=True, focus_on_register=False,
        )
        dialog.deiconify()
        cancel_button.focus_set()
        parent.wait_window(dialog)
        return accepted
    finally:
        if not accepted:
            invalidate_preview(preview)
        if dialog is not None:
            unregister_popup(dialog)
            if dialog.winfo_exists():
                dialog.destroy()
