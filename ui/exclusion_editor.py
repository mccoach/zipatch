# -*- coding: utf-8 -*-

import tkinter as tk
from dataclasses import replace

from core.constants import THEME
from core.exclusion_rules import (
    ExclusionListResult, analyze_exclusion_list, format_exclusion_diagnostics,
)
from core.paths import center_window
from core.text_io import replace_text_keep_undo
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup


EXCLUSION_FIELDS = (
    ("exclude_folders", "目录名单", "directory"),
    ("exclude_files", "文件名单", "file"),
)


def confirm_exclusion_diagnostics(parent, named_results):
    errors = any(result.has_errors for _, result in named_results)
    warnings = any(result.has_warnings for _, result in named_results)
    if not errors and not warnings:
        return True

    dialog = tk.Toplevel(parent)
    dialog.title("排除名单未保存" if errors else "排除名单空格风险确认")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    previous_grab = parent.grab_current()
    dialog.grab_set()
    center_window(dialog, 820, 550)
    accepted = False

    introduction = (
        "本次未保存。请修正或仅删除红色条目后重新保存。\n"
        "黄色条目有效，无需删除。\n"
        "行号对应下列所属名单正文：输入框校验使用回写后的行号，"
        "收藏改名校验使用收藏原文行号。\n"
        "条目采用转义展示，使空格及控制字符可见。"
        if errors else
        "黄色条目有效。外围空格会参与匹配，可能导致未命中。\n"
        "是否确认保存？行号对应下列所属名单正文："
        "输入框校验使用回写后的行号，收藏改名校验使用收藏原文行号。\n"
        "条目采用转义展示；首尾空格数量在风险原因中明确列出。"
    )
    tk.Label(
        dialog, text=introduction, justify="left", anchor="w",
        bg=THEME["bg"], fg=THEME["fg"], font=THEME["font_main"],
        wraplength=780,
    ).pack(fill="x", padx=16, pady=12)
    frame, text = styled_text_with_scrollbars(
        dialog, height=20, mono=True, wrap="word", readonly=True,
    )
    frame.pack(fill="both", expand=True, padx=16)
    text.configure(state="normal")
    text.insert("1.0", format_exclusion_diagnostics(named_results))
    text.configure(state="disabled")
    buttons = styled_frame(dialog)
    buttons.pack(fill="x", padx=16, pady=12)

    def close(confirm=False):
        nonlocal accepted
        accepted = confirm
        unregister_popup(dialog)
        dialog.destroy()
        return True

    styled_button(
        buttons, "返回修改" if errors else "返回检查",
        close, width=12,
    ).pack(side="right", padx=(8, 0))
    if not errors:
        styled_button(
            buttons, "确认保存", lambda: close(True), width=12, accent=True,
        ).pack(side="right")

    register_popup(dialog, close, exit_blocker=True)

    def return_to_editor(event=None):
        close()
        return "break"

    # 退出准备期间全局窗口管理器禁止关闭业务草稿；
    # 此窗口只返回校验决定，必须仍可取消，不应被该禁令锁住。
    dialog.bind("<Escape>", return_to_editor)
    dialog.protocol("WM_DELETE_WINDOW", return_to_editor)
    try:
        parent.wait_window(dialog)
    finally:
        unregister_popup(dialog)
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
    return accepted


class ExclusionDraftSession(TextDraftSession):
    """名单草稿只在明确保存／收藏时解释；普通编辑只撤销过期诊断。"""

    def __init__(self, dialog, commits, config, widgets, status):
        super().__init__(dialog, commits, config, widgets)
        self.status = status
        for field, _, _ in EXCLUSION_FIELDS:
            widget = widgets[field]
            widget.tag_configure("exclusion_disabled", foreground=THEME["fg_dim"])
            widget.tag_configure("exclusion_warning", background="#fff2a8")
            widget.tag_configure("exclusion_error", background=THEME["danger_bg"])
            for tag in ("exclusion_disabled", "exclusion_warning", "exclusion_error"):
                widget.tag_lower(tag)
            widget.tag_raise("search_match")
            widget.tag_raise("search_current")
            widget.edit_modified(False)
            widget.bind("<<Modified>>", self.on_modified, add="+")
            widget._prepare_favorite_content = (
                lambda content, display=True, selected=field:
                self.prepare_favorite(selected, content, display=display)
            )

    def clear_diagnostics(self):
        for widget in self.widgets.values():
            for tag in ("exclusion_disabled", "exclusion_warning", "exclusion_error"):
                widget.tag_remove(tag, "1.0", "end")

    def on_modified(self, event):
        widget = event.widget
        if not self._destroyed and widget.edit_modified():
            widget.edit_modified(False)
            self.clear_diagnostics()
            self.status.set("已修改，尚未校验")

    def render_results(self, results, original_values):
        self.clear_diagnostics()
        tags = {
            "disabled": "exclusion_disabled",
            "warning": "exclusion_warning",
            "error": "exclusion_error",
        }
        for field, result in results.items():
            widget = self.widgets[field]
            if result.normalized_text != original_values[field]:
                replace_text_keep_undo(widget, result.normalized_text)
            widget.edit_modified(False)
            for diagnostic in result.diagnostics:
                number = diagnostic.display_line
                widget.tag_add(tags[diagnostic.state], f"{number}.0", f"{number}.end")
            for tag in tags.values():
                widget.tag_lower(tag)
            widget.tag_raise("search_match")
            widget.tag_raise("search_current")

    def accept(self, values):
        results = {
            field: analyze_exclusion_list(values[field], kind)
            for field, _, kind in EXCLUSION_FIELDS
        }
        self.render_results(results, values)
        named_results = tuple(
            (name, results[field]) for field, name, _ in EXCLUSION_FIELDS
        )
        errors = any(result.has_errors for result in results.values())
        warnings = any(result.has_warnings for result in results.values())
        self.status.set(
            "本次未保存：红色条目需要修正；黄色条目有效"
            if errors else
            "校验完成：黄色条目有效，等待当次确认"
            if warnings else
            "校验完成，正在保存"
        )
        confirmed = confirm_exclusion_diagnostics(self.dialog, named_results)
        if errors or not confirmed:
            return False
        normalized_values = {
            field: result.normalized_text for field, result in results.items()
        }
        completed = super().accept(normalized_values)
        self.status.set("已保存" if completed else "保存失败，内容仍在当前窗口")
        return completed

    def prepare_favorite(self, field, content, display=True):
        definition = next(
            definition for definition in EXCLUSION_FIELDS if definition[0] == field
        )
        _, name, kind = definition
        result = analyze_exclusion_list(content, kind)
        if display:
            self.render_results({field: result}, {field: content})
        else:
            # 改名校验的是收藏正文，不是当前输入框；不回填正文，
            # 诊断行号须指向仍未标准化回写的收藏原文。
            result = ExclusionListResult(
                result.normalized_text,
                result.rules,
                tuple(
                    replace(diagnostic, display_line=diagnostic.input_line)
                    for diagnostic in result.diagnostics
                ),
                result.line_map,
            )
            name += "（当前收藏正文）"
        self.status.set("收藏校验完成；名单配置尚未提交")
        confirmed = confirm_exclusion_diagnostics(self.dialog, ((name, result),))
        if result.has_errors or not confirmed:
            self.status.set("收藏未保存；名单配置尚未提交")
            return None
        return result.normalized_text


def create_exclusion_dialog(parent, title, config, commits, page):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    dialog.grab_set()
    center_window(dialog, 920, 780)
    instructions = (
        "每行一条；空格、逗号、分号都是名称字符，不拆分、不去空格。\n"
        '保存时删除全部半角双引号。行首 | 停用；文件名单行首 : 表示无扩展名条件。\n'
        "名称匹配任意层级；正文行首 \\ 限定源目录根。输入 / 可以，启用规则保存为 \\。\n"
        "* 不跨目录，? 匹配一个字符，独立路径段 ** 匹配零层或多层目录。\n"
        "示例：cache、src\\cache、\\src\\cache、*.log、:.git*、|src/cache。\n"
        "命中目录后整个目录树跳过。编辑期间不校验，保存时统一处理两个名单。\n"
        "灰：停用    黄：有效但有风险    红：无效，需修改\n"
        "Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。"
    )
    tk.Label(
        dialog, text=instructions, justify="left", anchor="w",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
        wraplength=880,
    ).pack(fill="x", padx=16, pady=(12, 4))
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16)
    widgets = {}
    for field, name, _ in EXCLUSION_FIELDS:
        label = (
            "排除目录名称／相对路径"
            if field == "exclude_folders" else
            "排除完整文件名／相对路径"
        )
        widgets[field] = create_managed_text_box(
            body, label, config[field], height=11, mono=True, commits=commits,
            favorite_key=f"{page}_{field}", wrap_config_key=f"{page}.{field}",
        )
    status = tk.StringVar(master=dialog, value="尚未校验")
    session = ExclusionDraftSession(dialog, commits, config, widgets, status)
    buttons = styled_frame(dialog)
    buttons.pack(fill="x", padx=16, pady=12)
    tk.Label(
        buttons, textvariable=status, anchor="w",
        bg=THEME["bg"], fg=THEME["fg_dim"], font=THEME["font_main"],
    ).pack(side="left", fill="x", expand=True)
    styled_button(buttons, "取消", session.close, width=10).pack(
        side="right", padx=(8, 0),
    )
    styled_button(
        buttons, "保存", session.save_and_close, width=10, accent=True,
    ).pack(side="right")
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    parent.wait_window(dialog)
    return session.saved and not getattr(parent._root(), "_zipatch_closed", False)