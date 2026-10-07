# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog, messagebox

from core.constants import THEME
from core.message_utils import safe_show_error, safe_show_info
from core.paths import center_window
from core.text_io import get_text_value, read_text_with_fallback_encodings, replace_text_keep_undo
from ui.favorites import FavoriteTextBoxController, title_bar_button
from ui.text_editor import TextEditorController
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup


def create_managed_text_box(
    parent, label_text, initial_value, height=8, mono=False, *,
    commits, favorite_key=None, wrap="word", readonly=False,
    enable_favorites=True, enable_clear=True, enable_wrap_toggle=True,
    enable_import_text=False, wrap_config_key=None,
    config=None, field=None, page=None, on_content_changed=None,
):
    title_row = styled_frame(parent)
    title_row.pack(fill="x", pady=(8, 2))
    tk.Label(
        title_row, text=label_text, anchor="w", bg=THEME["bg"],
        fg=THEME["fg_label"], font=THEME["font_main"],
    ).pack(side="left", fill="x", expand=True)

    wraps = commits.manager.config_data["text_wrap"]
    wrap_value = wraps[wrap_config_key] if wrap_config_key else wrap != "none"
    wrap_var = tk.BooleanVar(value=wrap_value)
    frame, text = styled_text_with_scrollbars(
        parent, height=height, mono=mono,
        wrap="word" if wrap_value else "none", readonly=readonly,
    )
    frame.pack(fill="both", expand=True)
    if readonly:
        text.configure(state="normal")
    text.insert("1.0", initial_value or "")
    text.edit_modified(False)
    if readonly:
        text.configure(state="disabled")

    binding = None
    if config is not None and field is not None and not readonly:
        binding = commits.register(text, config, field, page)

    def content_completed(prepared_text=None):
        if binding is not None:
            if prepared_text is None:
                binding.mark_dirty()
            return commits.complete_text_operation(
                binding, prepared_text,
            ).request_satisfied
        if on_content_changed is not None:
            return on_content_changed()
        return True

    text._content_operation_completed = content_completed
    text._text_editor_controller = TextEditorController(
        parent=parent, toolbar_parent=parent, text_widget=text,
        before_text_frame=frame, enable_find=True,
        enable_replace=not readonly, enable_undo=not readonly,
        readonly=readonly, on_content_changed=content_completed,
    )

    if not readonly and enable_favorites and favorite_key:
        FavoriteTextBoxController(
            parent, title_row, text, commits, favorite_key, content_completed,
        )

    if not readonly and enable_import_text:
        def import_text():
            with commits.operation():
                path = filedialog.askopenfilename(
                    title="导入文本文件", filetypes=[("All Files", "*.*")],
                    parent=text.winfo_toplevel(),
                )
            if not path:
                return
            try:
                content, encoding = read_text_with_fallback_encodings(path)
            except (OSError, ValueError) as error:
                safe_show_error("导入失败", str(error), text.winfo_toplevel())
                return
            replace_text_keep_undo(text, content)
            if content_completed(content):
                safe_show_info(
                    "导入完成", f"文本文件已导入。\n\n编码：{encoding}\n路径：\n{path}",
                    text.winfo_toplevel(),
                )

        title_bar_button(title_row, "导入", import_text, width=6).pack(
            side="right", padx=(0, 6),
        )

    if enable_clear:
        def clear():
            if readonly and on_content_changed is not None and commits.operation_depth:
                return
            if readonly:
                text.configure(state="normal")
                text.delete("1.0", "end")
                text.configure(state="disabled")
            else:
                replace_text_keep_undo(text, "")
            content_completed("")

        title_bar_button(title_row, "清空", clear, width=6).pack(
            side="right", padx=(0, 6),
        )

    if enable_wrap_toggle:
        def toggle_wrap():
            text.configure(wrap="word" if wrap_var.get() else "none")
            if wrap_config_key:
                commits.submit_values(
                    wraps, {wrap_config_key: wrap_var.get()}, text.winfo_toplevel(),
                )

        tk.Checkbutton(
            title_row, text="自动换行", variable=wrap_var, command=toggle_wrap,
            bg=THEME["bg"], fg=THEME["fg_label"],
            activebackground=THEME["bg"], selectcolor=THEME["bg_input"],
            font=THEME["font_main"], relief="flat", bd=0, padx=4, pady=0,
        ).pack(side="right", padx=(0, 6))
    return text


class TextDraftSession:
    """可取消文本编辑；失败候选绝不留在共享配置。"""

    def __init__(self, dialog, commits, config, widgets):
        self.dialog = dialog
        self.commits = commits
        self.config = config
        self.widgets = widgets
        self.baseline = {field: get_text_value(widget) for field, widget in widgets.items()}
        self.saved = False
        self._close_in_progress = False
        self._destroyed = False

        def destroyed(event):
            if event.widget is self.dialog:
                self._destroyed = True

        dialog.bind("<Destroy>", destroyed, add="+")

    def values(self):
        return {
            field: self.commits.read_text(widget)
            for field, widget in self.widgets.items()
        }

    def accept(self, values):
        if not self.commits.accept_draft(self.config, values, self.dialog):
            return False
        self.baseline = dict(values)
        self.saved = True
        return True

    def save_and_close(self):
        if self._destroyed:
            return True
        if self._close_in_progress or self.commits.operation_depth:
            return False
        self._close_in_progress = True
        try:
            # 错误提示会进入模态事件循环，保护覆盖整个保存完成流程。
            with self.commits.operation():
                if not self.accept(self.values()):
                    return False
                self.destroy()
                return True
        finally:
            self._close_in_progress = False

    def destroy(self):
        if self._destroyed:
            return
        unregister_popup(self.dialog)
        self.dialog.destroy()
        self._destroyed = True

    def prepare_close(self):
        """只准备关闭决定，不提前销毁；确认期间拒绝重入关闭。"""
        if self._destroyed:
            return True
        if self._close_in_progress or self.commits.operation_depth:
            return False
        self._close_in_progress = True
        try:
            with self.commits.operation():
                values = self.values()
                if values != self.baseline:
                    choice = messagebox.askyesnocancel(
                        "内容已修改", "文本内容有修改，是否保存？", parent=self.dialog,
                    )
                    if choice is None:
                        return False
                    if choice and not self.accept(values):
                        return False
                return True
        finally:
            self._close_in_progress = False

    def close(self):
        if not self.prepare_close():
            return False
        self.destroy()
        return True


def create_text_settings_dialog(parent, title, config, commits, definitions, width, height):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    dialog.grab_set()
    center_window(dialog, width, height)
    is_exclude_dialog = any(field == "exclude_extensions" for field, *_ in definitions)
    instructions = (
        "其他说明：支持换行、英文逗号、中文逗号、空格分隔；"
        "在名单行或名单项前添加半角分号 ; 可临时取消该项，例如 ;tests，移除分号即可恢复。"
        "扩展名不写点号会自动补点号。注意：像 .gitignore 这类完整特殊文件名"
        "应写入“排除文件名”，不要写入“排除扩展名”。"
        if is_exclude_dialog else ""
    )
    instructions += "快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。收藏按钮位于各文本框标题栏右侧。"
    tk.Label(
        dialog,
        text=instructions,
        bg=THEME["bg"], fg=THEME["fg_dim"], font=THEME["font_main"],
        wraplength=width - 40, anchor="w", justify="left",
    ).pack(fill="x", padx=16, pady=(12, 6))
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16)
    widgets = {}
    for field, label, favorite, wrap_key, text_height in definitions:
        widgets[field] = create_managed_text_box(
            body, label, config[field], height=text_height, commits=commits,
            favorite_key=favorite, wrap_config_key=wrap_key,
        )
    session = TextDraftSession(dialog, commits, config, widgets)
    row = styled_frame(dialog)
    row.pack(fill="x", padx=16, pady=12)
    styled_button(row, "取消", session.close, width=10).pack(side="right", padx=(6, 0))
    styled_button(row, "保存", session.save_and_close, width=10, accent=True).pack(
        side="right", padx=6,
    )
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    parent.wait_window(dialog)
    # 程序整体退出不是设置窗口保存回调继续执行的边界。
    return session.saved and not getattr(parent._root(), "_zipatch_closed", False)


def edit_exclude_settings(parent, title, config, commits, page):
    definitions = [
        ("exclude_folders", "排除文件夹", f"{page}_exclude_folders", f"{page}.exclude_folders", 6),
        ("exclude_files", "排除文件名", f"{page}_exclude_files", f"{page}.exclude_files", 6),
        ("exclude_extensions", "排除扩展名", f"{page}_exclude_extensions", f"{page}.exclude_extensions", 6),
    ]
    return create_text_settings_dialog(parent, title, config, commits, definitions, 720, 740)


def edit_extra_text_settings(parent, title, config, commits, page):
    prefix = "merge_regular" if page == "merge" else "scan"
    wrap_prefix = "merge.regular" if page == "merge" else "scan"
    definitions = [
        ("preamble_text", "前言文本", f"{prefix}_preamble", f"{wrap_prefix}_preamble" if page == "merge" else "scan.preamble", 11),
        ("ending_text", "后语文本", f"{prefix}_ending", f"{wrap_prefix}_ending" if page == "merge" else "scan.ending", 11),
    ]
    return create_text_settings_dialog(parent, title, config, commits, definitions, 740, 700)