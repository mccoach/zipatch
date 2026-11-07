# -*- coding: utf-8 -*-

import tkinter as tk
from contextlib import nullcontext
from tkinter import TclError

from core.constants import THEME
from ui.theme import styled_frame, styled_entry, styled_button
from ui.text_layout import bind_allocated_text_wrap


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
        parent,
        toolbar_parent,
        text_widget,
        before_text_frame,
        enable_find=True,
        enable_replace=True,
        enable_undo=True,
        readonly=False,
        on_content_changed=None,
    ):
        self.parent = parent
        self.toolbar_parent = toolbar_parent
        self.text_widget = text_widget
        self.before_text_frame = before_text_frame
        self.enable_find = enable_find
        self.enable_replace = enable_replace
        self.enable_undo = enable_undo
        self.readonly = readonly
        self.on_content_changed = on_content_changed

        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.search_state = {"current_start": None}

        self.search_bar = styled_frame(toolbar_parent)
        self.search_bar.pack(fill="x", pady=(0, 6), before=before_text_frame)
        self.search_bar.pack_forget()

        self.create_search_bar()
        self.configure_text_widget()
        self.bind_shortcuts()

    def create_search_bar(self):
        """一行等宽输入和紧凑符号动作，不改变正文及查找算法。"""
        # 创建控件时模块导入已完成；避免编辑器与widgets的顶层循环依赖。
        from ui.widgets import add_tooltip

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

        actions = styled_frame(self.search_bar)
        actions.grid(
            row=0, column=4 if replace_enabled else 2,
            sticky="e", padx=(4, 0),
        )
        definitions = [
            ("↑", "上一处（Shift+Enter）", self.find_prev),
            ("↓", "下一处（Enter）", self.find_next),
        ]
        if replace_enabled:
            definitions.extend((
                ("→", "替换当前命中", self.replace_current),
                ("⇒", "全部替换", self.replace_all),
            ))
        definitions.append(("×", "关闭查找替换（Esc）", self.hide_search_bar))
        for symbol, description, command in definitions:
            button = styled_button(actions, symbol, command, width=2)
            button.configure(padx=2, pady=1)
            button.pack(side="left", padx=(1, 0))
            add_tooltip(button, description)

        self.find_var.trace_add("write", self.on_find_text_changed)

    def configure_text_widget(self):
        if not self.readonly:
            self.text_widget.configure(
                undo=True,
                maxundo=-1,
                autoseparators=True,
            )

        self.text_widget.tag_configure("search_match", background="#fff2a8")
        self.text_widget.tag_configure(
            "search_current",
            background="#ff9f43",
            foreground="#000000",
        )
        self.text_widget.tag_raise("search_match")
        self.text_widget.tag_raise("search_current")

        # 执行监听随原生 Text 命令的 rename 保留。
        # 不读取全文、不复制名单修订，也不消费 Modified 标记。
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

    def invalidate_current_match(self):
        """正文修改尝试先撤销旧命中；标签只承担派生显示。"""
        self.search_state["current_start"] = None
        self.search_state["keyword"] = None
        self.clear_search_highlight()

    def on_text_command(self, command, operation):
        arguments = self.text_widget.tk.splitlist(command)
        if len(arguments) >= 2:
            subcommand = arguments[1]
            if subcommand in ("insert", "delete", "replace") or (
                subcommand == "edit"
                and len(arguments) > 2
                and arguments[2] in ("undo", "redo")
            ):
                self.invalidate_current_match()
        # Tkinter 报告 Python 缺陷后不会返回此值。
        # Tcl 监听据此拒绝继续原修改命令，不将失败当成正常完成。
        return "ZIPATCH_SEARCH_OBSERVED"

    def on_text_destroyed(self, event):
        if event.widget is not self.text_widget:
            return
        callback = self._search_trace_command
        if callback is None:
            return
        editing = getattr(self.text_widget, "_list_text_editing", None)
        command = (
            editing.original_command
            if editing is not None else self.text_widget._w
        )
        # 原生命令已销毁时，其执行监听也已释放；不猜测异常消息。
        if self.text_widget.tk.call("info", "commands", command):
            self.text_widget.tk.call(
                "trace", "remove", "execution", command, "enter", callback,
            )
        self.text_widget.tk.call("rename", callback, "")
        self.text_widget.deletecommand(self._search_python_command)
        self._search_trace_command = None
        self._search_python_command = None

    def current_match_range(self, keyword):
        """命中须对应当前条件、记录位置、标签边界及实际正文。"""
        recorded = self.search_state["current_start"]
        if not recorded or self.search_state.get("keyword") != keyword:
            return None
        ranges = self.text_widget.tag_ranges("search_current")
        if len(ranges) != 2:
            return None
        start, end = map(self.text_widget.index, ranges)
        expected_end = self.text_widget.index(f"{recorded}+{len(keyword)}c")
        if start != recorded or end != expected_end:
            return None
        if self.text_widget.get(start, end) != keyword:
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
        self.text_widget.tag_remove("search_match", "1.0", "end")
        keyword = self.find_var.get()

        if not keyword:
            return

        start = "1.0"

        while True:
            index = self.text_widget.search(keyword, start, stopindex="end")

            if not index:
                break

            end = f"{index}+{len(keyword)}c"
            self.text_widget.tag_add("search_match", index, end)
            start = end

    def show_search_bar(self, focus_replace=False):
        if not self.search_bar.winfo_ismapped():
            self.search_bar.pack(
                fill="x",
                pady=(0, 6),
                before=self.before_text_frame,
            )

        if focus_replace and self.replace_entry is not None:
            self.replace_entry.focus_set()
            self.replace_entry.select_range(0, "end")
        else:
            self.find_entry.focus_set()
            self.find_entry.select_range(0, "end")

        self.highlight_all_matches()

    def hide_search_bar(self, event=None):
        if self.search_bar.winfo_ismapped():
            self.invalidate_current_match()
            self.search_bar.pack_forget()
            self.text_widget.focus_set()
            return "break"

        return None

    def find_next(self, event=None):
        keyword = self.find_var.get()

        if not keyword:
            return "break"

        self.highlight_all_matches()
        self.text_widget.tag_remove("search_current", "1.0", "end")

        start = self.text_widget.index("insert")

        if self.search_state["current_start"]:
            start = f"{self.search_state['current_start']}+1c"

        index = self.text_widget.search(keyword, start, stopindex="end")

        if not index:
            index = self.text_widget.search(keyword, "1.0", stopindex="end")

        if not index:
            self.search_state["current_start"] = None
            return "break"

        end = f"{index}+{len(keyword)}c"
        self.text_widget.tag_add("search_current", index, end)
        self.text_widget.mark_set("insert", end)
        self.text_widget.see(index)
        self.search_state["current_start"] = self.text_widget.index(index)
        self.search_state["keyword"] = keyword

        return "break"

    def find_prev(self, event=None):
        keyword = self.find_var.get()

        if not keyword:
            return "break"

        self.highlight_all_matches()
        self.text_widget.tag_remove("search_current", "1.0", "end")

        start = self.text_widget.index("insert")

        if self.search_state["current_start"]:
            start = self.search_state["current_start"]

        index = self.text_widget.search(
            keyword,
            start,
            stopindex="1.0",
            backwards=True,
        )

        if not index:
            index = self.text_widget.search(
                keyword,
                "end",
                stopindex="1.0",
                backwards=True,
            )

        if not index:
            self.search_state["current_start"] = None
            return "break"

        end = f"{index}+{len(keyword)}c"
        self.text_widget.tag_add("search_current", index, end)
        self.text_widget.mark_set("insert", end)
        self.text_widget.see(index)
        self.search_state["current_start"] = self.text_widget.index(index)
        self.search_state["keyword"] = keyword

        return "break"

    def replace_current(self, event=None):
        if self.readonly or str(self.text_widget.cget("state")) != "normal":
            return "break"

        keyword = self.find_var.get()
        if not keyword:
            self.invalidate_current_match()
            return "break"

        located = self.current_match_range(keyword)
        if located is None:
            self.invalidate_current_match()
            self.find_next()
            located = self.current_match_range(keyword)
            if located is None:
                return "break"

        start, end = located
        replacement = self.replace_var.get()
        if keyword == replacement:
            return self.find_next()

        # 原生修改开始前同步撤销旧命中。
        # 本次修改只消费刚核验的局部范围，不再从漂移标签获取位置。
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
        # 保留连续替换交互；下一项是新正文上重新查找形成的命中。
        return self.find_next()

    def replace_all(self, event=None):
        if self.readonly or str(self.text_widget.cget("state")) != "normal":
            return "break"

        keyword = self.find_var.get()
        replacement = self.replace_var.get()

        if not keyword:
            return "break"

        binding = getattr(self.text_widget, "_config_binding", None)
        editing = getattr(self.text_widget, "_list_text_editing", None)
        if editing is not None:
            # 名单消费已收口的完整正文；全部替换不负责删除物理空行。
            content = editing.complete()
        elif binding is not None:
            # 配置绑定统一拥有读取契约及观测，已支持真实尾换行保留。
            content = binding.read_current_value()
        elif getattr(self.text_widget, "_preserve_trailing_newlines", False):
            content = self.text_widget.get("1.0", "end-1c")
        else:
            content = get_text_value(self.text_widget)
        count = content.count(keyword)

        if count <= 0 or keyword == replacement:
            self.highlight_all_matches()
            return "break"

        final_content = content.replace(keyword, replacement)
        replace_text_keep_undo(self.text_widget, final_content)

        self.search_state["current_start"] = None
        self.highlight_all_matches()

        self.content_completed(final_content)
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
        self.invalidate_current_match()
        self.highlight_all_matches()

    def open_find(self, event=None):
        self.show_search_bar(focus_replace=False)
        return "break"

    def open_replace(self, event=None):
        if self.enable_replace and not self.readonly:
            self.show_search_bar(focus_replace=True)
        else:
            self.show_search_bar(focus_replace=False)
        return "break"
