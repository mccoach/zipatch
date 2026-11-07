# -*- coding: utf-8 -*-

"""同步业务期间的页面控件锁定；不拥有任务、确认或保存逻辑。"""

import tkinter as tk
from contextlib import contextmanager


@contextmanager
def locked_page(panel):
    """保存并恢复各控件原状态，嵌套锁定不提前解除外层锁。"""
    locked = []

    def lock(parent):
        for widget in parent.winfo_children():
            if isinstance(
                widget,
                (tk.Text, tk.Entry, tk.Button, tk.Checkbutton, tk.Radiobutton),
            ):
                locked.append((widget, str(widget.cget("state"))))
                widget.configure(state="disabled")
            lock(widget)

    try:
        lock(panel)
        yield
    finally:
        if not getattr(panel.root, "_zipatch_closed", False):
            for widget, state in reversed(locked):
                if widget.winfo_exists():
                    widget.configure(state=state)
