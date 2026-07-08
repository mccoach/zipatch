# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog

from core.constants import THEME
from core.paths import (
    get_initial_dir_from_path,
    open_path_with_default_app,
    build_output_path,
)
from core.message_utils import safe_show_error
from ui.theme import styled_frame, styled_entry, styled_button
from ui.entry_history import EntryHistoryPlugin, save_entry_history


def create_entry_row(
    parent,
    label_text,
    text_var,
    browse_command=None,
    open_command=None,
    label_width=10,
    config_data=None,
    history_key=None,
    save_config=None,
    enable_history=True,
):
    """
    创建单行输入框行。

    默认策略：
    - 单行输入框默认启用历史插件；
    - 如果没有传 config_data/history_key，则自动不启用；
    - 不激活多行文本框的查找/替换/收藏等功能；
    - 新参数都有默认值，旧调用不需要改也能正常运行。
    """
    row = styled_frame(parent, bg=THEME["bg_panel"])
    row.pack(fill="x", pady=4)

    tk.Label(
        row,
        text=label_text,
        width=label_width,
        anchor="w",
        bg=THEME["bg_panel"],
        fg=THEME["fg_label"],
        font=THEME["font_main"],
    ).pack(side="left", padx=(0, 8))

    entry_frame, entry = styled_entry(row, text_var)
    entry_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))

    if enable_history and config_data is not None and history_key:
        EntryHistoryPlugin(
            parent=parent,
            entry=entry,
            text_var=text_var,
            config_data=config_data,
            history_key=history_key,
            save_config=save_config,
            enabled=True,
        )

    if browse_command:
        def browse_and_save():
            browse_command()

            if enable_history and config_data is not None and history_key:
                save_entry_history(
                    config_data,
                    history_key,
                    text_var.get(),
                    save_config,
                )

        styled_button(row, "浏览", browse_and_save, width=6).pack(
            side="left",
            padx=(0, 4),
        )

    if open_command:
        styled_button(row, "打开", open_command, width=6).pack(side="left")

    return entry


def browse_folder(var, title="选择文件夹"):
    initial_dir = get_initial_dir_from_path(var.get())
    path = filedialog.askdirectory(initialdir=initial_dir, title=title)

    if path:
        var.set(path)


def browse_open_file(
    var,
    title="选择文件",
    filetypes=None,
):
    current_path = var.get().strip()
    initial_dir = get_initial_dir_from_path(current_path)
    initial_file = current_path.split("/")[-1] if current_path else None

    if filetypes is None:
        filetypes = [
            ("Text Documents", "*.txt"),
            ("All Files", "*.*"),
        ]

    path = filedialog.askopenfilename(
        title=title,
        initialdir=initial_dir,
        initialfile=initial_file,
        filetypes=filetypes,
    )

    if path:
        var.set(path)


def open_output_file(output_folder_var, output_filename_var, parent):
    output_folder = output_folder_var.get().strip()
    output_filename = output_filename_var.get().strip()

    if not output_folder or not output_filename:
        safe_show_error("打开失败", "输出文件夹或输出文件名为空。", parent=parent)
        return

    output_path = build_output_path(output_folder, output_filename)

    import os

    if not os.path.isfile(output_path):
        safe_show_error("打开失败", f"成果文件不存在：\n{output_path}", parent=parent)
        return

    open_path_with_default_app(output_path, parent=parent)


def bind_autosave(config, var_to_cfg_map, save_config):
    """
    通用自动保存：
    用户修改 StringVar/BooleanVar 后，立即写回配置并保存。
    """

    def _save(*_):
        for var, key in var_to_cfg_map:
            config[key] = var.get()
        save_config()

    for var, _ in var_to_cfg_map:
        var.trace_add("write", _save)


def make_checkbutton(parent, text, variable, bg=None):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=bg or THEME["bg_panel"],
        fg=THEME["fg_label"],
        activebackground=bg or THEME["bg_panel"],
        activeforeground=THEME["fg"],
        selectcolor=THEME["bg_input"],
        font=THEME["font_main"],
    )


def make_radiobutton(parent, text, variable, value, bg=None):
    return tk.Radiobutton(
        parent,
        text=text,
        variable=variable,
        value=value,
        bg=bg or THEME["bg_panel"],
        fg=THEME["fg_label"],
        activebackground=bg or THEME["bg_panel"],
        activeforeground=THEME["fg"],
        selectcolor=THEME["bg_input"],
        font=THEME["font_main"],
    )
