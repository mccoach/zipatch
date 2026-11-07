# -*- coding: utf-8 -*-

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from core.constants import THEME
from core.paths import (
    build_output_path, get_available_renamed_path, get_initial_dir_from_path,
)
from ui.message_dialogs import safe_show_error
from ui.window_manager import center_window, register_popup, unregister_popup
from ui.entry_history import EntryHistoryPlugin
from ui.theme import styled_button, styled_entry, styled_frame


class Tooltip:
    """只负责显示，不参与配置提交。"""

    def __init__(self, widget, text, delay_ms=450, wraplength=420):
        self.widget = widget
        self.text = text or ""
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self.after_id = None
        self.tip_window = None
        if self.text:
            widget.bind("<Enter>", self.schedule_show, add="+")
            widget.bind("<Leave>", self.hide, add="+")
            widget.bind("<ButtonPress>", self.hide, add="+")
            widget.bind("<Destroy>", self.on_destroy, add="+")

    def schedule_show(self, event=None):
        self.hide()
        self.after_id = self.widget.after(self.delay_ms, self.show)

    def show(self):
        self.after_id = None
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.overrideredirect(True)
        self.tip_window.geometry(
            f"+{self.widget.winfo_rootx() + 18}"
            f"+{self.widget.winfo_rooty() + self.widget.winfo_height() + 8}"
        )
        tk.Label(
            self.tip_window, text=self.text, justify="left", bg=THEME["bg_input"],
            fg=THEME["fg"], padx=8, pady=6, wraplength=self.wraplength,
            font=THEME["font_main"],
        ).pack()

    def hide(self, event=None):
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None

    def on_destroy(self, event=None):
        if event.widget is self.widget:
            self.hide()


def add_tooltip(widget, text):
    widget._tooltip = Tooltip(widget, text)
    return widget


def create_entry_row(
    parent, label_text, text_var, *, commits, config, field, page,
    browse_command=None, open_command=None, open_fields=None,
    label_width=10, history_key=None, tooltip_text=None, value_normalizer=None,
):
    row = styled_frame(parent, bg=THEME["bg_panel"])
    row.pack(fill="x", pady=4)
    label = tk.Label(
        row, text=label_text, width=label_width, anchor="w",
        bg=THEME["bg_panel"], fg=THEME["fg_label"], font=THEME["font_main"],
    )
    label.pack(side="left", padx=(0, 8))
    add_tooltip(label, tooltip_text)
    frame, entry = styled_entry(row, text_var)
    frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
    entry._row_frame = row
    binding = commits.register(
        entry, config, field, page, variable=text_var,
        normalizer=value_normalizer or str.strip, history_key=history_key,
    )

    if history_key:
        entry._history_plugin = EntryHistoryPlugin(
            parent, entry, text_var, binding, commits, history_key,
        )

    if browse_command:
        def browse():
            # 只抑制本字段在本选择流程中的失焦接受，不暂停全局保存。
            with commits.operation(), binding.suspend_focus_commit():
                selected = browse_command()
                if selected:
                    text_var.set(selected)
            commits.submit_editors([binding], parent=entry.winfo_toplevel())

        styled_button(row, "浏览", browse, width=6).pack(side="left", padx=(0, 4))

    if open_command:
        def open_selected_path():
            bindings = commits.select(page, open_fields or {field})
            commits.submit_editors(bindings, parent=entry.winfo_toplevel())
            # 只读打开在保存失败时仍可继续，失败已明确提示。
            open_command()

        styled_button(row, "打开", open_selected_path, width=6).pack(side="left")
    return entry


def browse_folder(var, title="选择文件夹", initial_dir=None, parent=None):
    initial_dir = initial_dir or get_initial_dir_from_path(var.get())
    return filedialog.askdirectory(
        initialdir=initial_dir, title=title, parent=parent,
    )


def browse_open_file(var, title="选择文件", filetypes=None, parent=None):
    current = var.get().strip()
    return filedialog.askopenfilename(
        title=title,
        initialdir=get_initial_dir_from_path(current),
        initialfile=Path(current).name if current else None,
        filetypes=filetypes or [("Text Documents", "*.txt"), ("All Files", "*.*")],
        parent=parent,
    )


def open_output_file(output_folder_var, output_filename_var, parent):
    folder = output_folder_var.get().strip()
    name = output_filename_var.get().strip()
    if not folder or not name:
        safe_show_error("打开失败", "输出文件夹或输出文件名为空。", parent)
        return
    from core.recovery_paths import checked_chain
    try:
        path, chain = checked_chain(
            build_output_path(folder, name), include_observation=True,
        )
        if chain[-1][1] != "file":
            safe_show_error("打开失败", f"成果普通文件不存在：\n{path}", parent)
            return
    except (OSError, ValueError) as error:
        safe_show_error("打开失败", str(error), parent)
        return
    open_path_with_default_app(path, parent)


def make_checkbutton(
    parent, text, variable, *, commits, config, field,
    command=None, bg=None, tooltip_text=None,
):
    def accept_option():
        previous = config[field]
        completed = False
        try:
            if command:
                command()
            completed = True
        finally:
            if not completed:
                variable.set(previous)
        commits.manager.accept(config, {field: variable.get()})
        commits.finish(parent.winfo_toplevel())

    widget = tk.Checkbutton(
        parent, text=text, variable=variable, command=accept_option,
        bg=bg or THEME["bg_panel"], fg=THEME["fg_label"],
        activebackground=bg or THEME["bg_panel"], activeforeground=THEME["fg"],
        selectcolor=THEME["bg_input"], font=THEME["font_main"],
    )
    return add_tooltip(widget, tooltip_text)


def make_radiobutton(
    parent, text, variable, value, *, commits, config, field,
    command=None, bg=None, tooltip_text=None, danger=False,
):
    def accept_option():
        previous = config[field]
        completed = False
        try:
            if command:
                command()
            completed = True
        finally:
            if not completed:
                variable.set(previous)
        commits.manager.accept(config, {field: variable.get()})
        commits.finish(parent.winfo_toplevel())

    widget = tk.Radiobutton(
        parent, text=text, variable=variable, value=value, command=accept_option,
        bg=bg or THEME["bg_panel"],
        fg=THEME["danger"] if danger else THEME["fg_label"],
        activebackground=bg or THEME["bg_panel"], activeforeground=THEME["fg"],
        selectcolor=THEME["danger_bg"] if danger else THEME["bg_input"],
        font=THEME["font_title"] if danger else THEME["font_main"],
    )
    return add_tooltip(widget, tooltip_text)

def open_path_with_default_app(path, parent=None):
    """外部打开前检查原路径和普通对象，不resolve后追踪链接目标。

    只交付本次检查过的路径，不承诺控制外部程序内部访问。
    .lnk和.url自身仍按普通文件交给系统，不新增用途限制。
    """
    if not path:
        safe_show_error("打开失败", "路径为空。", parent)
        return
    from core.recovery_paths import checked_chain
    try:
        absolute, chain = checked_chain(path, include_observation=True)
        if chain[-1][1] == "absent":
            safe_show_error("打开失败", f"路径不存在：\n{absolute}", parent)
            return
        if sys.platform == "win32":
            os.startfile(str(absolute))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(absolute)], check=True)
        else:
            subprocess.run(["xdg-open", str(absolute)], check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        safe_show_error(
            "打开失败", f"无法自动打开：\n{path}\n\n错误：{error}", parent,
        )


def resolve_output_file_conflict(output_file, force_overwrite, parent=None):
    """原链及普通成果政策先于强制覆盖或成果冲突交互。"""
    from core.recovery_paths import checked_chain
    target, chain = checked_chain(output_file, include_observation=True)
    kind = chain[-1][1]
    if kind not in ("absent", "file"):
        raise IsADirectoryError(f"成果目标不是普通文件：{target}")
    output_file = str(target)
    if force_overwrite or kind == "absent":
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
        dialog, text="成果文件已存在，请选择处理方式：",
        bg=THEME["bg"], fg=THEME["fg"], font=THEME["font_title"],
    ).pack(fill="x", padx=20, pady=(20, 6))
    tk.Label(
        dialog, text=output_file, bg=THEME["bg"], fg=THEME["fg_dim"],
        font=THEME["font_main"], anchor="w", justify="left", wraplength=540,
    ).pack(fill="x", padx=20, pady=(0, 16))
    button_row = styled_frame(dialog)
    button_row.pack(fill="x", padx=20, pady=(0, 20))

    def choose(action):
        result["action"] = action
        dialog.destroy()

    styled_button(
        button_row, "取消", lambda: choose("cancel"), width=10,
    ).pack(side="right", padx=(6, 0))
    styled_button(
        button_row, "自动改名", lambda: choose("rename"), width=10,
    ).pack(side="right", padx=6)
    styled_button(
        button_row, "覆盖", lambda: choose("overwrite"), width=10, accent=True,
    ).pack(side="right", padx=6)
    register_popup(dialog, lambda: choose("cancel"), exit_blocker=True)
    parent.wait_window(dialog)
    unregister_popup(dialog)

    if result["action"] == "overwrite":
        return output_file
    if result["action"] == "rename":
        return get_available_renamed_path(output_file)
    return None
