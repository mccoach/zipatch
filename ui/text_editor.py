# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import TclError

from core.constants import THEME
from core.text_io import get_text_value, replace_text_keep_undo
from ui.theme import styled_frame, styled_entry, styled_button


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
        tk.Label(
            self.search_bar,
            text="查找",
            bg=THEME["bg"],
            fg=THEME["fg_label"],
            font=THEME["font_main"],
        ).pack(side="left", padx=(0, 4))

        find_entry_frame, self.find_entry = styled_entry(
            self.search_bar,
            self.find_var,
        )
        find_entry_frame.pack(side="left", fill="x", expand=True, padx=(0, 6))

        if self.enable_replace and not self.readonly:
            tk.Label(
                self.search_bar,
                text="替换为",
                bg=THEME["bg"],
                fg=THEME["fg_label"],
                font=THEME["font_main"],
            ).pack(side="left", padx=(0, 4))

            replace_entry_frame, self.replace_entry = styled_entry(
                self.search_bar,
                self.replace_var,
            )
            replace_entry_frame.pack(side="left", fill="x", expand=True, padx=(0, 6))
        else:
            self.replace_entry = None

        styled_button(self.search_bar, "上一个", self.find_prev, width=7).pack(
            side="left",
            padx=(0, 4),
        )
        styled_button(self.search_bar, "下一个", self.find_next, width=7).pack(
            side="left",
            padx=(0, 4),
        )

        if self.enable_replace and not self.readonly:
            styled_button(self.search_bar, "替换", self.replace_current, width=7).pack(
                side="left",
                padx=(0, 4),
            )
            styled_button(self.search_bar, "全部替换", self.replace_all, width=9).pack(
                side="left",
                padx=(0, 4),
            )

        styled_button(self.search_bar, "关闭", self.hide_search_bar, width=7).pack(
            side="left",
        )

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
        try:
            self.text_widget.edit_undo()
        except TclError as error:
            if "nothing to undo" not in str(error).lower():
                raise
        else:
            self.content_completed()
        return "break"

    def redo(self, event=None):
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
            self.clear_search_highlight()
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
        self.search_state["current_start"] = index

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
        self.search_state["current_start"] = index

        return "break"

    def replace_current(self, event=None):
        if self.readonly:
            return "break"

        keyword = self.find_var.get()

        if not keyword:
            return "break"

        ranges = self.text_widget.tag_ranges("search_current")

        if not ranges:
            return self.find_next()

        start, end = ranges[0], ranges[1]
        replacement = self.replace_var.get()

        if self.text_widget.get(start, end) == replacement:
            return self.find_next()

        self.text_widget.edit_separator()
        self.text_widget.delete(start, end)
        self.text_widget.insert(start, replacement)
        self.text_widget.edit_separator()

        self.search_state["current_start"] = None
        self.highlight_all_matches()
        self.content_completed()
        return self.find_next()

    def replace_all(self, event=None):
        if self.readonly:
            return "break"

        keyword = self.find_var.get()
        replacement = self.replace_var.get()

        if not keyword:
            return "break"

        binding = getattr(self.text_widget, "_config_binding", None)
        content = (
            binding.read_current_value()
            if binding is not None else get_text_value(self.text_widget)
        )
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

    def on_find_text_changed(self, *_):
        self.search_state["current_start"] = None
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
