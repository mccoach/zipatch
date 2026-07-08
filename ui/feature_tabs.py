# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME


class DraggableFeatureTabs(tk.Frame):
    """
    功能标签栏。

    交互：
    - 左键点击：切换功能；
    - 右键菜单：左移、右移、恢复默认顺序。

    说明：
    拖拽排序已移除。
    该功能低频，保留右键左移/右移即可，避免拖拽预览造成交互复杂度和误操作。
    """

    def __init__(
        self,
        parent,
        feature_registry,
        config_data,
        get_current_mode,
        on_select,
        on_order_changed,
    ):
        super().__init__(parent, bg=THEME["bg"])

        self.feature_registry = feature_registry
        self.config_data = config_data
        self.get_current_mode = get_current_mode
        self.on_select = on_select
        self.on_order_changed = on_order_changed

        self.buttons = {}

        self.refresh()

    def get_order(self):
        order = self.config_data.get("ui", {}).get("feature_order", [])

        if not isinstance(order, list):
            order = []

        known = set(self.feature_registry.keys())
        result = []
        seen = set()

        for key in order:
            if key in known and key not in seen:
                result.append(key)
                seen.add(key)

        for key, item in sorted(
            self.feature_registry.items(),
            key=lambda pair: pair[1].get("default_order", 9999),
        ):
            if key not in seen:
                result.append(key)
                seen.add(key)

        return result

    def refresh(self):
        for child in self.winfo_children():
            child.destroy()

        self.buttons = {}

        current = self.get_current_mode()

        for key in self.get_order():
            item = self.feature_registry[key]
            selected = key == current

            btn = tk.Button(
                self,
                text=item["title"],
                width=14,
                bg=THEME["bg_btn_selected"] if selected else THEME["bg_btn"],
                fg=THEME["fg_on_dark"] if selected else THEME["fg_label"],
                activebackground=THEME["bg_btn_hover"],
                activeforeground=THEME["fg_on_dark"],
                relief="flat",
                bd=0,
                padx=10,
                pady=6,
                cursor="hand2",
                font=THEME["font_main"],
                command=lambda k=key: self.on_select(k),
            )
            btn.pack(side="left", padx=4)

            btn.bind("<Enter>", lambda e, k=key: self.on_enter(k))
            btn.bind("<Leave>", lambda e, k=key: self.on_leave(k))
            btn.bind("<Button-3>", lambda e, k=key: self.show_context_menu(e, k))

            self.buttons[key] = btn

    def refresh_button_styles(self):
        current = self.get_current_mode()

        for key, btn in self.buttons.items():
            selected = key == current

            btn.config(
                bg=THEME["bg_btn_selected"] if selected else THEME["bg_btn"],
                fg=THEME["fg_on_dark"] if selected else THEME["fg_label"],
            )

    def on_enter(self, key):
        btn = self.buttons.get(key)

        if not btn:
            return

        btn.config(bg=THEME["bg_btn_hover"], fg=THEME["fg_on_dark"])

    def on_leave(self, key):
        self.refresh_button_styles()

    def show_context_menu(self, event, key):
        menu = tk.Menu(self, tearoff=0)

        menu.add_command(
            label="左移",
            command=lambda: self.move_one_step(key, -1),
        )
        menu.add_command(
            label="右移",
            command=lambda: self.move_one_step(key, 1),
        )
        menu.add_separator()
        menu.add_command(
            label="恢复默认顺序",
            command=self.restore_default_order,
        )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def move_one_step(self, key, step):
        order = self.get_order()

        if key not in order:
            return

        index = order.index(key)
        new_index = index + step

        if new_index < 0 or new_index >= len(order):
            self.refresh_button_styles()
            return

        order[index], order[new_index] = order[new_index], order[index]

        self.on_order_changed(order)

    def restore_default_order(self):
        order = [
            key
            for key, item in sorted(
                self.feature_registry.items(),
                key=lambda pair: pair[1].get("default_order", 9999),
            )
        ]

        self.on_order_changed(order)
