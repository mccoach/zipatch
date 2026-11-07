# -*- coding: utf-8 -*-

"""当前名单结果、展示映射、比较与诊断，不拥有业务持久化。"""

import tkinter as tk
from contextlib import contextmanager
from dataclasses import dataclass

from core.constants import THEME
from ui.message_dialogs import safe_show_error
from ui.window_manager import center_window
from core.rule_lists import (
    RuleListResult, clean_rule_quotes, format_rule_diagnostics,
    has_exclusion_content,
)
from ui.text_editor import replace_text_keep_undo
from ui.list_text_editing import ListTextEditing
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup, take_modal_control


RULE_DISABLED_FOREGROUND = "#555555"
RULE_DISABLED_BACKGROUND = "#c6c6c6"
RULE_WARNING_BACKGROUND = "#fff2a8"


def confirm_rule_diagnostics(parent, named_results, use_input_lines=False):
    errors = any(result.has_errors for _, result in named_results)
    confirmation = any(
        result.requires_confirmation for _, result in named_results
    )
    if not errors and not confirmation:
        return True
    dialog = tk.Toplevel(parent)
    dialog.title("名单未保存" if errors else "名单空格风险确认")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
    center_window(dialog, 820, 550)
    accepted = False
    introduction = (
        "本次未保存。请修正、删除或停用红色条目。\n黄色条目有效，无需删除。"
        if errors else
        "黄色条目有效，首尾半角空格参与匹配，可能导致未命中。是否确认保存？"
    )
    introduction += (
        "\n输入框校验显示整理后的行号；收藏改名显示收藏原文行号。"
        "\n转义展示用于看清空格和控制字符，不要求输入转义形式。"
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
    text.insert(
        "1.0", format_rule_diagnostics(named_results, use_input_lines),
    )
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
        buttons, "返回修改" if errors else "返回检查", close, width=12,
    ).pack(side="right", padx=(8, 0))
    if not errors:
        styled_button(
            buttons, "确认保存", lambda: close(True), width=12, accent=True,
        ).pack(side="right")
    register_popup(dialog, close, exit_blocker=True)

    def return_to_editor(event=None):
        close()
        return "break"

    dialog.bind("<Escape>", return_to_editor)
    dialog.protocol("WM_DELETE_WINDOW", return_to_editor)
    try:
        parent.wait_window(dialog)
    finally:
        unregister_popup(dialog)
    return accepted


@dataclass(frozen=True)
class RuleListView:
    text: str
    result: RuleListResult
    input_lines: bool


class RuleListEditor:
    """当前结果供展示比较复用；临时操作和持续失败提示分别拥有生命周期。"""

    def __init__(
        self, dialog, commits, widgets, definitions, status, config, *,
        semantics="standard",
    ):
        if semantics not in ("standard", "exclusion"):
            raise ValueError("未知名单编辑语义")
        self.dialog = dialog
        self.commits = commits
        self.widgets = widgets
        self.config = config
        self.semantics = semantics
        self.definitions = {
            field: (name, kind) for field, name, kind in definitions
        }
        self.status = status
        self.destroyed = False
        self.views = {}
        self.editing = {}
        self.base_status = ""
        self.failure_status = ""
        self._operation_status = None
        self._cursor_field = None
        self._diagnostic_after_id = None

        for field, widget in widgets.items():
            if semantics == "exclusion":
                widget._preserve_trailing_newlines = True
            widget.tag_configure(
                "exclusion_disabled", foreground=RULE_DISABLED_FOREGROUND,
                background=RULE_DISABLED_BACKGROUND,
            )
            widget.tag_configure(
                "exclusion_warning", background=RULE_WARNING_BACKGROUND,
            )
            widget.tag_configure(
                "exclusion_error", background=THEME["danger_bg"],
            )
            editing = ListTextEditing(
                widget, commits,
                lambda text, selected=field: self.on_text(selected, text),
            )
            self.editing[field] = editing
            result = commits.prepare_rule(
                config, field, self.definitions[field][1], editing.text,
                semantics=semantics,
            )
            view = RuleListView(
                editing.text, result, editing.text != result.normalized_text,
            )
            self.views[field] = view
            widget._prepare_favorite_content = (
                lambda content, display=True, selected=field:
                self.prepare_favorite(selected, content, display)
            )
            self.render(widget, view)
            if semantics == "exclusion":
                for sequence in (
                    "<KeyRelease>", "<ButtonRelease-1>",
                    "<FocusIn>", "<<Modified>>",
                ):
                    widget.bind(
                        sequence,
                        lambda event, selected=field:
                        self.refresh_line_diagnostic(selected),
                        add="+",
                    )

        self.baseline_comparison = self.comparison()
        dialog.bind("<Destroy>", self.on_destroy, add="+")
        self.set_status("高亮随当前原文显示；明确保存时才标准化并提交")

    def on_destroy(self, event):
        if event.widget is not self.dialog or self.destroyed:
            return
        self.destroyed = True
        if self._diagnostic_after_id is not None:
            self.dialog.after_cancel(self._diagnostic_after_id)
            self._diagnostic_after_id = None
        self.views.clear()
        self.editing.clear()

    def set_status(self, text):
        self.base_status = text
        self.refresh_line_diagnostic()
        self.schedule_line_diagnostic()

    @contextmanager
    def operation_status(self, text):
        """仅在本次同步操作内遮蔽行诊断，退出时必定恢复。"""
        previous = self._operation_status
        self._operation_status = text
        self.refresh_line_diagnostic()
        try:
            yield
        finally:
            self._operation_status = previous
            if not self.destroyed:
                self.refresh_line_diagnostic()
                self.schedule_line_diagnostic()

    def mark_save_failed(self):
        """持续失败事实只在成功保存业务草稿后清除。"""
        self.failure_status = "保存失败，内容仍在当前窗口"
        self.refresh_line_diagnostic()

    def schedule_line_diagnostic(self):
        if self.semantics != "exclusion" or self.destroyed:
            return
        if self._diagnostic_after_id is None:
            self._diagnostic_after_id = self.dialog.after_idle(
                self.finish_line_diagnostic,
            )

    def finish_line_diagnostic(self):
        self._diagnostic_after_id = None
        self.refresh_line_diagnostic()

    def refresh_line_diagnostic(self, field=None):
        """光标只消费与当前修订一致的已完成结果，不读取或分析正文。"""
        if self.destroyed:
            return
        if field is not None:
            self._cursor_field = field
        display = self._operation_status or self.failure_status or self.base_status
        selected = self._cursor_field
        if (
            self.semantics == "exclusion"
            and selected in self.editing
            and self._operation_status is None
            and not self.failure_status
            and not self.commits.operation_depth
            and str(self.widgets[selected].cget("state")) == "normal"
        ):
            editing = self.editing[selected]
            view = self.views[selected]
            if (
                editing.depth == 0
                and editing.revision == editing.completed_revision
                and editing.text == view.text
            ):
                number = int(
                    self.widgets[selected].index("insert").split(".")[0]
                )
                reasons = [
                    diagnostic.reason
                    for diagnostic in view.result.diagnostics
                    if (
                        diagnostic.input_line if view.input_lines
                        else diagnostic.display_line
                    ) == number
                ]
                if reasons:
                    display += (
                        f"；{self.definitions[selected][0]}第 {number} 行："
                        + "；".join(reasons)
                    )
        self.status.set(display)

    def render(self, widget, view):
        tags = {
            "disabled": "exclusion_disabled",
            "warning": "exclusion_warning",
            "error": "exclusion_error",
        }
        for tag in tags.values():
            widget.tag_remove(tag, "1.0", "end")
        for diagnostic in view.result.diagnostics:
            number = (
                diagnostic.input_line if view.input_lines
                else diagnostic.display_line
            )
            widget.tag_add(
                tags[diagnostic.state], f"{number}.0", f"{number}.end",
            )
        for tag in tags.values():
            widget.tag_lower(tag)
        widget.tag_raise("search_match")
        widget.tag_raise("search_current")
        widget.tag_raise("sel")

    def update_edit_status(self):
        if self.semantics == "standard":
            self.set_status("已修改，尚未保存；高亮对应当前原文")
        else:
            self.set_status(
                "已修改，尚未保存；高亮对应当前原文"
                if self.comparison() != self.baseline_comparison else
                "当前表达无未保存变化；高亮对应当前原文"
            )

    def on_text(self, field, text):
        if self.destroyed:
            return
        previous = self.views[field]
        if previous.text == text:
            view = previous
        else:
            if text in (
                previous.result.source_text, previous.result.normalized_text,
            ):
                result = previous.result
            else:
                result = self.commits.prepare_rule(
                    self.config, field, self.definitions[field][1], text,
                    semantics=self.semantics,
                )
            view = RuleListView(
                text, result, text != result.normalized_text,
            )
            self.views[field] = view
        self.render(self.widgets[field], view)
        self.update_edit_status()
        # on_text 返回后正式正文关联才完成；空闲回调仅刷新派生展示。
        self.schedule_line_diagnostic()

    def values(self):
        return {
            field: editing.complete()
            for field, editing in self.editing.items()
        }

    def comparison(self):
        return {
            field: view.result.normalized_text
            for field, view in self.views.items()
        }

    def has_changes(self):
        self.values()
        return self.comparison() != self.baseline_comparison

    def prepare(self, values):
        results = {}
        for field, content in values.items():
            editing = self.editing[field]
            current = editing.complete()
            cleaned = clean_rule_quotes(content)
            if cleaned != current:
                replace_text_keep_undo(self.widgets[field], cleaned)
            result = self.views[field].result
            normalized = result.normalized_text
            view = RuleListView(normalized, result, False)
            # 回写成功才确认展示映射；部分失败沿原完成链核验实际正文。
            if normalized != editing.text:
                replace_text_keep_undo(self.widgets[field], normalized)
            self.views[field] = view
            self.render(self.widgets[field], view)
            results[field] = result
        self.refresh_line_diagnostic()
        return results

    def confirm(self, results):
        named = tuple(
            (self.definitions[field][0], result)
            for field, result in results.items()
        )
        errors = any(result.has_errors for result in results.values())
        self.set_status(
            "本次未保存：红色条目需要修正；黄色条目有效"
            if errors else "校验完成，等待确认或保存"
        )
        accepted = confirm_rule_diagnostics(self.dialog, named)
        return not errors and accepted

    def accepted(self, results):
        for field, result in results.items():
            self.commits.remember_rule(self.config, field, result)
        self.baseline_comparison = self.comparison()
        self.failure_status = ""

    def prepare_favorite(self, field, content, display=True):
        if self.semantics == "standard":
            return self.prepare_standard_favorite(field, content, display)
        if not has_exclusion_content(content):
            # 最低内容拒绝先于标准化，不能为拒绝回写草稿或更新基线。
            with self.operation_status("收藏内容仅含排版，未提交"):
                safe_show_error(
                    "收藏内容仅含排版",
                    "排除收藏至少需要一条非排版行；停用行也可以。\n"
                    "当前草稿和原收藏均未因此修改。",
                    self.dialog,
                )
            return None

        try:
            with self.operation_status("收藏校验中；名单配置尚未提交"):
                name, kind = self.definitions[field]
                if display:
                    result = self.prepare({field: content})[field]
                    input_lines = False
                else:
                    cleaned = clean_rule_quotes(content)
                    current = self.views[field].result
                    result = (
                        current
                        if cleaned in (
                            current.source_text, current.normalized_text,
                        )
                        else self.commits.prepare_rule(
                            self.config, field, kind, content,
                            semantics=self.semantics,
                        )
                    )
                    input_lines = True
                    name += "（当前收藏正文）"
                accepted = confirm_rule_diagnostics(
                    self.dialog, ((name, result),),
                    use_input_lines=input_lines,
                )
                if result.has_errors or not accepted:
                    return None
                return result.normalized_text
        finally:
            if not self.destroyed:
                self.update_edit_status()

    def prepare_standard_favorite(self, field, content, display=True):
        """范围外换行收藏维持原准备、回写及反馈契约。"""
        name, kind = self.definitions[field]
        if display:
            result = self.prepare({field: content})[field]
            input_lines = False
        else:
            cleaned = clean_rule_quotes(content)
            current = self.views[field].result
            if cleaned in (current.source_text, current.normalized_text):
                result = current
            else:
                result = self.commits.prepare_rule(
                    self.config, field, kind, content,
                    semantics="standard",
                )
            input_lines = cleaned != result.normalized_text
            name += "（当前收藏正文）"
        self.set_status("收藏校验完成；名单配置尚未提交")
        accepted = (
            confirm_rule_diagnostics(
                self.dialog, ((name, result),), use_input_lines=True,
            )
            if input_lines else
            confirm_rule_diagnostics(self.dialog, ((name, result),))
        )
        if result.has_errors or not accepted:
            self.set_status("收藏未保存；名单配置尚未提交")
            return None
        return result.normalized_text