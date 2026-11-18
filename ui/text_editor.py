# -*- coding: utf-8 -*-

import tkinter as tk
from contextlib import nullcontext
from tkinter import TclError
from bisect import bisect_left, bisect_right

from core.constants import THEME
from core.text_search import (
    SearchResult, compose_search_replacement, find_text_matches,
)
from ui.search_icons import linear_icon_button, set_mode_button_state
from ui.theme import styled_frame, styled_entry



def get_text_value(text_widget):
    """普通接受政策保持；精确用途由调用方只移除Tk哨兵。"""
    return text_widget.get("1.0", "end").rstrip("\n")


def set_text_value(text_widget, value):
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value or "")


def replace_text_keep_undo(text_widget, value):
    """整体替换及所属自动处理同次完成，不制造相同正文的撤销历史。"""
    final = value or ""
    editing = getattr(text_widget, "_list_text_editing", None)
    if editing is not None:
        from core.rule_lists import clean_rule_quotes
        final = clean_rule_quotes(final)
        if editing.complete() == final:
            return
    elif text_widget.get("1.0", "end-1c") == final:
        return
    with (
        editing.edit_group(prepared_text=final)
        if editing is not None else nullcontext()
    ):
        automatic = text_widget.cget("autoseparators")
        text_widget.edit_separator()
        text_widget.configure(autoseparators=False)
        try:
            text_widget.replace("1.0", "end-1c", final)
        finally:
            text_widget.edit_separator()
            text_widget.configure(autoseparators=automatic)


def replace_text_preserve_view(text_widget, value):
    """同步替换展示内容；不刷新事件循环，不产生撤销历史。"""
    first, last = text_widget.yview()
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value or "")
    if last >= 0.999:
        text_widget.see("end")
    else:
        text_widget.yview_moveto(first)


class TextEditorController:
    """
    Text 文本框插件控制器。

    可按需启用：
    - 查找；
    - 替换；
    - 撤销；
    - 重做；
    - 清空；
    - 自动换行由外层 managed text 创建。
    """

    def __init__(
        self,
        toolbar_parent,
        text_widget,
        before_text_frame,
        commits,
        enable_find=True,
        enable_replace=True,
        enable_undo=True,
        readonly=False,
        on_content_changed=None,
    ):
        self.text_widget = text_widget
        self.before_text_frame = before_text_frame
        self.enable_find = enable_find
        self.enable_replace = enable_replace
        self.enable_undo = enable_undo
        self.readonly = readonly
        self.on_content_changed = on_content_changed

        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.commits = commits

        self.case_sensitive = tk.BooleanVar(master=text_widget, value=False)
        self.whole_word = tk.BooleanVar(master=text_widget, value=False)
        self.wildcard = tk.BooleanVar(master=text_widget, value=False)
        self.match_status = tk.StringVar(master=text_widget, value="—")
        self.matches = SearchResult()
        self.current_index = None
        self._source = ""
        self._source_dirty = True
        self._line_starts = (0,)
        self._supplementary_positions = ()
        self._tk_uses_surrogates = int(
            text_widget.tk.call("string", "length", "\U0001F600")
        ) == 2
        self._refresh_after_id = None
        self._refresh_anchor = None
        # 只区分本控制器的查找跳转与外部光标移动；
        # 实时光标位置仍由Text控件唯一拥有，不保存第二份光标坐标。
        self._moving_search_cursor = False
        self._destroyed = False
        self._boundary_binding = text_widget._root().bind(
            "<<SearchBoundariesChanged>>", self.on_boundaries_changed, add="+",
        )

        self.search_bar = styled_frame(toolbar_parent)
        self.search_bar.pack(fill="x", pady=(0, 6), before=before_text_frame)
        self.search_bar.pack_forget()

        self.create_search_bar()
        self.configure_text_widget()
        self.bind_shortcuts()

    def create_search_bar(self):
        """输入、稳定状态、三个独立模式、原五个动作共用一行。"""
        replace_enabled = self.enable_replace and not self.readonly
        self.search_bar.grid_columnconfigure(
            1, weight=1, uniform="search_inputs",
        )
        tk.Label(
            self.search_bar, text="查找", bg=THEME["bg"],
            fg=THEME["fg_label"], font=THEME["font_main"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 3))
        find_frame, self.find_entry = styled_entry(
            self.search_bar, self.find_var,
        )
        find_frame.grid(row=0, column=1, sticky="ew")
        if replace_enabled:
            self.search_bar.grid_columnconfigure(
                3, weight=1, uniform="search_inputs",
            )
            tk.Label(
                self.search_bar, text="替换", bg=THEME["bg"],
                fg=THEME["fg_label"], font=THEME["font_main"],
            ).grid(row=0, column=2, sticky="w", padx=(5, 3))
            replace_frame, self.replace_entry = styled_entry(
                self.search_bar, self.replace_var,
            )
            replace_frame.grid(row=0, column=3, sticky="ew")
        else:
            self.replace_entry = None

        controls = styled_frame(self.search_bar)
        controls.grid(
            row=0, column=4 if replace_enabled else 2,
            sticky="e", padx=(5, 0),
        )
        self.status_label = tk.Label(
            controls, textvariable=self.match_status, width=8, anchor="e",
            bg=THEME["bg"], fg=THEME["fg_dim"], font=THEME["font_main"],
        )
        self.status_label.pack(side="left", padx=(0, 4))
        from ui.widgets import add_tooltip
        add_tooltip(self.status_label, "当前匹配位置 / 全部非重叠匹配数量")

        modes = tk.Frame(
            controls, bg=THEME["bg"], bd=0,
            highlightthickness=1, highlightbackground=THEME["border_light"],
            padx=1, pady=1,
        )
        modes.pack(side="left", padx=(2, 0))
        self.mode_buttons = []
        for icon, label, variable in (
            ("case", "区分大小写", self.case_sensitive),
            ("whole", "全词匹配", self.whole_word),
            ("wildcard", "通配符 * 和 ?", self.wildcard),
        ):
            button = linear_icon_button(
                modes, icon,
                lambda selected=variable: self.toggle_mode(selected), label,
            )
            button.pack(side="left", padx=(1, 0))
            self.mode_buttons.append((button, label, variable))
        self.refresh_mode_buttons()

        actions = tk.Frame(
            controls, bg=THEME["bg"], bd=0,
            highlightthickness=1, highlightbackground=THEME["border_light"],
            padx=1, pady=1,
        )
        actions.pack(side="left", padx=(4, 0))
        definitions = [
            ("previous", "上一处（Shift+Enter）", self.find_prev),
            ("next", "下一处（Enter）", self.find_next),
        ]
        if replace_enabled:
            definitions.extend((
                ("replace", "替换当前命中；替换正文按字面插入", self.replace_current),
                ("replace_all", "全部替换；替换正文按字面插入", self.replace_all),
            ))
        definitions.append(("close", "关闭查找替换（Esc）", self.hide_search_bar))
        self.action_buttons = []
        for icon, description, command in definitions:
            button = linear_icon_button(actions, icon, command, description)
            button.pack(side="left", padx=(1, 0))
            self.action_buttons.append(button)
        self.find_var.trace_add("write", self.on_find_text_changed)

    def refresh_mode_buttons(self):
        for button, label, variable in self.mode_buttons:
            set_mode_button_state(button, label, variable.get())

    def toggle_mode(self, variable):
        variable.set(not variable.get())
        self.refresh_mode_buttons()
        self.refresh_matches(relocate=True)

    def on_boundaries_changed(self, event=None):
        if not self._destroyed and self.whole_word.get():
            self.refresh_matches(relocate=True)

    def configure_text_widget(self):
        if not self.readonly:
            self.text_widget.configure(
                undo=True, maxundo=-1, autoseparators=True,
            )
        self.text_widget.tag_configure("search_match", background="#fff2a8")
        self.text_widget.tag_configure(
            "search_current", background="#ff9f43", foreground="#000000",
        )
        self.text_widget.tag_raise("search_match")
        self.text_widget.tag_raise("search_current")
        self._search_python_command = self.text_widget._register(
            self.on_text_command,
        )
        self._search_trace_command = self.text_widget._w + "_zipatch_search_trace"
        self.text_widget.tk.call(
            "proc", self._search_trace_command, "args",
            f"set response [{self._search_python_command} {{*}}$args]\n"
            'if {$response ne "ZIPATCH_SEARCH_OBSERVED"} {\n'
            '    return -code error "查找命中监听失败；详见 Python 回调错误"\n'
            "}\n"
            "return",
        )
        self.text_widget.tk.call(
            "trace", "add", "execution", self.text_widget._w, "enter",
            self._search_trace_command,
        )
        self.text_widget.bind("<Destroy>", self.on_text_destroyed, add="+")

    def _current_offsets(self):
        if (
            self.current_index is not None
            and self.current_index < len(self.matches.ranges)
        ):
            return self.matches.ranges[self.current_index]
        return None

    def invalidate_current_match(self):
        self.current_index = None

        self.clear_search_highlight()
        self.update_match_status()

    def on_text_command(self, command, operation):
        arguments = self.text_widget.tk.splitlist(command)
        if (
            len(arguments) >= 5
            and arguments[1:4] == ("mark", "set", "insert")
            and not self._moving_search_cursor
            and self.text_widget.index(arguments[4])
            != self.text_widget.index("insert")
        ):
            # 鼠标、方向键及其他组件通过原生insert标记移动光标。
            # 外部新位置优先于旧命中；总数及全部高亮继续消费原结果。
            self.current_index = None
            self._refresh_anchor = None

            self.text_widget.tag_remove("search_current", "1.0", "end")
            if not self._source_dirty:
                self.update_match_status()
        if len(arguments) >= 2:
            subcommand = arguments[1]
            if subcommand in ("insert", "delete", "replace") or (
                subcommand == "edit" and len(arguments) > 2
                and arguments[2] in ("undo", "redo")
            ):
                if self._refresh_after_id is None:
                    self._refresh_anchor = self._current_offsets()
                self._source_dirty = True
                self.matches = SearchResult()
                self.invalidate_current_match()
                # 正文编辑只使派生结果失效，不取得光标或滚动所有权。
                # 隐藏查找及空查询不读取全文；下次实际查找时再更新快照。
                if self.find_var.get() and self.search_bar.winfo_manager():
                    self.match_status.set("查找中…")
                    if self._refresh_after_id is None:
                        self._refresh_after_id = self.text_widget.after_idle(
                            self.finish_scheduled_refresh,
                        )
        return "ZIPATCH_SEARCH_OBSERVED"

    def finish_scheduled_refresh(self):
        self._refresh_after_id = None
        if self.find_var.get() and self.search_bar.winfo_manager():
            self.refresh_matches()

    def _read_search_source(self):
        self._source = self.text_widget.get("1.0", "end-1c")
        self._line_starts = (
            0, *(index + 1 for index, character in enumerate(self._source)
                 if character == "\n"),
        )
        self._supplementary_positions = (
            tuple(index for index, character in enumerate(self._source)
                  if ord(character) > 0xFFFF)
            if self._tk_uses_surrogates else ()
        )
        self._source_dirty = False

    def _tk_index(self, offset):
        line = bisect_right(self._line_starts, offset)
        start = self._line_starts[line - 1]
        column = offset - start
        column += (
            bisect_left(self._supplementary_positions, offset)
            - bisect_left(self._supplementary_positions, start)
        )
        return self.text_widget.index(f"{line}.{column}")

    def refresh_matches(self, *, relocate=False):
        if self._destroyed:
            return
        if self._refresh_after_id is not None:
            self.text_widget.after_cancel(self._refresh_after_id)
            self._refresh_after_id = None
        anchor = self._current_offsets() or self._refresh_anchor
        self._refresh_anchor = None
        query = self.find_var.get()
        if query and self._source_dirty:
            self._read_search_source()
        self.matches = find_text_matches(
            self._source, query,
            case_sensitive=self.case_sensitive.get(),
            whole_word=self.whole_word.get(),
            wildcard=self.wildcard.get(),
            boundary_codepoints=self.commits.manager.config_data[
                "search"
            ]["boundary_codepoints"],
        )
        self.current_index = None

        self.clear_search_highlight()
        if self.search_bar.winfo_manager():
            for start, end in self.matches.ranges:
                self.text_widget.tag_add(
                    "search_match", self._tk_index(start), self._tk_index(end),
                )
        if anchor is not None and self.matches.ranges:
            if anchor in self.matches.ranges:
                self.current_index = self.matches.ranges.index(anchor)
            elif relocate:
                self.current_index = next(
                    (index for index, (start, _) in enumerate(self.matches.ranges)
                     if start >= anchor[0]),
                    0,
                )
        self._display_current(move=relocate and self.current_index is not None)
        self.update_match_status()

    def _display_current(self, *, move=False):
        self.text_widget.tag_remove("search_current", "1.0", "end")
        offsets = self._current_offsets()
        if offsets is None:

            return
        start, end = map(self._tk_index, offsets)

        if self.search_bar.winfo_manager():
            self.text_widget.tag_add("search_current", start, end)
            self.text_widget.tag_raise("search_current")
            self.text_widget.tag_raise("sel")
        if move and self.search_bar.winfo_manager():
            self._moving_search_cursor = True
            try:
                self.text_widget.mark_set("insert", end)
            finally:
                self._moving_search_cursor = False
            self.text_widget.see(start)

    def update_match_status(self):
        query = self.find_var.get()
        if not query:
            description, warning = "—", False
            detail = "查询为空"
        elif self.matches.issue:
            description, warning = "查询不完整", True
            detail = self.matches.issue
        elif not self.matches.ranges:
            description, warning = "无匹配", True
            detail = "当前正文没有匹配；不会移动光标或执行替换"
        else:
            number = 0 if self.current_index is None else self.current_index + 1
            description = f"{number} / {len(self.matches.ranges)}"
            warning = False
            detail = "当前匹配位置 / 全部非重叠匹配数量"
        self.match_status.set(description)
        self.status_label.configure(
            fg=THEME["warning"] if warning else THEME["fg_dim"],
        )
        self.status_label._tooltip.text = detail

    def on_text_destroyed(self, event):
        if event.widget is not self.text_widget:
            return
        self._destroyed = True
        if self._refresh_after_id is not None:
            self.text_widget.after_cancel(self._refresh_after_id)
            self._refresh_after_id = None
        self.text_widget._root().unbind(
            "<<SearchBoundariesChanged>>", self._boundary_binding,
        )
        callback = self._search_trace_command
        if callback is None:
            return
        editing = getattr(self.text_widget, "_list_text_editing", None)
        command = (
            editing.original_command if editing is not None else self.text_widget._w
        )
        if self.text_widget.tk.call("info", "commands", command):
            self.text_widget.tk.call(
                "trace", "remove", "execution", command, "enter", callback,
            )
        self.text_widget.tk.call("rename", callback, "")
        self.text_widget.deletecommand(self._search_python_command)
        self._search_trace_command = None
        self._search_python_command = None
        self._source = ""
        self.matches = SearchResult()

    def current_match_range(self, keyword):
        if self._source_dirty or keyword != self.find_var.get():
            return None
        offsets = self._current_offsets()
        if offsets is None:
            return None
        start, end = map(self._tk_index, offsets)
        if self.text_widget.get(start, end) != self._source[offsets[0]:offsets[1]]:
            return None
        return start, end

    def bind_shortcuts(self):
        if self.enable_undo and not self.readonly:
            self.text_widget.bind("<Control-z>", self.undo)
            self.text_widget.bind("<Control-Z>", self.undo)
            self.text_widget.bind("<Control-y>", self.redo)
            self.text_widget.bind("<Control-Y>", self.redo)

        if self.enable_find:
            self.text_widget.bind("<Control-f>", self.open_find)
            self.text_widget.bind("<Control-F>", self.open_find)

        if self.enable_replace and not self.readonly:
            self.text_widget.bind("<Control-h>", self.open_replace)
            self.text_widget.bind("<Control-H>", self.open_replace)

        self.text_widget.bind("<Escape>", self.hide_search_bar)

        self.find_entry.bind("<Return>", self.find_next)
        self.find_entry.bind("<Shift-Return>", self.find_prev)
        self.find_entry.bind("<Escape>", self.hide_search_bar)

        if self.replace_entry is not None:
            self.replace_entry.bind("<Return>", self.replace_current)
            self.replace_entry.bind("<Shift-Return>", self.find_prev)
            self.replace_entry.bind("<Escape>", self.hide_search_bar)

    def content_completed(self, prepared_text=None):
        if self.on_content_changed is not None:
            self.on_content_changed(prepared_text)

    def undo(self, event=None):
        if self.readonly or str(self.text_widget.cget("state")) != "normal":
            return "break"
        try:
            self.text_widget.edit_undo()
        except TclError as error:
            if "nothing to undo" not in str(error).lower():
                raise
        else:
            self.content_completed()
        return "break"

    def redo(self, event=None):
        if self.readonly or str(self.text_widget.cget("state")) != "normal":
            return "break"
        try:
            self.text_widget.edit_redo()
        except TclError as error:
            if "nothing to redo" not in str(error).lower():
                raise
        else:
            self.content_completed()
        return "break"

    def clear_search_highlight(self):
        self.text_widget.tag_remove("search_match", "1.0", "end")
        self.text_widget.tag_remove("search_current", "1.0", "end")

    def highlight_all_matches(self):
        self.refresh_matches()

    def show_search_bar(self, focus_replace=False):
        if not self.search_bar.winfo_manager():
            self.search_bar.pack(
                fill="x", pady=(0, 6), before=self.before_text_frame,
            )
        if focus_replace and self.replace_entry is not None:
            self.replace_entry.focus_set()
            self.replace_entry.select_range(0, "end")
        else:
            self.find_entry.focus_set()
            self.find_entry.select_range(0, "end")
        self.refresh_matches()

    def hide_search_bar(self, event=None):
        if self.search_bar.winfo_manager():
            self.invalidate_current_match()
            self.search_bar.pack_forget()
            self.text_widget.focus_set()
            return "break"
        return None

    def _ensure_matches(self):
        if self._source_dirty or self._refresh_after_id is not None:
            self.refresh_matches()

    def _navigate(self, direction):
        self._ensure_matches()
        ranges = self.matches.ranges
        if not ranges:
            self.update_match_status()
            return "break"
        if self.current_index is not None:
            self.current_index = (self.current_index + direction) % len(ranges)
        else:
            position = min(
                len(self._source), len(self.text_widget.get("1.0", "insert")),
            )
            if direction > 0:
                self.current_index = next(
                    (index for index, (start, _) in enumerate(ranges)
                     if start >= position),
                    0,
                )
            else:
                self.current_index = next(
                    (index for index in range(len(ranges) - 1, -1, -1)
                     if ranges[index][0] < position),
                    len(ranges) - 1,
                )
        self._display_current(move=True)
        self.update_match_status()
        return "break"

    def find_next(self, event=None):
        return self._navigate(1)

    def find_prev(self, event=None):
        return self._navigate(-1)

    def replace_current(self, event=None):
        if self.readonly or str(self.text_widget.cget("state")) != "normal":
            return "break"
        self._ensure_matches()
        if not self.matches.ranges:
            return "break"
        keyword = self.find_var.get()
        located = self.current_match_range(keyword)
        if located is None:
            self.find_next()
            located = self.current_match_range(keyword)
            if located is None:
                return "break"
        start, end = located
        replacement = self.replace_var.get()
        if self.text_widget.get(start, end) == replacement:
            return self.find_next()

        scope = getattr(self.text_widget, "_edit_text_scope", None)
        with scope() if scope is not None else nullcontext():
            automatic = self.text_widget.cget("autoseparators")
            self.text_widget.edit_separator()
            self.text_widget.configure(autoseparators=False)
            try:
                self.text_widget.replace(start, end, replacement)
            finally:
                self.text_widget.edit_separator()
                self.text_widget.configure(autoseparators=automatic)

        self.content_completed()
        # 发布和原有完成回调之后，从实际新正文重新匹配。
        self._refresh_anchor = None
        self.refresh_matches()
        return self.find_next()

    def replace_all(self, event=None):
        if self.readonly or str(self.text_widget.cget("state")) != "normal":
            return "break"
        self._ensure_matches()
        if not self.matches.ranges:
            return "break"
        final_content = compose_search_replacement(
            self._source, self.matches.ranges, self.replace_var.get(),
        )
        if final_content == self._source:
            self.refresh_matches()
            return "break"
        replace_text_keep_undo(self.text_widget, final_content)
        self.content_completed(final_content)
        self._refresh_anchor = None
        self.refresh_matches()
        return "break"

    def toggle_disabled_lines(self, event=None):
        """仅名单安装；逐物理行切换实际行首一个竖线。"""
        text = self.text_widget
        editing = getattr(text, "_list_text_editing", None)
        if (
            editing is None or self.readonly
            or str(text.cget("state")) != "normal"
        ):
            return "break"
        selection = text.tag_ranges("sel")
        if selection:
            first, last = map(text.index, selection)
            first_line = int(first.split(".")[0])
            last_line, column = map(int, last.split("."))
            if column == 0:
                last_line -= 1
        else:
            first_line = last_line = int(text.index("insert").split(".")[0])
        view = text.yview()[0]
        cursor = text._w + "_zipatch_cursor"
        start_mark = text._w + "_zipatch_selection_start"
        end_mark = text._w + "_zipatch_selection_end"
        text.mark_set(cursor, "insert")
        text.mark_gravity(cursor, "right")
        if selection:
            text.mark_set(start_mark, first)
            text.mark_gravity(start_mark, "left")
            text.mark_set(end_mark, last)
            text.mark_gravity(end_mark, "right")
        try:
            with editing.edit_group():
                for number in range(last_line, first_line - 1, -1):
                    start = f"{number}.0"
                    if text.get(start, f"{start}+1c") == "|":
                        text.delete(start, f"{start}+1c")
                    else:
                        text.insert(start, "|")
            text.mark_set("insert", cursor)
            if selection:
                text.tag_remove("sel", "1.0", "end")
                text.tag_add("sel", start_mark, end_mark)
            text.yview_moveto(view)
            self.content_completed()
        finally:
            text.mark_unset(cursor)
            if selection:
                text.mark_unset(start_mark, end_mark)
        return "break"

    def on_find_text_changed(self, *_):
        self.refresh_matches(relocate=True)

    def open_find(self, event=None):
        self.show_search_bar(focus_replace=False)
        return "break"

    def open_replace(self, event=None):
        if self.enable_replace and not self.readonly:
            self.show_search_bar(focus_replace=True)
        else:
            self.show_search_bar(focus_replace=False)
        return "break"
