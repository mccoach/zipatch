# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_show_error, safe_ask_yes_no
from core.paths import center_window
from core.text_io import get_text_value, replace_text_keep_undo
from ui.theme import styled_frame, styled_entry
from ui.window_manager import register_popup, unregister_popup


def title_bar_button(parent, text, command, width=6, danger=False, accent=False, bold=False):
    """
    文本框标题栏小按钮。

    统一用于：
    - 清空；
    - 收藏；
    - 收藏浮窗里的改名/删除/添加。
    """
    if danger:
        bg = THEME["bg_input"]
        fg = THEME["danger"]
        hover_bg = THEME["danger"]
        hover_fg = THEME["fg_on_dark"]
    elif accent:
        bg = THEME["bg_input"]
        fg = "#168a16"
        hover_bg = "#168a16"
        hover_fg = THEME["fg_on_dark"]
    else:
        bg = THEME["bg_btn"]
        fg = THEME["fg"]
        hover_bg = THEME["bg_btn_hover"]
        hover_fg = THEME["fg_on_dark"]

    font = THEME["font_title"] if bold else THEME["font_main"]

    btn = tk.Button(
        parent,
        text=text,
        command=command,
        width=width,
        bg=bg,
        fg=fg,
        activebackground=hover_bg,
        activeforeground=hover_fg,
        relief="flat",
        bd=0,
        padx=4,
        pady=1,
        cursor="hand2",
        font=font,
    )

    btn._normal_bg = bg
    btn._normal_fg = fg

    btn.bind("<Enter>", lambda e: btn.config(bg=hover_bg, fg=hover_fg))
    btn.bind("<Leave>", lambda e: btn.config(bg=btn._normal_bg, fg=btn._normal_fg))

    return btn


def ask_favorite_name(parent, initial_name=""):
    dialog = tk.Toplevel(parent)
    dialog.title("收藏名称")
    dialog.configure(bg=THEME["bg"])
    center_window(dialog, 420, 160)
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)

    result = {"name": None}
    name_var = tk.StringVar(value=initial_name or "")

    tk.Label(
        dialog,
        text="请输入收藏名称/描述：",
        bg=THEME["bg"],
        fg=THEME["fg_label"],
        font=THEME["font_main"],
        anchor="w",
    ).pack(fill="x", padx=16, pady=(16, 6))

    entry_frame, entry = styled_entry(dialog, name_var)
    entry_frame.pack(fill="x", padx=16, pady=(0, 14))

    button_row = styled_frame(dialog)
    button_row.pack(fill="x", padx=16, pady=(0, 14))

    def confirm():
        name = name_var.get().strip()

        if not name:
            safe_show_error("名称为空", "收藏名称/描述不能为空。", parent=dialog)
            return

        result["name"] = name
        unregister_popup(dialog)
        dialog.destroy()

    def cancel(event=None):
        unregister_popup(dialog)
        dialog.destroy()
        return "break"

    title_bar_button(button_row, "取消", cancel, width=10).pack(
        side="right",
        padx=(8, 0),
    )
    title_bar_button(button_row, "保存", confirm, width=10).pack(side="right")

    entry.bind("<Return>", lambda event: confirm())
    register_popup(dialog, cancel)

    def focus_entry():
        try:
            entry.focus_force()
            entry.select_range(0, "end")
        except Exception:
            pass

    dialog.after(80, focus_entry)

    parent.wait_window(dialog)

    return result["name"]


class FavoriteTextBoxController:
    """
    文本框收藏浮窗。

    标题栏只显示一个“收藏”按钮。
    点击后显示轻量浮窗：
    - 第一行：添加当前文本为收藏 + 绿色加号；
    - 第二行起：已有收藏项。
    """

    def __init__(
        self,
        parent,
        title_row,
        text_widget,
        config_data,
        favorite_key,
        save_config_func,
    ):
        self.parent = parent
        self.title_row = title_row
        self.text_widget = text_widget
        self.config_data = config_data
        self.favorite_key = favorite_key
        self.save_config_func = save_config_func
        self.popup = None

        self.config_data.setdefault("favorites", {})
        self.config_data["favorites"].setdefault(self.favorite_key, [])

        self.favorite_button = title_bar_button(
            title_row,
            "收藏",
            self.toggle_popup,
            width=6,
        )
        self.favorite_button.pack(side="right", padx=(0, 6))

    def get_favorites(self):
        return self.config_data["favorites"].setdefault(self.favorite_key, [])

    def save_favorites(self, favorites):
        self.config_data["favorites"][self.favorite_key] = favorites
        self.save_config_func()

    def save_current_text_as_favorite(self):
        content = get_text_value(self.text_widget)

        if not content:
            safe_show_error("内容为空", "当前文本框内容为空，不能收藏。", parent=self.parent)
            return

        name = ask_favorite_name(self.parent)

        if not name:
            return

        favorites = [
            item
            for item in self.get_favorites()
            if item.get("name") != name
        ]

        favorites.append({
            "name": name,
            "content": content,
        })

        self.save_favorites(favorites)
        self.refresh_popup()

    def load_favorite(self, item):
        replace_text_keep_undo(self.text_widget, item.get("content", ""))
        self.close_popup()
        self.text_widget.focus_set()

    def rename_favorite(self, item):
        old_name = item.get("name", "")
        new_name = ask_favorite_name(self.parent, initial_name=old_name)

        if not new_name or new_name == old_name:
            return

        content = item.get("content", "")

        favorites = [
            fav
            for fav in self.get_favorites()
            if fav.get("name") not in (old_name, new_name)
        ]

        favorites.append({
            "name": new_name,
            "content": content,
        })

        self.save_favorites(favorites)
        self.refresh_popup()

    def delete_favorite(self, item):
        name = item.get("name", "")

        if not safe_ask_yes_no("确认删除", f"确定删除收藏：\n{name}", parent=self.parent):
            return

        favorites = [
            fav
            for fav in self.get_favorites()
            if fav.get("name") != name
        ]

        self.save_favorites(favorites)
        self.refresh_popup()

    def toggle_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.close_popup()
        else:
            self.show_popup()

    def close_popup(self, event=None):
        if self.popup and self.popup.winfo_exists():
            unregister_popup(self.popup)
            self.popup.destroy()

        self.popup = None
        return "break"

    def refresh_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.show_popup()

    def show_popup(self):
        self.close_popup()

        self.popup = tk.Toplevel(self.parent)
        self.popup.configure(
            bg=THEME["border"],
            padx=1,
            pady=1,
        )
        self.popup.transient(self.parent)
        self.popup.resizable(False, True)

        try:
            self.popup.overrideredirect(True)
        except Exception:
            pass

        try:
            self.parent.update_idletasks()
            x = self.favorite_button.winfo_rootx()
            y = self.favorite_button.winfo_rooty() + self.favorite_button.winfo_height() + 2
            self.popup.geometry(f"420x280+{x - 330}+{y}")
        except Exception:
            center_window(self.popup, 420, 280)

        register_popup(
            self.popup,
            self.close_popup,
            focus_on_register=True,
            close_on_focus_out=True,
            focus_guard_widgets=[self.favorite_button],
        )

        list_frame = tk.Frame(self.popup, bg=THEME["bg"])
        list_frame.pack(fill="both", expand=True)

        self.create_add_row(list_frame)

        favorites = self.get_favorites()

        if not favorites:
            tk.Label(
                list_frame,
                text="暂无收藏",
                bg=THEME["bg"],
                fg=THEME["fg_dim"],
                font=THEME["font_main"],
            ).pack(fill="x", padx=8, pady=12)
            return

        for item in favorites:
            self.create_favorite_row(list_frame, item)

    def create_add_row(self, parent):
        row = styled_frame(parent)
        row.pack(fill="x", padx=8, pady=(8, 6))

        add_button = tk.Button(
            row,
            text="添加当前文本为收藏",
            anchor="w",
            command=self.save_current_text_as_favorite,
            bg=THEME["bg_input"],
            fg=THEME["fg"],
            activebackground=THEME["bg_btn_hover"],
            activeforeground=THEME["fg_on_dark"],
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            font=THEME["font_main"],
        )
        add_button.pack(side="left", fill="x", expand=True, padx=(0, 4))

        title_bar_button(
            row,
            "✚",
            self.save_current_text_as_favorite,
            width=3,
            accent=True,
            bold=True,
        ).pack(side="right", padx=(4, 0))

        tk.Frame(parent, height=1, bg=THEME["border"]).pack(fill="x", padx=8, pady=(0, 6))

    def create_favorite_row(self, parent, item):
        row = styled_frame(parent)
        row.pack(fill="x", padx=8, pady=2)

        name = item.get("name", "")

        tk.Button(
            row,
            text=name,
            anchor="w",
            command=lambda fav=item: self.load_favorite(fav),
            bg=THEME["bg_input"],
            fg=THEME["fg"],
            activebackground=THEME["bg_btn_hover"],
            activeforeground=THEME["fg_on_dark"],
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            font=THEME["font_main"],
        ).pack(side="left", fill="x", expand=True)

        title_bar_button(
            row,
            "✖",
            lambda fav=item: self.delete_favorite(fav),
            width=3,
            danger=True,
            bold=True,
        ).pack(side="right", padx=(4, 0))

        title_bar_button(
            row,
            "改名",
            lambda fav=item: self.rename_favorite(fav),
            width=5,
        ).pack(side="right", padx=(4, 0))
