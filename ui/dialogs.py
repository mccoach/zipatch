# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog

from core.constants import THEME
from core.message_utils import safe_show_error, safe_show_info
from core.text_io import get_text_value, replace_text_keep_undo, read_text_with_fallback_encodings
from core.paths import center_window
from ui.theme import styled_frame, styled_button, styled_text_with_scrollbars
from ui.text_editor import TextEditorController
from ui.favorites import FavoriteTextBoxController, title_bar_button
from ui.window_manager import register_popup, unregister_popup


def clear_text_with_undo(text, readonly=False):
    """
    清空文本框。

    - 可编辑文本框：走 replace_text_keep_undo，确保 Ctrl+Z 可撤销；
    - 只读文本框：临时解锁，清空后恢复只读。
    """
    if readonly:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")
        return

    replace_text_keep_undo(text, "")


def get_text_wrap_value(config_data, wrap_config_key, default_wrap):
    if not config_data or not wrap_config_key:
        return default_wrap != "none"

    config_data.setdefault("text_wrap", {})
    value = config_data["text_wrap"].get(wrap_config_key)

    if isinstance(value, bool):
        return value

    default_value = default_wrap != "none"
    config_data["text_wrap"][wrap_config_key] = default_value
    return default_value


def save_text_wrap_value(config_data, wrap_config_key, value, save_config_func=None):
    if not config_data or not wrap_config_key:
        return

    config_data.setdefault("text_wrap", {})
    config_data["text_wrap"][wrap_config_key] = bool(value)

    if save_config_func:
        save_config_func()


def create_wrap_checkbutton(parent, wrap_var, command):
    """
    标题行里的“自动换行”小控件。

    和 title_bar_button 在高度、pady、间距上尽量保持一致。
    """
    return tk.Checkbutton(
        parent,
        text="自动换行",
        variable=wrap_var,
        command=command,
        bg=THEME["bg"],
        fg=THEME["fg_label"],
        activebackground=THEME["bg"],
        activeforeground=THEME["fg"],
        selectcolor=THEME["bg_input"],
        font=THEME["font_main"],
        relief="flat",
        bd=0,
        padx=4,
        pady=0,
        cursor="hand2",
    )


def create_managed_text_box(
    parent,
    label_text,
    initial_value,
    height=8,
    mono=False,
    config_data=None,
    favorite_key=None,
    save_config_func=None,
    wrap="word",
    readonly=False,
    enable_favorites=True,
    enable_clear=True,
    enable_wrap_toggle=True,
    enable_import_text=False,
    wrap_config_key=None,
):
    """
    通用文本框创建器。

    功能策略：
    - 可编辑文本框：查找、替换、撤销、重做、清空、自动换行、收藏全开；
    - 只读文本框：查找、清空、自动换行；
    - 自动换行统一显示在标题行，交给用户自行切换；
    - 如果传入 wrap_config_key，则每个文本框独立持久化自动换行状态。

    标题行右侧视觉顺序固定为：
        自动换行  清空  导入  收藏

    其中“导入”和“收藏”按参数可选。

    注意：
    Tkinter 的 pack(side="right") 显示顺序与创建顺序相反。
    因此右侧控件创建顺序应为：
        收藏 -> 导入 -> 清空 -> 自动换行
    """
    title_row = styled_frame(parent)
    title_row.pack(fill="x", pady=(8, 2))

    tk.Label(
        title_row,
        text=label_text,
        anchor="w",
        bg=THEME["bg"],
        fg=THEME["fg_label"],
        font=THEME["font_main"],
    ).pack(side="left", fill="x", expand=True)

    wrap_var = tk.BooleanVar(
        value=get_text_wrap_value(
            config_data,
            wrap_config_key,
            wrap,
        )
    )

    frame, text = styled_text_with_scrollbars(
        parent,
        height=height,
        mono=mono,
        wrap="word" if wrap_var.get() else "none",
        readonly=False,
    )
    frame.pack(fill="both", expand=True)

    text.insert("1.0", initial_value or "")

    if readonly:
        text.configure(state="disabled")

    def toggle_wrap():
        text.configure(wrap="word" if wrap_var.get() else "none")
        save_text_wrap_value(
            config_data,
            wrap_config_key,
            wrap_var.get(),
            save_config_func,
        )

    text._text_editor_controller = TextEditorController(
        parent=parent,
        toolbar_parent=parent,
        text_widget=text,
        before_text_frame=frame,
        enable_find=True,
        enable_replace=not readonly,
        enable_undo=not readonly,
        readonly=readonly,
    )

    # ------------------------------------------------------------
    # 标题行右侧功能区
    #
    # 目标视觉顺序：
    #   自动换行  清空  收藏
    #
    # 因为 pack(side="right") 越后 pack 的越靠左，
    # 所以创建顺序必须是：
    #   收藏 -> 清空 -> 自动换行
    # ------------------------------------------------------------

    if (
        not readonly
        and enable_favorites
        and config_data is not None
        and favorite_key
        and save_config_func
    ):
        FavoriteTextBoxController(
            parent=parent,
            title_row=title_row,
            text_widget=text,
            config_data=config_data,
            favorite_key=favorite_key,
            save_config_func=save_config_func,
        )

    if not readonly and enable_import_text:
        def import_text_file():
            file_path = filedialog.askopenfilename(
                title="导入文本文件",
                filetypes=[
                    ("All Files", "*.*"),
                ],
            )

            if not file_path:
                return

            try:
                content, encoding = read_text_with_fallback_encodings(file_path)
                replace_text_keep_undo(text, content)
                safe_show_info(
                    "导入完成",
                    f"文本文件已导入。\n\n编码：{encoding}\n路径：\n{file_path}",
                    parent=parent,
                )
            except Exception as e:
                safe_show_error(
                    "导入失败",
                    f"无法按文本读取该文件：\n{file_path}\n\n错误：{e}",
                    parent=parent,
                )

        title_bar_button(
            title_row,
            "导入",
            import_text_file,
            width=6,
        ).pack(side="right", padx=(0, 6))

    if enable_clear:
        title_bar_button(
            title_row,
            "清空",
            lambda: clear_text_with_undo(text, readonly=readonly),
            width=6,
        ).pack(side="right", padx=(0, 6))

    if enable_wrap_toggle:
        create_wrap_checkbutton(
            title_row,
            wrap_var,
            toggle_wrap,
        ).pack(side="right", padx=(0, 6))

    return text


def close_text_edit_dialog_with_confirm(dialog, text_widgets, save_callback):
    initial_values = {
        widget: get_text_value(widget)
        for widget in text_widgets
    }

    def has_changed():
        return any(
            get_text_value(widget) != initial_values[widget]
            for widget in text_widgets
        )

    def close_self(event=None):
        if not has_changed():
            unregister_popup(dialog)
            dialog.destroy()
            return "break"

        from tkinter import messagebox

        result = messagebox.askyesnocancel(
            "内容已修改",
            "文本内容有修改，是否保存？",
            parent=dialog,
        )

        if result is None:
            return False

        if result and save_callback:
            save_callback()

        unregister_popup(dialog)
        dialog.destroy()
        return "break"

    dialog.protocol("WM_DELETE_WINDOW", close_self)
    register_popup(dialog, close_self)

    return close_self


def edit_exclude_settings(
    parent,
    title,
    config,
    config_data=None,
    folders_favorite_key=None,
    files_favorite_key=None,
    extensions_favorite_key=None,
    save_config_func=None,
    folders_wrap_config_key=None,
    files_wrap_config_key=None,
    extensions_wrap_config_key=None,
):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    center_window(dialog, 720, 740)
    dialog.transient(parent)
    dialog.grab_set()

    confirmed = {"value": False}

    tk.Label(
        dialog,
        text="其他说明：支持换行、英文逗号、中文逗号、空格分隔；在名单行或名单项前添加半角分号 ; 可临时取消该项，例如 ;tests，移除分号即可恢复。扩展名不写点号会自动补点号。注意：像 .gitignore 这类完整特殊文件名应写入“排除文件名”，不要写入“排除扩展名”。快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。",
        bg=THEME["bg"],
        fg=THEME["fg_dim"],
        font=THEME["font_main"],
        anchor="w",
        justify="left",
        wraplength=680,
    ).pack(fill="x", padx=16, pady=(12, 6))

    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16, pady=4)

    folders_text = create_managed_text_box(
        parent=body,
        label_text="排除文件夹",
        initial_value=config.get("exclude_folders", ""),
        height=6,
        mono=False,
        config_data=config_data,
        favorite_key=folders_favorite_key,
        save_config_func=save_config_func,
        wrap="word",
        wrap_config_key=folders_wrap_config_key,
    )

    files_text = create_managed_text_box(
        parent=body,
        label_text="排除文件名",
        initial_value=config.get("exclude_files", ""),
        height=6,
        mono=False,
        config_data=config_data,
        favorite_key=files_favorite_key,
        save_config_func=save_config_func,
        wrap="word",
        wrap_config_key=files_wrap_config_key,
    )

    extensions_text = create_managed_text_box(
        parent=body,
        label_text="排除扩展名",
        initial_value=config.get("exclude_extensions", ""),
        height=6,
        mono=False,
        config_data=config_data,
        favorite_key=extensions_favorite_key,
        save_config_func=save_config_func,
        wrap="word",
        wrap_config_key=extensions_wrap_config_key,
    )

    btn_frame = styled_frame(dialog)
    btn_frame.pack(fill="x", padx=16, pady=12)

    def save_changes():
        config["exclude_folders"] = get_text_value(folders_text)
        config["exclude_files"] = get_text_value(files_text)
        config["exclude_extensions"] = get_text_value(extensions_text)
        confirmed["value"] = True

    def on_save():
        save_changes()
        if save_config_func:
            save_config_func()
        unregister_popup(dialog)
        dialog.destroy()

    close_dialog = close_text_edit_dialog_with_confirm(
        dialog,
        [folders_text, files_text, extensions_text],
        save_changes,
    )

    styled_button(btn_frame, "取消", close_dialog, width=10).pack(
        side="right",
        padx=(6, 0),
    )
    styled_button(btn_frame, "保存", on_save, width=10, accent=True).pack(
        side="right",
        padx=6,
    )

    parent.wait_window(dialog)

    return confirmed["value"]


def edit_extra_text_settings(
    parent,
    title,
    config,
    config_data=None,
    preamble_favorite_key=None,
    ending_favorite_key=None,
    save_config_func=None,
    preamble_wrap_config_key=None,
    ending_wrap_config_key=None,
):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    center_window(dialog, 740, 700)
    dialog.transient(parent)
    dialog.grab_set()

    confirmed = {"value": False}

    tk.Label(
        dialog,
        text="快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。收藏按钮位于各文本框标题栏右侧。",
        bg=THEME["bg"],
        fg=THEME["fg_dim"],
        font=THEME["font_main"],
        anchor="w",
        justify="left",
        wraplength=700,
    ).pack(fill="x", padx=16, pady=(12, 0))

    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16, pady=(6, 4))

    preamble_text = create_managed_text_box(
        parent=body,
        label_text="前言文本",
        initial_value=config.get("preamble_text", ""),
        height=11,
        mono=False,
        config_data=config_data,
        favorite_key=preamble_favorite_key,
        save_config_func=save_config_func,
        wrap="word",
        wrap_config_key=preamble_wrap_config_key,
    )

    ending_text = create_managed_text_box(
        parent=body,
        label_text="后语文本",
        initial_value=config.get("ending_text", ""),
        height=11,
        mono=False,
        config_data=config_data,
        favorite_key=ending_favorite_key,
        save_config_func=save_config_func,
        wrap="word",
        wrap_config_key=ending_wrap_config_key,
    )

    btn_frame = styled_frame(dialog)
    btn_frame.pack(fill="x", padx=16, pady=12)

    def save_changes():
        config["preamble_text"] = get_text_value(preamble_text)
        config["ending_text"] = get_text_value(ending_text)
        confirmed["value"] = True

    def on_save():
        save_changes()
        if save_config_func:
            save_config_func()
        unregister_popup(dialog)
        dialog.destroy()

    close_dialog = close_text_edit_dialog_with_confirm(
        dialog,
        [preamble_text, ending_text],
        save_changes,
    )

    styled_button(btn_frame, "取消", close_dialog, width=10).pack(
        side="right",
        padx=(6, 0),
    )
    styled_button(btn_frame, "保存", on_save, width=10, accent=True).pack(
        side="right",
        padx=6,
    )

    parent.wait_window(dialog)

    return confirmed["value"]
