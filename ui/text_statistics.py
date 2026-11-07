# -*- coding: utf-8 -*-

"""两个源码Text输入框的字符与物理行信息，不提交配置。"""

import re
import tkinter as tk
from bisect import bisect_right

from core.constants import THEME


class TextStatistics:
    """正文变化更新一次快照，光标和选区只消费已有快照。

    标签固定单行及最小横向请求，使用标题栏剩余空间。
    不监听Entry，不让描述变化重新分配正文宽度或标题高度。
    """

    def __init__(self, widget, title_row):
        if not isinstance(widget, tk.Text):
            raise TypeError("源码统计只支持Text")
        self.widget = widget
        self.destroyed = False
        self.after_id = None
        self.content_dirty = True
        self.source = ""
        self.line_starts = (0,)
        self.tk_line_starts = (0,)
        self.label = tk.Label(
            title_row, text="", width=1, height=1, wraplength=0,
            bg=title_row.cget("bg"), fg=THEME["fg_dim"],
            font=THEME["font_mono"], anchor="e",
            padx=0, pady=0, borderwidth=0, highlightthickness=0,
        )
        # 操作按钮已创建；统计独占剩余可伸展空间，标题不参与平分。
        self.label.pack(
            side="right", fill="x", expand=True, padx=(6, 8),
        )
        widget._text_statistics = self

        self.python_command = widget._register(self.observe_command)
        self.trace_command = widget._w + "_zipatch_statistics_trace"
        widget.tk.call(
            "proc", self.trace_command, "args",
            f"set response [{self.python_command} {{*}}$args]\n"
            'if {$response ne "ZIPATCH_STATISTICS_OBSERVED"} {\n'
            '    return -code error "文本统计监听失败；详见Python回调错误"\n'
            "}\n"
            "return",
        )
        widget.tk.call(
            "trace", "add", "execution", widget._w, "enter",
            self.trace_command,
        )
        for sequence in (
            "<FocusIn>", "<FocusOut>", "<KeyRelease>",
            "<ButtonRelease-1>", "<B1-Motion>", "<<Selection>>",
        ):
            widget.bind(sequence, self.schedule, add="+")
        widget.bind("<Destroy>", self.on_destroy, add="+")
        self.refresh()

    def observe_command(self, command, operation):
        arguments = self.widget.tk.splitlist(command)
        if len(arguments) >= 2:
            kind = arguments[1]
            changed = kind in ("insert", "delete", "replace") or (
                kind == "edit" and len(arguments) > 2
                and arguments[2] in ("undo", "redo")
            )
            if changed:
                self.content_dirty = True
                self.schedule()
            elif (
                kind == "mark" and len(arguments) > 3
                and arguments[2] == "set" and arguments[3] == "insert"
                or kind == "tag" and len(arguments) > 3
                and arguments[2] in ("add", "remove")
                and arguments[3] == "sel"
            ):
                self.schedule()
        return "ZIPATCH_STATISTICS_OBSERVED"

    def schedule(self, event=None):
        if not self.destroyed and self.after_id is None:
            self.after_id = self.widget.after_idle(self.refresh)

    def _read_source(self):
        self.source = self.widget.get("1.0", "end-1c")
        self.line_starts = (
            0, *(match.end() for match in re.finditer(
                r"\r\n|\r|\n", self.source,
            )),
        )
        # Tk按LF提供行索引，物理行还包含单独CR。
        self.tk_line_starts = (
            0, *(match.end() for match in re.finditer("\n", self.source)),
        )
        self.content_dirty = False

    def _text_offset(self, index):
        normalized = self.widget.index(index)
        if self.widget.compare(normalized, ">", "end-1c"):
            normalized = self.widget.index("end-1c")
        number = int(normalized.split(".")[0])
        start = self.tk_line_starts[number - 1]
        return start + len(self.widget.get(f"{number}.0", normalized))

    def refresh(self):
        if self.destroyed:
            return
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        if self.content_dirty:
            self._read_source()
        total = len(self.source)
        lines = len(self.line_starts)
        selection = self.widget.tag_ranges("sel")
        if selection:
            start = self._text_offset(selection[0])
            end = self._text_offset(selection[1])
            first_line = bisect_right(self.line_starts, start)
            last_line = bisect_right(
                self.line_starts, max(start, end - 1),
            )
            description = (
                f"{first_line}–{last_line}/{lines} 行 ｜ "
                f"{end - start}/{total} 字符"
            )
        elif self.widget.focus_get() is self.widget:
            position = self._text_offset("insert")
            number = bisect_right(self.line_starts, position)
            description = f"{number}/{lines} 行 ｜ {total} 字符"
        else:
            description = f"{lines} 行 ｜ {total} 字符"
        self.label.configure(text=description)

    def on_destroy(self, event):
        if event.widget is not self.widget or self.destroyed:
            return
        self.destroyed = True
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        editing = getattr(self.widget, "_list_text_editing", None)
        command = (
            editing.original_command
            if editing is not None else self.widget._w
        )
        if self.widget.tk.call("info", "commands", command):
            self.widget.tk.call(
                "trace", "remove", "execution", command, "enter",
                self.trace_command,
            )
        self.widget.tk.call("rename", self.trace_command, "")
        self.widget.deletecommand(self.python_command)
        self.widget._text_statistics = None
        self.source = ""
        self.line_starts = self.tk_line_starts = ()
