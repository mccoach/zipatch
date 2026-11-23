# -*- coding: utf-8 -*-

"""扫描当前明确范围的真实窗口授权，不写配置、不访问来源。"""

import tkinter as tk

from core.constants import THEME
from ui.dialogs import create_managed_text_box
from ui.theme import styled_button, styled_frame
from ui.window_manager import (
    center_window, register_popup, take_modal_control, unregister_popup,
)


def select_scan_scope(parent, commits, title, description):
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    accepted = False
    controls = styled_frame(dialog)
    controls.pack(side="bottom", fill="x", padx=14, pady=12)
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=14, pady=(12, 0))
    create_managed_text_box(
        body, "本次明确扫描范围",
        description
        + "\n\n授权仅属于这里已经展示的固定范围，不授权未来任务。"
        "\n关闭或Esc取消；扫描不改变来源，不读取引用目标正文。",
        height=24, readonly=True, mono=True, commits=commits,
        enable_favorites=False, enable_clear=False, enable_wrap_toggle=False,
    )

    def finish(value):
        nonlocal accepted
        accepted = value
        unregister_popup(dialog)
        dialog.destroy()
        return True

    cancel = styled_button(
        controls, "取消扫描", lambda: finish(False), width=12,
    )
    cancel.pack(side="right", padx=(8, 0))
    styled_button(
        controls, "授权上述明确范围", lambda: finish(True),
        width=22, danger=True,
    ).pack(side="right")

    try:
        center_window(
            dialog, 1000, min(740, dialog.winfo_screenheight() - 100),
        )
        take_modal_control(dialog)
        register_popup(
            dialog, lambda: finish(False),
            exit_blocker=True, focus_on_register=False,
        )
        dialog.deiconify()
        cancel.focus_set()
        parent.wait_window(dialog)
        return accepted
    finally:
        unregister_popup(dialog)
        if dialog.winfo_exists():
            dialog.destroy()


def make_scan_authorizer(parent, commits):
    def decide(title, description):
        return select_scan_scope(parent, commits, title, description)

    return decide
