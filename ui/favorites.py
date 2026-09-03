# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_show_error, safe_ask_yes_no
from core.paths import center_window
from core.text_io import get_text_value, replace_text_keep_undo
from ui.theme import styled_frame, styled_entry
from ui.window_manager import register_popup, unregister_popup, close_registered_popup


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

    收藏浮窗的最终交互身份：
    - 它是依附于文本框标题栏按钮的菜单型 Toplevel；
    - 打开后进入全局弹窗栈并拿到焦点，保证 ESC 先关闭收藏浮窗；
    - 不依赖 FocusOut 自动关闭，避免和按钮点击、文本替换、焦点恢复抢时序；
    - 选择收藏项后，等待当前 Tk 按钮事件闭环结束，再关闭浮窗并把焦点交还目标文本框。
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

        favorites = self.get_favorites()
        existing_index = None

        for index, item in enumerate(favorites):
            if item.get("name") == name:
                existing_index = index
                break

        if existing_index is not None:
            ok = safe_ask_yes_no(
                "收藏已存在",
                f"已存在同名收藏：\n{name}\n\n是否覆盖原收藏内容？",
                parent=self.parent,
            )

            if not ok:
                return

            favorites[existing_index] = {
                "name": name,
                "content": content,
            }
        else:
            favorites.append({
                "name": name,
                "content": content,
            })

        self.save_favorites(favorites)
        self.refresh_popup()

    def load_favorite(self, item):
        replace_text_keep_undo(self.text_widget, item.get("content", ""))
        self.parent.after_idle(self.text_widget.focus_set)

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
            close_registered_popup(self.popup)
        else:
            self.show_popup()

    def close_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = None
        return "break"

    def refresh_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.show_popup()

    def show_popup(self):
        if self.popup and self.popup.winfo_exists():
            close_registered_popup(self.popup, restore_focus=False)

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
        else:
            for item in favorites:
                self.create_favorite_row(list_frame, item)

        self.position_popup()

        register_popup(
            self.popup,
            self.close_popup,
            focus_on_register=True,
            close_on_focus_out=True,
        )

        self.popup.lift()
        self.popup.focus_force()

    def position_popup(self):
        self.popup.update_idletasks()

        popup_width = 420
        popup_height = min(self.popup.winfo_reqheight(), 320)

        button_x = self.favorite_button.winfo_rootx()
        button_y = self.favorite_button.winfo_rooty()
        button_width = self.favorite_button.winfo_width()
        button_height = self.favorite_button.winfo_height()

        screen_width = self.favorite_button.winfo_screenwidth()
        screen_height = self.favorite_button.winfo_screenheight()

        x = button_x + button_width - popup_width
        y = button_y + button_height + 2

        if x < 0:
            x = 0

        if x + popup_width > screen_width:
            x = max(0, screen_width - popup_width - 8)

        if y + popup_height > screen_height - 40:
            y = max(0, button_y - popup_height - 2)

        self.popup.geometry(f"{popup_width}x{popup_height}+{x}+{y}")

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