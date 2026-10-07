# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from ui.window_manager import register_popup, unregister_popup, is_descendant_or_self


class EntryHistoryPlugin:
    """历史浮窗只负责选择和删除；字段接受归 EditorBinding。"""

    def __init__(self, parent, entry, text_var, binding, commits, history_key):
        self.parent = parent
        self.entry = entry
        self.text_var = text_var
        self.binding = binding
        self.commits = commits
        self.history_key = history_key
        self.popup = None
        self._close_after_id = None
        entry.bind("<FocusIn>", self.show_popup, add="+")
        entry.bind("<FocusOut>", self.on_focus_out, add="+")
        entry.bind("<Down>", self.show_popup, add="+")
        entry.bind("<Escape>", self.close_popup, add="+")
        entry.bind("<Destroy>", self.on_destroy, add="+")

    def history_items(self):
        return self.commits.manager.config_data["entry_history"].get(self.history_key, [])

    def on_focus_out(self, event=None):
        if self._close_after_id is not None:
            self.entry.after_cancel(self._close_after_id)
        # 此延迟仅控制浮窗关闭，绝不决定配置提交时机。
        self._close_after_id = self.entry.after(160, self.close_if_outside)

    def close_if_outside(self):
        self._close_after_id = None
        focused = self.entry.focus_get()
        if focused is not self.entry and not is_descendant_or_self(focused, self.popup):
            self.close_popup()

    def show_popup(self, event=None):
        self.close_popup()
        values = self.history_items()
        if not values:
            return
        popup = tk.Toplevel(self.parent)
        self.popup = popup
        popup.configure(bg=THEME["border"], padx=1, pady=1)
        popup.overrideredirect(True)
        popup.resizable(False, True)
        body = tk.Frame(popup, bg=THEME["bg"])
        body.pack(fill="both", expand=True)

        for value in values:
            row = tk.Frame(body, bg=THEME["bg"])
            row.pack(fill="x")
            tk.Button(
                row, text=value, anchor="w",
                command=lambda current=value: self.apply_history(current),
                bg=THEME["bg_input"], fg=THEME["fg"], relief="flat",
                font=THEME["font_main"], padx=8, pady=4,
            ).pack(side="left", fill="x", expand=True)
            tk.Button(
                row, text="✖", width=3,
                command=lambda current=value: self.delete_history(current),
                bg=THEME["bg_input"], fg=THEME["danger"], relief="flat",
            ).pack(side="right")

        popup.update_idletasks()
        x = self.entry.winfo_rootx() + 36
        y = self.entry.winfo_rooty() + self.entry.winfo_height() + 4
        width = max(360, self.entry.winfo_width() - 36)
        height = min(220, popup.winfo_reqheight())
        width = min(width, max(260, self.entry.winfo_screenwidth() - x - 8))
        if y + height > self.entry.winfo_screenheight() - 40:
            y = max(0, self.entry.winfo_rooty() - height - 4)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        register_popup(
            popup, self.close_popup, focus_on_register=False,
            close_on_focus_out=True, focus_guard_widgets=[self.entry],
        )

    def apply_history(self, value):
        self.text_var.set(value)
        self.commits.submit_editors([self.binding], self.entry.winfo_toplevel())
        self.close_popup()
        self.entry.focus_set()
        self.entry.icursor("end")

    def delete_history(self, value):
        self.binding.commit()
        histories = self.commits.manager.config_data["entry_history"]
        final = [item for item in histories.get(self.history_key, []) if item != value]
        self.commits.manager.accept(histories, {self.history_key: final})
        self.commits.finish(self.entry.winfo_toplevel())
        self.show_popup()
        self.entry.focus_set()

    def close_popup(self, event=None):
        if self.popup is not None:
            popup, self.popup = self.popup, None
            unregister_popup(popup)
            if popup.winfo_exists():
                popup.destroy()
        return "break" if event is not None else True

    def on_destroy(self, event=None):
        if event.widget is not self.entry:
            return
        if self._close_after_id is not None:
            self.entry.after_cancel(self._close_after_id)
            self._close_after_id = None
        self.close_popup()