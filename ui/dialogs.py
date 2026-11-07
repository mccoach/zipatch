# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog, messagebox

from core.constants import THEME
from ui.message_dialogs import safe_show_error, safe_show_info
from ui.window_manager import center_window
from core.file_observation import observe_file
from core.text_io import decode_import_text_bytes
from ui.text_editor import get_text_value, replace_text_keep_undo
from ui.favorites import FavoriteTextBoxController, title_bar_button
from ui.text_editor import TextEditorController
from ui.text_statistics import TextStatistics

from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import (
    register_popup, unregister_popup, restore_editor_interaction,
    take_modal_control,
)


def read_import_text(path, *, preserve_newlines=False):
    """界面导入组织共享获取和用途解码；不写控件、配置或文件。

    一次观察取得完整bytes，普通与精确用途共用获取，
    候选编码及通用换行政策仍由纯解码能力拥有。
    """
    if type(preserve_newlines) is not bool:
        raise TypeError("保留导入换行开关必须是布尔值")
    observed = observe_file(path, include_bytes=True)
    if observed.kind != "file":
        raise FileNotFoundError(f"导入来源文件不存在：{observed.path}")
    return decode_import_text_bytes(
        observed.source_bytes, path=observed.path,
        preserve_newlines=preserve_newlines,
    )


def create_managed_text_box(
    parent, label_text, initial_value, height=8, mono=False, *,
    commits, favorite_key=None, wrap="word", readonly=False,
    enable_favorites=True, enable_clear=True, enable_wrap_toggle=True,
    enable_import_text=False, wrap_config_key=None,
    config=None, field=None, page=None, on_content_changed=None,
    preserve_trailing_newlines=False, preserve_import_newlines=False,
    show_statistics=False,
):
    title_row = styled_frame(parent)
    title_row.pack(fill="x", pady=(8, 2))
    title_label = tk.Label(
        title_row, text=label_text, anchor="w", justify="left",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
    )
    # 标题不与统计平分剩余空间；操作按钮先占位，
    # 统计最后取得全部剩余宽度，文字变化不改变标题栏高度。
    title_label.pack(side="left")

    wraps = commits.manager.config_data["text_wrap"]
    wrap_value = wraps[wrap_config_key] if wrap_config_key else wrap != "none"
    wrap_var = tk.BooleanVar(value=wrap_value)
    frame, text = styled_text_with_scrollbars(
        parent, height=height, mono=mono,
        wrap="word" if wrap_value else "none", readonly=readonly,
    )
    frame.pack(fill="both", expand=True)
    # 规则名单的末尾空行属于正文结构；普通文本继续沿用既有语义。
    text._preserve_trailing_newlines = preserve_trailing_newlines
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
        readonly=readonly,
        on_content_changed=lambda prepared=None:
        text._content_operation_completed(prepared),
    )

    if not readonly and enable_favorites and favorite_key:
        FavoriteTextBoxController(
            parent, title_row, text, commits, favorite_key,
            lambda prepared=None: text._content_operation_completed(prepared),
        )

    if not readonly and enable_import_text:
        def import_text():
            if commits.operation_depth or str(text.cget("state")) != "normal":
                return
            try:
                with commits.operation():
                    path = filedialog.askopenfilename(
                        title="导入文本文件", filetypes=[("All Files", "*.*")],
                        parent=text.winfo_toplevel(),
                    )
                    if not path:
                        return
                    try:
                        if preserve_import_newlines:
                            content, encoding = read_import_text(
                                path, preserve_newlines=True,
                            )
                        else:
                            content, encoding = read_import_text(path)
                    except (OSError, ValueError) as error:
                        safe_show_error("导入失败", str(error), text.winfo_toplevel())
                        return
                    replace_text_keep_undo(text, content)
                    if text._content_operation_completed(content):
                        safe_show_info(
                            "导入完成",
                            f"文本文件已导入。\n\n编码：{encoding}\n路径：\n{path}",
                            text.winfo_toplevel(),
                        )
            finally:
                restore_editor_interaction(text)

        title_bar_button(title_row, "导入", import_text, width=6).pack(
            side="right", padx=(0, 6),
        )

    if enable_clear:
        def clear():
            # 已锁定输入不得产生空配置接受；只读清空仍保留原有用途。
            if commits.operation_depth:
                return
            if not readonly and str(text.cget("state")) != "normal":
                return
            if readonly:
                text.configure(state="normal")
                text.delete("1.0", "end")
                text.configure(state="disabled")
            else:
                replace_text_keep_undo(text, "")
            text._content_operation_completed("")

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
    if show_statistics and not readonly:
        TextStatistics(text, title_row)
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

    def has_changes(self, values):
        return values != self.baseline

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
                if self.has_changes(values):
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
    take_modal_control(dialog)
    center_window(dialog, width, height)
    instructions = "快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。收藏按钮位于各文本框标题栏右侧。"
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
    # 独立名单编辑职责；延迟导入避免与 managed text 创建能力循环导入。
    from ui.exclusion_editor import create_exclusion_dialog
    return create_exclusion_dialog(parent, title, config, commits, page)


def edit_extra_text_settings(parent, title, config, commits, page):
    prefix = "merge_regular" if page == "merge" else "scan"
    wrap_prefix = "merge.regular" if page == "merge" else "scan"
    definitions = [
        ("preamble_text", "前言文本", f"{prefix}_preamble", f"{wrap_prefix}_preamble" if page == "merge" else "scan.preamble", 11),
        ("ending_text", "后语文本", f"{prefix}_ending", f"{wrap_prefix}_ending" if page == "merge" else "scan.ending", 11),
    ]
    return create_text_settings_dialog(parent, title, config, commits, definitions, 740, 700)