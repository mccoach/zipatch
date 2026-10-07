# -*- coding: utf-8 -*-

import sys
import tkinter as tk
from tkinter import messagebox, TclError

from core.constants import THEME
from core.paths import center_window, get_available_renamed_path


def safe_show_info(title, message, parent=None):
    try:
        messagebox.showinfo(title, message, parent=parent)
    except TclError:
        print(f"[INFO] {title}: {message}")


def safe_show_error(title, message, parent=None):
    try:
        messagebox.showerror(title, message, parent=parent)
    except TclError:
        print(f"[ERROR] {title}: {message}", file=sys.stderr)


def safe_ask_yes_no(title, message, parent=None, icon="question"):
    """
    显示系统原生 yes/no 确认弹窗。

    icon 只控制系统原生图标，不改变弹窗类型、按钮或返回值。
    支持 Tk 原生图标：question、info、warning、error。
    """
    try:
        return messagebox.askyesno(
            title,
            message,
            parent=parent,
            icon=icon,
        )
    except TclError:
        print(f"[CONFIRM] {title}: {message}")
        return False


def safe_ask_risk_confirm(title, message, parent=None, danger=False):
    """
    执行前风险确认弹窗。

    保持系统原生 yes/no 弹窗及原有返回值不变，只按风险状态区分图标：
    - 普通可执行确认：info；
    - 高风险可执行确认：warning。
    """
    return safe_ask_yes_no(
        title,
        message,
        parent=parent,
        icon="warning" if danger else "info",
    )


def resolve_output_file_conflict(output_file, force_overwrite, parent=None):
    """
    输出文件冲突处理：
    - force_overwrite=True：直接覆盖；
    - 文件不存在：直接使用；
    - 否则弹窗让用户选择：取消 / 自动改名 / 覆盖。
    """
    import os

    if force_overwrite or not os.path.exists(output_file):
        return output_file

    dialog = tk.Toplevel(parent)
    dialog.title("成果文件已存在")
    dialog.configure(bg=THEME["bg"])
    center_window(dialog, 580, 200)
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)

    result = {"action": "cancel"}

    tk.Label(
        dialog,
        text="成果文件已存在，请选择处理方式：",
        bg=THEME["bg"],
        fg=THEME["fg"],
        font=THEME["font_title"]
    ).pack(fill="x", padx=20, pady=(20, 6))

    tk.Label(
        dialog,
        text=output_file,
        bg=THEME["bg"],
        fg=THEME["fg_dim"],
        font=THEME["font_main"],
        anchor="w",
        justify="left",
        wraplength=540
    ).pack(fill="x", padx=20, pady=(0, 16))

    # 延迟导入，避免 core 与 ui 循环依赖
    from ui.theme import styled_frame, styled_button

    button_row = styled_frame(dialog)
    button_row.pack(fill="x", padx=20, pady=(0, 20))

    def choose(action):
        result["action"] = action
        dialog.destroy()

    styled_button(
        button_row,
        "取消",
        lambda: choose("cancel"),
        width=10
    ).pack(side="right", padx=(6, 0))

    styled_button(
        button_row,
        "自动改名",
        lambda: choose("rename"),
        width=10
    ).pack(side="right", padx=6)

    styled_button(
        button_row,
        "覆盖",
        lambda: choose("overwrite"),
        width=10,
        accent=True
    ).pack(side="right", padx=6)

    from ui.window_manager import register_popup, unregister_popup
    register_popup(dialog, lambda: choose("cancel"), exit_blocker=True)
    parent.wait_window(dialog)
    unregister_popup(dialog)

    if result["action"] == "overwrite":
        return output_file

    if result["action"] == "rename":
        return get_available_renamed_path(output_file)

    return None
