# -*- coding: utf-8 -*-

"""原生消息和风险确认；保持既有按钮、返回值及具体Tcl失败政策。"""

import sys
from tkinter import messagebox, TclError


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
    """icon仅控制原生图标，不改变yes/no按钮或返回值。"""
    try:
        return messagebox.askyesno(
            title, message, parent=parent, icon=icon,
        )
    except TclError:
        print(f"[CONFIRM] {title}: {message}")
        return False


def safe_ask_risk_confirm(title, message, parent=None, danger=False):
    """普通确认使用info，高风险确认使用warning；既有政策保持。"""
    return safe_ask_yes_no(
        title, message, parent=parent,
        icon="warning" if danger else "info",
    )
