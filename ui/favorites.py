# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_ask_yes_no, safe_show_error
from core.paths import center_window
from core.text_io import get_text_value, replace_text_keep_undo
from ui.theme import styled_entry, styled_frame
from ui.window_manager import register_popup, unregister_popup, close_registered_popup


def title_bar_button(parent, text, command, width=6, danger=False, accent=False, bold=False):
    foreground = THEME["danger"] if danger else ("#168a16" if accent else THEME["fg"])
    background = THEME["bg_input"] if danger or accent else THEME["bg_btn"]
    button = tk.Button(
        parent, text=text, command=command, width=width,
        bg=background, fg=foreground, activebackground=THEME["bg_btn_hover"],
        activeforeground=THEME["fg_on_dark"], relief="flat", bd=0,
        padx=4, pady=1, cursor="hand2",
        font=THEME["font_title"] if bold else THEME["font_main"],
    )
    button.bind("<Enter>", lambda event: button.configure(
        bg=THEME["bg_btn_hover"], fg=THEME["fg_on_dark"],
    ))
    button.bind("<Leave>", lambda event: button.configure(bg=background, fg=foreground))
    return button


def ask_favorite_name(parent, initial_name=""):
    owner = parent.winfo_toplevel()
    dialog = tk.Toplevel(owner)
    dialog.title("收藏名称")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(owner)
    dialog.grab_set()
    dialog.resizable(False, False)
    center_window(dialog, 420, 160)
    result = {"name": None}
    variable = tk.StringVar(value=initial_name)
    tk.Label(dialog, text="请输入收藏名称/描述：", bg=THEME["bg"]).pack(
        fill="x", padx=16, pady=(16, 6),
    )
    frame, entry = styled_entry(dialog, variable)
    frame.pack(fill="x", padx=16, pady=(0, 14))
    row = styled_frame(dialog)
    row.pack(fill="x", padx=16, pady=(0, 14))

    def cancel():
        unregister_popup(dialog)
        dialog.destroy()
        return True

    def confirm():
        name = variable.get().strip()
        if not name:
            safe_show_error("名称为空", "收藏名称/描述不能为空。", dialog)
            return
        result["name"] = name
        cancel()

    title_bar_button(row, "取消", cancel, width=10).pack(side="right", padx=(8, 0))
    title_bar_button(row, "保存", confirm, width=10).pack(side="right")
    entry.bind("<Return>", lambda event: confirm())
    register_popup(dialog, cancel, exit_blocker=True)
    entry.focus_set()
    entry.select_range(0, "end")
    owner.wait_window(dialog)
    return result["name"]


class FavoriteTextBoxController:
    def __init__(self, parent, title_row, text_widget, commits, favorite_key, content_completed):
        self.parent = parent
        self.text_widget = text_widget
        self.commits = commits
        self.favorite_key = favorite_key
        self.content_completed = content_completed
        self.popup = None
        self.button = title_bar_button(title_row, "收藏", self.toggle_popup)
        self.button.pack(side="right", padx=(0, 6))

    def favorites(self):
        return self.commits.manager.config_data["favorites"][self.favorite_key]

    def submit(self, favorites):
        result = self.commits.submit_values(
            self.commits.manager.config_data["favorites"],
            {self.favorite_key: favorites}, self.parent.winfo_toplevel(),
        )
        if result.request_satisfied:
            self.refresh_popup()
        return result

    def save_current_text_as_favorite(self):
        content = get_text_value(self.text_widget)
        if not content:
            safe_show_error("内容为空", "当前文本框内容为空，不能收藏。", self.parent.winfo_toplevel())
            return
        with self.commits.operation():
            name = ask_favorite_name(self.parent)
        if name is None:
            return
        values = list(self.favorites())
        index = next((i for i, value in enumerate(values) if value["name"] == name), None)
        if index is not None:
            with self.commits.operation():
                confirmed = safe_ask_yes_no(
                    "收藏已存在", f"已存在同名收藏：\n{name}\n\n是否覆盖原收藏内容？",
                    self.parent.winfo_toplevel(),
                )
            if not confirmed:
                return
            values[index] = {"name": name, "content": content}
        else:
            values.append({"name": name, "content": content})
        self.submit(values)

    def load_favorite(self, value):
        replace_text_keep_undo(self.text_widget, value["content"])
        self.content_completed(value["content"])
        if self.popup is not None:
            close_registered_popup(self.popup, restore_focus=False)
        self.text_widget.focus_set()

    def rename_favorite(self, value):
        old_name = value["name"]
        with self.commits.operation():
            name = ask_favorite_name(self.parent, old_name)
        if name is None or name == old_name:
            return
        values = [
            favorite for favorite in self.favorites()
            if favorite["name"] not in (old_name, name)
        ]
        values.append({"name": name, "content": value["content"]})
        self.submit(values)

    def delete_favorite(self, value):
        with self.commits.operation():
            confirmed = safe_ask_yes_no(
                "确认删除", f"确定删除收藏：\n{value['name']}",
                self.parent.winfo_toplevel(),
            )
        if not confirmed:
            return
        self.submit([
            favorite for favorite in self.favorites()
            if favorite["name"] != value["name"]
        ])

    def toggle_popup(self):
        if self.popup is not None:
            close_registered_popup(self.popup)
        else:
            self.show_popup()

    def close_popup(self):
        popup, self.popup = self.popup, None
        if popup is not None:
            unregister_popup(popup)
            if popup.winfo_exists():
                popup.destroy()
        return True

    def refresh_popup(self):
        if self.popup is not None:
            self.show_popup()

    def show_popup(self):
        self.close_popup()
        popup = tk.Toplevel(self.parent)
        self.popup = popup
        popup.overrideredirect(True)
        popup.configure(bg=THEME["border"], padx=1, pady=1)
        popup.transient(self.parent.winfo_toplevel())
        body = styled_frame(popup)
        body.pack(fill="both", expand=True)
        title_bar_button(
            body, "添加当前文本为收藏", self.save_current_text_as_favorite, width=32,
        ).pack(fill="x", padx=8, pady=8)

        if not self.favorites():
            tk.Label(body, text="暂无收藏", bg=THEME["bg"]).pack(pady=12)

        for value in self.favorites():
            row = styled_frame(body)
            row.pack(fill="x", padx=8, pady=2)
            tk.Button(
                row, text=value["name"], anchor="w",
                command=lambda current=value: self.load_favorite(current),
                bg=THEME["bg_input"], fg=THEME["fg"], relief="flat",
                font=THEME["font_main"], padx=8, pady=4,
            ).pack(side="left", fill="x", expand=True)
            title_bar_button(
                row, "✖", lambda current=value: self.delete_favorite(current),
                width=3, danger=True, bold=True,
            ).pack(side="right", padx=(4, 0))
            title_bar_button(
                row, "改名", lambda current=value: self.rename_favorite(current), width=5,
            ).pack(side="right", padx=(4, 0))

        popup.update_idletasks()
        width = 420
        height = min(popup.winfo_reqheight(), 320)
        x = max(0, self.button.winfo_rootx() + self.button.winfo_width() - width)
        y = self.button.winfo_rooty() + self.button.winfo_height() + 2
        x = min(x, max(0, popup.winfo_screenwidth() - width - 8))
        if y + height > popup.winfo_screenheight() - 40:
            y = max(0, self.button.winfo_rooty() - height - 2)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        register_popup(popup, self.close_popup, close_on_focus_out=True)