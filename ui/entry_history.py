# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from ui.window_manager import register_popup, unregister_popup, is_descendant_or_self


DEFAULT_ENTRY_HISTORY_MAX_ITEMS = 10


def normalize_history_item(value):
    return (value or "").strip()


def get_entry_history_max_items(config_data):
    if not isinstance(config_data, dict):
        return DEFAULT_ENTRY_HISTORY_MAX_ITEMS

    settings = config_data.setdefault("settings", {})
    value = settings.get("entry_history_max_items", DEFAULT_ENTRY_HISTORY_MAX_ITEMS)

    try:
        value = int(value)
    except Exception:
        value = DEFAULT_ENTRY_HISTORY_MAX_ITEMS

    if value < 1:
        value = DEFAULT_ENTRY_HISTORY_MAX_ITEMS

    settings["entry_history_max_items"] = value
    return value


def get_history_list(config_data, history_key):
    if not config_data or not history_key:
        return []

    config_data.setdefault("entry_history", {})
    history = config_data["entry_history"].setdefault(history_key, [])

    if not isinstance(history, list):
        history = []
        config_data["entry_history"][history_key] = history

    return history


def save_entry_history(config_data, history_key, value, save_config=None, value_normalizer=None):
    normalizer = value_normalizer or normalize_history_item
    value = normalizer(value)

    if not value or not config_data or not history_key:
        return

    history = get_history_list(config_data, history_key)

    max_items = get_entry_history_max_items(config_data)

    history = [
        item
        for item in history
        if item != value
    ]

    history.insert(0, value)
    history = history[:max_items]

    config_data["entry_history"][history_key] = history

    if save_config:
        save_config()


def delete_entry_history(config_data, history_key, value, save_config=None):
    if not config_data or not history_key:
        return

    history = get_history_list(config_data, history_key)
    config_data["entry_history"][history_key] = [
        item
        for item in history
        if item != value
    ]

    if save_config:
        save_config()


class EntryHistoryPlugin:
    """
    单行输入框历史插件。

    行业常见规则：
    - 输入框获得焦点时，若有历史，向下显示候选浮层；
    - 浮层不抢焦点，焦点继续留在输入框，用户可继续输入；
    - 点击历史项回填并关闭；
    - 点击删除按钮只删除该项；
    - 输入框失焦后延迟关闭，给鼠标点击历史项留出时间；
    - 按 Enter、失焦、浏览选择后保存当前值；
    - Esc 关闭浮层；
    - 每个输入框独立 history_key。
    """

    def __init__(
        self,
        parent,
        entry,
        text_var,
        config_data=None,
        history_key=None,
        save_config=None,
        enabled=True,
        max_items=None,
        auto_seed_current=True,
        close_delay_ms=160,
        value_normalizer=None,
    ):
        self.parent = parent
        self.entry = entry
        self.text_var = text_var
        self.config_data = config_data
        self.history_key = history_key
        self.save_config = save_config
        self.enabled = enabled
        self.max_items = max_items
        self.auto_seed_current = auto_seed_current
        self.close_delay_ms = close_delay_ms
        self.value_normalizer = value_normalizer or normalize_history_item
        self.popup = None

        if not self.enabled or not self.config_data or not self.history_key:
            return

        if self.auto_seed_current:
            self.save_current_value()

        self.entry.bind("<FocusIn>", self.on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self.on_focus_out, add="+")
        self.entry.bind("<Return>", self.on_return, add="+")
        self.entry.bind("<Escape>", self.close_popup, add="+")
        self.entry.bind("<Down>", self.on_down_key, add="+")

    def history_items(self):
        max_items = self.max_items or get_entry_history_max_items(self.config_data)
        return get_history_list(self.config_data, self.history_key)[:max_items]

    def has_history(self):
        return bool(self.history_items())

    def on_focus_in(self, event=None):
        self.show_popup()

    def on_focus_out(self, event=None):
        self.save_current_value()
        self.entry.after(self.close_delay_ms, self.close_if_focus_outside)

    def on_return(self, event=None):
        self.save_current_value()
        self.show_popup()
        return None

    def on_down_key(self, event=None):
        self.show_popup()
        return None

    def save_current_value(self):
        normalized_value = self.value_normalizer(self.text_var.get())

        if normalized_value != self.text_var.get():
            self.text_var.set(normalized_value)

        save_entry_history(
            self.config_data,
            self.history_key,
            normalized_value,
            self.save_config,
            value_normalizer=self.value_normalizer,
        )

    def show_popup(self, event=None):
        if not self.enabled or not self.has_history():
            self.close_popup()
            return

        self.close_popup()

        self.popup = tk.Toplevel(self.parent)
        self.popup.configure(bg=THEME["border"], padx=1, pady=1)
        self.popup.transient(self.parent)
        self.popup.resizable(False, True)

        try:
            self.popup.overrideredirect(True)
        except Exception:
            pass

        self.position_popup(initial=True)

        register_popup(
            self.popup,
            self.close_popup,
            focus_on_register=False,
            close_on_focus_out=False,
            focus_guard_widgets=[self.entry],
        )

        body = tk.Frame(self.popup, bg=THEME["bg"])
        body.pack(fill="both", expand=True)

        for item in self.history_items():
            self.create_history_row(body, item)

        self.position_popup(initial=False)

        try:
            self.popup.lift()
            self.entry.focus_set()
        except Exception:
            pass

    def position_popup(self, initial=False):
        """
        历史记录浮窗位置。

        不正压下一行输入框，而是右下错位显示：
        - x 向右偏移，让左侧标签和下一行输入框边缘露出来；
        - y 稍微下移，但不贴得太死；
        - 宽度相应缩短，避免右侧溢出太多。
        """
        try:
            self.parent.update_idletasks()

            x_offset = 36
            y_offset = 4

            entry_x = self.entry.winfo_rootx()
            entry_y = self.entry.winfo_rooty()
            entry_width = self.entry.winfo_width()
            entry_height = self.entry.winfo_height()

            x = entry_x + x_offset
            y = entry_y + entry_height + y_offset
            width = max(entry_width - x_offset, 360)

            if initial:
                height = 1
            else:
                self.popup.update_idletasks()
                height = min(self.popup.winfo_reqheight(), 220)

            screen_width = self.entry.winfo_screenwidth()
            screen_height = self.entry.winfo_screenheight()

            if x + width > screen_width - 8:
                width = max(260, screen_width - x - 8)

            if y + height > screen_height - 40:
                y = max(0, entry_y - height - y_offset)

            self.popup.geometry(f"{width}x{height}+{x}+{y}")

        except Exception:
            pass

    def create_history_row(self, parent, item):
        row = tk.Frame(parent, bg=THEME["bg"])
        row.pack(fill="x")

        value_btn = tk.Button(
            row,
            text=item,
            anchor="w",
            command=lambda value=item: self.apply_history(value),
            bg=THEME["bg_input"],
            fg=THEME["fg"],
            activebackground=THEME["bg_btn_hover"],
            activeforeground=THEME["fg_on_dark"],
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            font=THEME["font_main"],
        )
        value_btn.pack(side="left", fill="x", expand=True, padx=(0, 1), pady=(0, 1))

        delete_btn = tk.Button(
            row,
            text="✖",
            command=lambda value=item: self.delete_history(value),
            width=3,
            bg=THEME["bg_input"],
            fg=THEME["danger"],
            activebackground=THEME["danger"],
            activeforeground=THEME["fg_on_dark"],
            relief="flat",
            bd=0,
            padx=4,
            pady=4,
            font=THEME["font_main"],
        )
        delete_btn.pack(side="right", pady=(0, 1))

    def apply_history(self, value):
        self.text_var.set(value)
        self.close_popup()
        self.entry.focus_set()
        self.entry.icursor("end")

    def delete_history(self, value):
        delete_entry_history(
            self.config_data,
            self.history_key,
            value,
            self.save_config,
        )

        if self.has_history():
            self.show_popup()
        else:
            self.close_popup()

        self.entry.focus_set()

    def close_if_focus_outside(self):
        if not self.popup or not self.popup.winfo_exists():
            return

        try:
            focused = self.entry.focus_get()
        except Exception:
            focused = None

        if focused is self.entry:
            return

        if is_descendant_or_self(focused, self.popup):
            return

        self.close_popup()

    def close_popup(self, event=None):
        if self.popup and self.popup.winfo_exists():
            unregister_popup(self.popup)
            self.popup.destroy()

        self.popup = None
        return "break"
