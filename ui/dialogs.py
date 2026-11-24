# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog, messagebox, TclError
from pathlib import Path

from contextlib import nullcontext

from core.path_validation import normalize_windows_display_path
from ui.file_dialog_history import (
    file_dialog_initial_directory, remember_file_dialog_directory,
)

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


def show_rule_help(parent, title, content, *, width, height):
    """排除及换行帮助共用只读窗口，不提交草稿或配置。"""
    dialog = tk.Toplevel(parent)
    try:
        dialog.title(title)
        dialog.configure(bg=THEME["bg"])
        dialog.transient(parent)
        take_modal_control(dialog)
        center_window(dialog, width, height)

        frame, text = styled_text_with_scrollbars(
            dialog, height=30, wrap="word", readonly=True,
        )
        frame.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        text.configure(state="normal")
        text.insert("1.0", content)
        text.configure(state="disabled")
        buttons = styled_frame(dialog)
        buttons.pack(fill="x", padx=16, pady=(0, 14))

        def close():
            unregister_popup(dialog)
            dialog.destroy()
            return True

        styled_button(buttons, "关闭", close, width=10).pack(side="right")
        register_popup(dialog, close)
        parent.wait_window(dialog)
    finally:
        unregister_popup(dialog)
        if dialog.winfo_exists():
            dialog.destroy()


def create_managed_text_box(
    parent, label_text, initial_value, height=8, mono=False, *,
    commits, favorite_key=None, wrap="word", readonly=False,
    enable_favorites=True, enable_clear=True, enable_wrap_toggle=True,
    enable_import_text=False, wrap_config_key=None,
    config=None, field=None, page=None, on_content_changed=None,
    preserve_trailing_newlines=False, preserve_import_newlines=False,
    show_statistics=False, title_action_factory=None, enable_copy=False,
    browse_kind=None, file_dialog_key=None,
):
    """通用编辑框；浏览按需启用，路径固定追加而非替换正文。

    文件选择偏好按文本框稳定用途和操作分别保存。
    业务名单过滤、检查和配置接受继续经过原正文完成链。
    """
    if browse_kind not in (None, "directory", "file"):
        raise ValueError("浏览类别必须是directory、file或None")
    identity = file_dialog_key or wrap_config_key or (
        f"{page}.{field}" if page and field else None
    )
    if not readonly and (enable_import_text or browse_kind is not None):
        if type(identity) is not str or not identity:
            raise ValueError("启用文件选择的文本框必须提供稳定file_dialog_key")

    title_row = styled_frame(parent)
    title_row.pack(fill="x", pady=(8, 2))
    title_label = tk.Label(
        title_row, text=label_text, anchor="w", justify="left",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
    )
    title_label.pack(side="left")

    # 工具区整体靠右，内部按阅读顺序从左向右排列；
    # 不再由多个side=right的登记顺序隐式决定按钮位置。
    tools = styled_frame(title_row)
    tools.pack(side="right")
    text_actions = []

    wraps = commits.manager.config_data["text_wrap"]
    wrap_value = wraps[wrap_config_key] if wrap_config_key else wrap != "none"
    wrap_var = tk.BooleanVar(master=parent, value=wrap_value)
    frame, text = styled_text_with_scrollbars(
        parent, height=height, mono=mono,
        wrap="word" if wrap_value else "none", readonly=readonly,
    )
    frame.pack(fill="both", expand=True)
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
        toolbar_parent=parent, text_widget=text,
        before_text_frame=frame, commits=commits, enable_find=True,
        enable_replace=not readonly, enable_undo=not readonly,
        readonly=readonly,
        on_content_changed=lambda prepared=None:
        text._content_operation_completed(prepared),
    )

    if not readonly and browse_kind is not None:
        browse_key = f"text.{identity}.browse"

        def browse():
            if commits.operation_depth or str(text.cget("state")) != "normal":
                return
            try:
                with (
                    commits.operation(),
                    binding.suspend_focus_commit()
                    if binding is not None else nullcontext(),
                ):
                    owner = text.winfo_toplevel()
                    initial = file_dialog_initial_directory(commits, browse_key)
                    if browse_kind == "directory":
                        selected = filedialog.askdirectory(
                            title="选择文件夹，追加到名单末尾",
                            initialdir=initial, mustexist=True, parent=owner,
                        )
                        paths = (selected,) if selected else ()
                    else:
                        paths = filedialog.askopenfilenames(
                            title="选择文件，追加到名单末尾（可多选）",
                            initialdir=initial,
                            filetypes=[("All Files", "*.*")], parent=owner,
                        )
                    if not paths:
                        return

                    # 选择器填写名义路径，不resolve压平链接，
                    # 不读取文件正文，不保存任何业务授权。
                    normalized = []
                    for spelling in paths:
                        path = Path(spelling)
                        if (
                            not path.is_absolute()
                            or any(ord(character) < 32 for character in spelling)
                            or not (
                                path.is_dir() if browse_kind == "directory"
                                else path.is_file()
                            )
                        ):
                            raise ValueError(f"选择路径已失效或类型不符：{spelling}")
                        normalized.append(normalize_windows_display_path(spelling))
                    directory = (
                        Path(paths[0]) if browse_kind == "directory"
                        else Path(paths[0]).parent
                    )
                    editing = getattr(text, "_list_text_editing", None)
                    current = (
                        editing.complete() if editing is not None
                        else text.get("1.0", "end-1c")
                    )
                    separator = "\n" if current and not current.endswith("\n") else ""
                    addition = separator + "\n".join(normalized) + "\n"
                    with editing.edit_group() if editing is not None else nullcontext():
                        automatic = text.cget("autoseparators")
                        text.edit_separator()
                        text.configure(autoseparators=False)
                        try:
                            text.insert("end-1c", addition)
                        finally:
                            text.edit_separator()
                            text.configure(autoseparators=automatic)
                    remember_file_dialog_directory(commits, browse_key, directory)
                    text.mark_set("insert", "end-1c")
                    text.see("end")
                # 有绑定时，正文完成链统一保存正文和选择目录偏好。
                # 无绑定的草稿只保存目录偏好，不提前接受草稿正文。
                text._content_operation_completed()
                if binding is None:
                    commits.finish(owner)
            except (OSError, ValueError) as error:
                safe_show_error("浏览未完成", str(error), text.winfo_toplevel())
            finally:
                restore_editor_interaction(text)

        browse_button = title_bar_button(tools, "浏览", browse, width=4)
        text_actions.append(browse_button)
        text._browse_button = browse_button
        from ui.widgets import add_tooltip
        add_tooltip(
            browse_button,
            "选择文件夹，追加绝对路径到名单末尾"
            if browse_kind == "directory"
            else "选择文件，逐行追加绝对路径到名单末尾；支持多选",
        )

    if not readonly and enable_import_text:
        import_key = f"text.{identity}.import"

        def import_text():
            if commits.operation_depth or str(text.cget("state")) != "normal":
                return
            try:
                with (
                    commits.operation(),
                    binding.suspend_focus_commit()
                    if binding is not None else nullcontext(),
                ):
                    owner = text.winfo_toplevel()
                    path = filedialog.askopenfilename(
                        title="导入文本文件", filetypes=[("All Files", "*.*")],
                        initialdir=file_dialog_initial_directory(commits, import_key),
                        parent=owner,
                    )
                    if not path:
                        return
                    try:
                        content, encoding = read_import_text(
                            path, preserve_newlines=preserve_import_newlines,
                        )
                    except (OSError, ValueError) as error:
                        safe_show_error("导入失败", str(error), owner)
                        return
                    replace_text_keep_undo(text, content)
                    remember_file_dialog_directory(
                        commits, import_key, Path(path).parent,
                    )
                completed = text._content_operation_completed(content)
                # 有绑定的完成链已经交付保存结果，失败后不自动再试。
                # 无绑定的设置草稿仅持久化选择目录，不提交草稿正文。
                if binding is None:
                    saved = commits.finish(owner)
                    completed = completed and saved.request_satisfied
                if completed:
                    safe_show_info(
                        "导入完成",
                        f"文本文件已导入。\n\n编码：{encoding}\n路径：\n{path}",
                        owner,
                    )
            except (OSError, ValueError) as error:
                safe_show_error("导入未完成", str(error), text.winfo_toplevel())
            finally:
                restore_editor_interaction(text)

        import_button = title_bar_button(tools, "导入", import_text, width=4)
        text_actions.append(import_button)
        text._import_button = import_button

    if not readonly and enable_favorites and favorite_key:
        favorite = FavoriteTextBoxController(
            parent, tools, text, commits, favorite_key,
            lambda prepared=None: text._content_operation_completed(prepared),
        )
        favorite.button.pack_forget()
        text_actions.append(favorite.button)

    if title_action_factory is not None:
        # 页面专属动作处于输入来源工具之后、复制与清空之前。
        # 工厂仍只取得正文及工具容器，业务语义不进入通用编辑器。
        existing = set(tools.winfo_children())
        title_action_factory(text, tools)
        for action in tools.winfo_children():
            if action not in existing:
                action.pack_forget()
                text_actions.append(action)

    if enable_copy:
        def copy_all():
            try:
                content = text.get("1.0", "end-1c")
                text.clipboard_clear()
                text.clipboard_append(content)
            except TclError as error:
                safe_show_error("复制失败", str(error), text.winfo_toplevel())
                return
            copy_button.configure(text="已复制")
            pending = copy_button._feedback_after_id
            if pending is not None:
                copy_button.after_cancel(pending)

            def reset_feedback():
                copy_button._feedback_after_id = None
                copy_button.configure(text="复制")

            copy_button._feedback_after_id = copy_button.after(
                1200, reset_feedback,
            )

        copy_button = title_bar_button(tools, "复制", copy_all, width=4)
        copy_button._feedback_after_id = None
        text_actions.append(copy_button)

        def cancel_copy_feedback(event):
            if event.widget is copy_button:
                pending = copy_button._feedback_after_id
                if pending is not None:
                    copy_button.after_cancel(pending)
                    copy_button._feedback_after_id = None

        copy_button.bind("<Destroy>", cancel_copy_feedback, add="+")
        text._copy_all_button = copy_button

    if enable_clear:
        def clear():
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

        text_actions.append(title_bar_button(tools, "清空", clear, width=4))

    if enable_wrap_toggle:
        def toggle_wrap():
            text.configure(wrap="word" if wrap_var.get() else "none")
            if wrap_config_key:
                commits.submit_values(
                    wraps, {wrap_config_key: wrap_var.get()}, text.winfo_toplevel(),
                )

        text_actions.insert(0, tk.Checkbutton(
            tools, text="自动换行", variable=wrap_var, command=toggle_wrap,
            bg=THEME["bg"], fg=THEME["fg_label"],
            activebackground=THEME["bg"], selectcolor=THEME["bg_input"],
            font=THEME["font_main"], relief="flat", bd=0, padx=4, pady=0,
        ))
    for action in text_actions:
        action.pack(side="left", padx=(0, 6))
    text._title_tools = tools
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

    def apply(self):
        """接受当前草稿并保存，成功后继续留在窗口编辑。"""
        return self._save(close_after=False)

    def save_and_close(self):
        """只有本次保存成功后才关闭，失败继续保留草稿。"""
        return self._save(close_after=True)

    def _save(self, *, close_after):
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
                if close_after:
                    self.destroy()
                return True
        finally:
            self._close_in_progress = False

    def cancel(self):
        """放弃尚未接受的草稿并关闭；不撤销此前成功应用的设置。"""
        if self._destroyed:
            return True
        if self._close_in_progress or self.commits.operation_depth:
            return False
        self.destroy()
        return True

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
    styled_button(row, "取消", session.cancel, width=10).pack(
        side="right", padx=(8, 0),
    )
    styled_button(row, "保存", session.save_and_close, width=10, accent=True).pack(
        side="right", padx=(8, 0),
    )
    styled_button(row, "应用", session.apply, width=10).pack(side="right")
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