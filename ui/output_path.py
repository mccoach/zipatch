# -*- coding: utf-8 -*-

"""完整输出路径输入；选择文件名不发布成果、不取得覆盖授权。"""

from pathlib import Path
from tkinter import filedialog

from core.path_validation import normalize_windows_display_path
from core.paths import get_initial_dir_from_path
from ui.file_dialog_history import (
    file_dialog_initial_directory, remember_file_dialog_directory,
)
from ui.message_dialogs import safe_show_error
from ui.widgets import create_entry_row, open_path_with_default_app


def create_output_path_row(parent, panel):
    variable = panel.output_path
    key = f"entry.{panel.config_key}.output_path.browse"

    def browse():
        current = normalize_windows_display_path(variable.get())
        try:
            fallback = get_initial_dir_from_path(current)
        except (OSError, ValueError):
            fallback = None
        selected = filedialog.asksaveasfilename(
            title="选择输出路径及文件名",
            initialdir=file_dialog_initial_directory(
                panel.commits, key, fallback=fallback,
            ),
            initialfile=Path(current).name if current else "",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            confirmoverwrite=False,
            parent=panel.root,
        )
        if selected:
            # 这里只接受选择位置；覆盖确认仍由正式执行入口统一负责。
            remember_file_dialog_directory(
                panel.commits, key, Path(selected).parent,
            )
        return selected

    def open_output():
        from core.paths import resolve_output_path
        from core.recovery_paths import checked_chain

        try:
            path, chain = checked_chain(
                resolve_output_path(variable.get()), include_observation=True,
            )
            if chain[-1][1] != "file":
                raise ValueError(f"成果普通文件不存在：\n{path}")
        except (OSError, ValueError) as error:
            safe_show_error("打开失败", str(error), panel.root)
            return
        open_path_with_default_app(path, panel.root)

    return create_entry_row(
        parent, "输出路径", variable,
        commits=panel.commits, config=panel.cfg,
        field="output_path", page=panel.config_key,
        history_key=f"{panel.config_key}.output_path",
        value_normalizer=normalize_windows_display_path,
        browse_command=browse, open_command=open_output,
        tooltip_text="填写包含文件名的完整绝对路径；没有扩展名时使用.txt。",
    )