# -*- coding: utf-8 -*-

"""路径历史主动展开、紧凑过滤及选择；字段接受仍归EditorBinding。"""

import tkinter as tk

from core.constants import THEME
from ui.window_manager import (
    is_descendant_or_self, restore_editor_interaction,
)


HISTORY_VISIBLE_ROWS = 5


class EntryHistoryPlugin:
    """聚焦不展开；按钮、Down及Alt+Down是唯一打开入口。"""

    def __init__(self, parent, entry, text_var, binding, commits, history_key):
        self.parent = parent
        self.entry = entry
        self.text_var = text_var
        self.binding = binding
        self.commits = commits
        self.history_key = history_key
        self.popup = None
        self.listbox = None
        self.delete_button = None
        self.visible_values = ()
        self.filter_text = ""
        self._destroyed = False

        self.button = tk.Button(
            entry.master, text="▾", width=2,
            command=self.toggle_popup,
            bg=THEME["bg_input"], fg=THEME["fg"],
            activebackground=THEME["bg_btn_hover"],
            activeforeground=THEME["fg_on_dark"],
            relief="flat", bd=0, highlightthickness=0,
            padx=3, pady=0, cursor="hand2",
            font=THEME["font_main"],
        )
        self.button.pack(side="right", fill="y")
        # 延迟导入避免widgets与历史插件的顶层循环依赖。
        from ui.widgets import add_tooltip
        add_tooltip(self.button, "路径历史（↓ / Alt+↓）；聚焦输入框不会自动展开")

        # 优先键盘标签只拦截本插件正在使用的按键；
        # 未展开时Return继续走原EditorBinding提交。
        # Tk把点号开头的bind标签解释为窗口路径。
        # 此标签属于插件，不是窗口；用固定前缀保留实例唯一性。
        self.key_tag = "ZipatchHistoryKeys:" + entry._w
        entry.bindtags((self.key_tag, *entry.bindtags()))
        # bind_class由根窗口注册Python回调；本插件记录其所有权，
        # 销毁时既解除Tcl绑定，也删除根窗口持有的回调命令。
        self._key_callbacks = {}
        for sequence, callback in (
            ("<Down>", self.open_from_keyboard),
            ("<Alt-Down>", self.open_from_keyboard),
            ("<Up>", self.select_previous),
            ("<Escape>", self.escape),
            ("<Return>", self.accept_from_entry),
        ):
            self._key_callbacks[sequence] = entry.bind_class(
                self.key_tag, sequence, callback,
            )
        self._trace_id = text_var.trace_add("write", self.on_text_changed)
        entry.bind("<ButtonRelease-1>", self.return_to_editor, add="+")
        entry.bind("<Unmap>", self.on_entry_hidden, add="+")
        entry.bind("<Destroy>", self.on_destroy, add="+")
        self._owner = entry.winfo_toplevel()
        # 顶层控件的事件标签接收其内部控件事件，不安装全局监听。
        # 插件只拥有自己的回调，销毁时精确解除。
        self._owner_bindings = {
            sequence: self._owner.bind(
                sequence, self.on_owner_interaction, add="+",
            )
            for sequence in ("<ButtonPress-1>", "<FocusIn>")
        }

    def history_items(self):
        return self.commits.manager.config_data["entry_history"].get(
            self.history_key, [],
        )

    def can_open(self):
        return (
            not self._destroyed
            and not self.commits.operation_depth
            and str(self.entry.cget("state")) == "normal"
        )

    def return_to_editor(self, event=None):
        """明确返回编辑；历史面板与输入框共用同一原生顶层窗口。"""
        if self.can_open():
            restore_editor_interaction(self.entry)
            if self.popup is not None:
                # Frame.lift只调整同窗口内部层级，不激活另一原生窗口。
                self.popup.lift()

    def on_owner_interaction(self, event):
        """离开本输入及其下拉区域时关闭，不改变新控件的焦点。"""
        if self.popup is None or self.commits.operation_depth:
            return
        if any(
            is_descendant_or_self(event.widget, region)
            for region in (self.entry, self.button, self.popup)
        ):
            return
        self.close_popup()

    def on_entry_hidden(self, event):
        if event.widget is self.entry:
            self.close_popup()

    def toggle_popup(self):
        if not self.can_open():
            return
        if self.popup is not None:
            self.close_popup()
            self.return_to_editor()
        else:
            self.show_popup()

    def move_selection(self, step):
        """列表是输入框的派生选择视图，不取得键盘输入所有权。"""
        if self.listbox is None or not self.visible_values:
            return
        selected = self.listbox.curselection()
        if selected:
            index = max(0, min(
                len(self.visible_values) - 1, selected[0] + step,
            ))
        else:
            index = 0 if step > 0 else len(self.visible_values) - 1
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)
        self.refresh_selection()

    def open_from_keyboard(self, event=None):
        if not self.can_open():
            return "break"
        if self.popup is None:
            self.show_popup()
        self.move_selection(1)
        return "break"

    def select_previous(self, event=None):
        if self.popup is None:
            return None
        if self.can_open():
            self.move_selection(-1)
        return "break"

    def escape(self, event=None):
        if self.popup is None:
            return None
        self.close_popup()
        self.return_to_editor()
        return "break"

    def accept_from_entry(self, event=None):
        if self.popup is None or self.listbox is None:
            return None
        selected = self.listbox.curselection()
        if not selected:
            return None
        return self.accept_selection()

    def on_text_changed(self, *_):
        if self.popup is not None:
            self.filter_text = self.text_var.get()
            self.refresh_popup()

    def selected_value(self):
        if self.listbox is None:
            return None
        selected = self.listbox.curselection()
        if not selected or selected[0] >= len(self.visible_values):
            return None
        return self.visible_values[selected[0]]

    def accept_selection(self, event=None):
        value = self.selected_value()
        if value is not None:
            self.apply_history(value)
        return "break"

    def select_clicked(self, event):
        if not self.visible_values:
            return "break"
        index = self.listbox.nearest(event.y)
        bounds = self.listbox.bbox(index)
        if bounds is None or not bounds[1] <= event.y < bounds[1] + bounds[3]:
            return "break"
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        return self.accept_selection()

    def refresh_selection(self, event=None):
        value = self.selected_value()
        if self.delete_button is not None:
            self.delete_button.configure(
                state="normal" if value is not None else "disabled",
            )
        if self.listbox is not None:
            self.listbox._tooltip.text = value or ""

    def update_hover_path(self, event):
        if not self.visible_values:
            self.listbox._tooltip.text = ""
            return
        index = self.listbox.nearest(event.y)
        self.listbox._tooltip.text = self.visible_values[index]

    def show_popup(self):
        if not self.can_open() or not self.history_items():
            return
        if self.popup is not None:
            return
        self.filter_text = ""
        owner = self.entry.winfo_toplevel()
        # 下拉是同一窗口内部的覆盖视图，不创建独立原生窗口。
        popup = tk.Frame(
            owner, bg=THEME["border"], padx=1, pady=1,
        )
        self.popup = popup
        popup.bind("<Destroy>", self.on_popup_destroyed, add="+")

        body = tk.Frame(popup, bg=THEME["bg_input"])
        body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            body, width=1, height=HISTORY_VISIBLE_ROWS,
            exportselection=False, activestyle="dotbox",
            bg=THEME["bg_input"], fg=THEME["fg"],
            selectbackground=THEME["select_bg"],
            selectforeground=THEME["select_fg"],
            font=THEME["font_main"], relief="flat",
            bd=0, highlightthickness=0,
        )
        scrollbar = tk.Scrollbar(
            body, orient="vertical", command=self.listbox.yview,
        )
        self.listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)

        footer = tk.Frame(popup, bg=THEME["bg"])
        footer.pack(fill="x")
        self.delete_button = tk.Button(
            footer, text="×", width=2, state="disabled",
            command=self.delete_selected_history,
            bg=THEME["bg"], fg=THEME["danger"],
            relief="flat", bd=0, cursor="hand2",
            font=THEME["font_main"],
        )
        self.delete_button.pack(side="right")
        self.hint = tk.Label(
            footer, text="", anchor="w", width=1,
            bg=THEME["bg"], fg=THEME["fg_dim"],
            font=THEME["font_main"], padx=5,
        )
        self.hint.pack(side="left", fill="x", expand=True)

        from ui.widgets import add_tooltip
        add_tooltip(self.delete_button, "删除选中历史，不删除实际文件")
        add_tooltip(self.listbox, "悬停查看完整路径")
        self.listbox._tooltip.text = ""
        self.listbox.bind("<Motion>", self.update_hover_path, add="+")
        self.listbox.bind("<<ListboxSelect>>", self.refresh_selection)
        self.listbox.bind("<ButtonRelease-1>", self.select_clicked)
        self.listbox.bind("<Return>", self.accept_selection)
        self.listbox.bind("<Escape>", self.escape)
        self.refresh_popup()

        self.return_to_editor()

    def refresh_popup(self):
        if self.popup is None:
            return
        previous = self.selected_value()
        query = self.filter_text.casefold()
        self.visible_values = tuple(
            value for value in self.history_items()
            if not query or query in value.casefold()
        )
        self.listbox.delete(0, "end")
        for value in self.visible_values:
            self.listbox.insert("end", value)
        if previous in self.visible_values:
            index = self.visible_values.index(previous)
            self.listbox.selection_set(index)
            self.listbox.activate(index)
        self.listbox.configure(
            height=max(1, min(HISTORY_VISIBLE_ROWS, len(self.visible_values))),
        )
        self.hint.configure(
            text="输入可筛选 · Esc关闭"
            if self.visible_values else "没有匹配历史 · Esc关闭",
        )
        self.refresh_selection()
        self.position_popup()

    def position_popup(self):
        popup = self.popup
        if popup is None:
            return
        popup.update_idletasks()
        owner = self._owner
        field = self.entry.master
        available_width = max(1, owner.winfo_width())
        available_height = max(1, owner.winfo_height())
        width = min(max(1, field.winfo_width()), available_width)
        height = min(popup.winfo_reqheight(), available_height)
        x = field.winfo_rootx() - owner.winfo_rootx()
        top = field.winfo_rooty() - owner.winfo_rooty()
        x = max(0, min(x, available_width - width))
        y = top + field.winfo_height() + 2
        if y + height > available_height:
            y = max(0, top - height - 2)
        popup.place(x=x, y=y, width=width, height=height)
        popup.lift()

    def apply_history(self, value):
        if not self.can_open():
            return
        # 先关闭，回填的变量通知不再刷新即将销毁的历史视图。
        self.close_popup()
        self.text_var.set(value)
        self.commits.submit_editors(
            [self.binding], self.entry.winfo_toplevel(),
        )
        self.entry.icursor("end")
        self.return_to_editor()

    def delete_selected_history(self):
        value = self.selected_value()
        if value is not None:
            self.delete_history(value)

    def delete_history(self, value):
        if not self.can_open():
            return
        editors = (
            self.commits.select(
                self.binding.page, {"code_header_line", "code_footer_line"},
            )
            if self.binding.page in ("merge", "restore")
            and self.binding.field in ("code_header_line", "code_footer_line")
            else [self.binding]
        )
        self.commits.accept_editors(editors)
        if any(editor.dirty for editor in editors):
            return
        histories = self.commits.manager.config_data["entry_history"]
        final = [
            previous for previous in histories.get(self.history_key, [])
            if previous != value
        ]
        self.commits.manager.accept(histories, {self.history_key: final})
        self.commits.finish(self.entry.winfo_toplevel())
        if not final:
            self.close_popup()
            self.return_to_editor()
        else:
            self.refresh_popup()

    def close_popup(self, event=None):
        popup, self.popup = self.popup, None
        self.listbox = None
        self.delete_button = None
        self.visible_values = ()
        if popup is not None:
            if popup.winfo_exists():
                popup.destroy()
        return "break" if event is not None else True

    def on_popup_destroyed(self, event):
        if event.widget is self.popup:
            self.popup = None
            self.listbox = None
            self.delete_button = None
            self.visible_values = ()

    def on_destroy(self, event=None):
        if event.widget is not self.entry or self._destroyed:
            return
        self._destroyed = True
        self.text_var.trace_remove("write", self._trace_id)
        root = self.entry._root()
        for sequence, callback_id in self._key_callbacks.items():
            self.entry.unbind_class(self.key_tag, sequence)
            root.deletecommand(callback_id)
        self._key_callbacks.clear()
        for sequence, callback_id in self._owner_bindings.items():
            self._owner.unbind(sequence, callback_id)
        self._owner_bindings.clear()
        self.close_popup()
