# -*- coding: utf-8 -*-

import tkinter as tk
from contextlib import contextmanager

from core.constants import THEME
from core.favorite_order import order_favorites
from ui.message_dialogs import safe_ask_yes_no, safe_show_error
from ui.window_manager import center_window
from ui.text_editor import replace_text_keep_undo
from ui.theme import styled_entry, styled_frame
from ui.widgets import add_tooltip
from ui.window_manager import (
    register_popup, unregister_popup, close_registered_popup,
    restore_editor_interaction, take_modal_control,
)


# 当前微软雅黑 UI 10 磅名称采用固定紧凑行高，不在运行时测量字体。
# 行容器拥有最终高度，名称与三个操作按钮共同填满同一行。
FAVORITE_ROW_HEIGHT = 30
FAVORITE_ACTION_WIDTH = 20
FAVORITE_ACTION_BITMAPS = {
    "rename": (
        "............",
        ".........XX.",
        "........XXX.",
        ".......XXX..",
        "......XXX...",
        ".....XXX....",
        "....XXX.....",
        "...XXX......",
        "..XXX.......",
        "..XX........",
        ".XXXXXXX....",
        "............",
    ),
    "pin": (
        "............",
        "...XXXXXX...",
        "....XXXX....",
        "....XXXX....",
        "....XXXX....",
        "...XXXXXX...",
        "..XXXXXXXX..",
        ".....XX.....",
        ".....XX.....",
        ".....XX.....",
        ".....X......",
        "............",
    ),
    "delete": (
        "............",
        "..XX....XX..",
        "...XX..XX...",
        "....XXXX....",
        ".....XX.....",
        "....XXXX....",
        "...XX..XX...",
        "..XX....XX..",
        "............",
        "............",
        "............",
        "............",
    ),
}


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


def favorite_action_button(parent, action, command, *, pinned=False):
    """绘制有明确语义与悬停提示的收藏操作图标。"""
    foreground = (
        THEME["danger"] if action == "delete"
        else "#168a16" if action == "pin" and pinned
        else THEME["fg"]
    )
    background = "#cde8cf" if action == "pin" and pinned else THEME["bg_input"]
    image = tk.PhotoImage(master=parent, width=12, height=12)
    for y, row in enumerate(FAVORITE_ACTION_BITMAPS[action]):
        for x, pixel in enumerate(row):
            if pixel == "X":
                image.put(foreground, (x, y))
    button = tk.Button(
        parent, image=image, command=command,
        width=FAVORITE_ACTION_WIDTH, height=FAVORITE_ROW_HEIGHT,
        padx=0, pady=0, bd=0, highlightthickness=0, relief="flat",
        bg=background, activebackground=THEME["bg_btn_hover"],
        cursor="hand2", takefocus=True,
    )
    button._favorite_action = action
    button._favorite_pinned = pinned
    button._favorite_image = image
    description = {
        "rename": "改名",
        "pin": "取消置顶" if pinned else "置顶",
        "delete": "删除",
    }[action]
    add_tooltip(button, description)
    # 悬停配色不能覆盖 Tooltip 已安装的 Enter/Leave 绑定。
    button.bind(
        "<Enter>",
        lambda event: button.configure(bg=THEME["bg_btn_hover"]),
        add="+",
    )
    button.bind(
        "<Leave>", lambda event: button.configure(bg=background), add="+",
    )
    return button


def ask_favorite_name(parent, initial_name="", *, existing_names=None):
    """只返回最终名称；收藏正文、覆盖确认与提交由调用方负责。"""
    owner = parent.winfo_toplevel()
    dialog = tk.Toplevel(owner)
    dialog.title("收藏名称")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(owner)
    take_modal_control(dialog)
    dialog.resizable(False, False)
    center_window(dialog, 420, 360 if existing_names is not None else 160)
    result = {"name": None}
    variable = tk.StringVar(master=dialog, value=initial_name)
    tk.Label(dialog, text="请输入收藏名称/描述：", bg=THEME["bg"]).pack(
        fill="x", padx=16, pady=(16, 6),
    )
    frame, entry = styled_entry(dialog, variable)
    frame.pack(fill="x", padx=16, pady=(0, 14))

    if existing_names is not None:
        tk.Label(
            dialog,
            text="输入新名称可新增；选择已有名称后保存可替换该收藏。",
            bg=THEME["bg"], fg=THEME["fg_label"],
            anchor="w", justify="left", wraplength=388,
        ).pack(fill="x", padx=16, pady=(0, 6))
        list_frame = styled_frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        names = tk.Listbox(
            list_frame, height=6, exportselection=False,
            bg=THEME["bg_input"], fg=THEME["fg"],
            selectbackground=THEME["select_bg"],
            selectforeground=THEME["select_fg"],
            font=THEME["font_main"], relief="solid", bd=1,
        )
        scrollbar = tk.Scrollbar(
            list_frame, orient="vertical", command=names.yview,
        )
        names.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        names.pack(side="left", fill="both", expand=True)
        for name in existing_names:
            names.insert("end", name)
        if not existing_names:
            tk.Label(
                dialog, text="当前分类暂无收藏，请输入新名称。",
                bg=THEME["bg"], fg=THEME["fg_dim"], anchor="w",
            ).pack(fill="x", padx=16, pady=(0, 6))

        def select_existing(event=None):
            selection = names.curselection()
            if selection:
                variable.set(names.get(selection[0]))
                entry.icursor("end")

        names.bind("<<ListboxSelect>>", select_existing)

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
    try:
        owner.wait_window(dialog)
    finally:
        unregister_popup(dialog)
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
        # 加载与所有正式收藏修改使用同一个稳定分区规则。
        # 数组是持久化顺序的唯一来源，不维护第二份显示排序。
        result = self.commits.submit_values(
            self.commits.manager.config_data["favorites"],
            {self.favorite_key: order_favorites(favorites)},
            self.parent.winfo_toplevel(),
        )
        # 普通收藏失败仍保留已接受内存状态供重试。
        # 视图消费该权威状态；刷新不表示持久化成功。
        self.refresh_popup()
        return result

    def can_edit(self):
        return (
            not self.commits.operation_depth
            and str(self.text_widget.cget("state")) == "normal"
        )

    @contextmanager
    def interaction(self):
        """保护完整操作；管理留在浮窗，明确回填才返回正文。"""
        try:
            with self.commits.operation():
                yield
        finally:
            if self.popup is None:
                restore_editor_interaction(self.text_widget)
            else:
                # 保留原窗口与当前输入上下文，不激活正文所属窗口，
                # 避免 Windows 将仍存活的收藏浮窗遮到其后面。
                self.popup.lift(self.text_widget.winfo_toplevel())

    def save_current_text_as_favorite(self):
        if not self.can_edit():
            return
        with self.interaction():
            content = self.commits.read_text(
                self.text_widget,
                preserve_trailing_newlines=getattr(
                    self.text_widget, "_preserve_trailing_newlines", False,
                ),
            )
            prepare = getattr(self.text_widget, "_prepare_favorite_content", None)
            if prepare is not None:
                content = prepare(content)
                if content is None:
                    return
            if not content:
                safe_show_error(
                    "内容为空", "当前文本框内容为空，不能收藏。",
                    self.parent.winfo_toplevel(),
                )
                return
            name = ask_favorite_name(
                self.parent,
                existing_names=tuple(value["name"] for value in self.favorites()),
            )
            if name is None:
                return
            values = list(self.favorites())
            index = next((
                number for number, value in enumerate(values)
                if value["name"] == name
            ), None)
            if index is not None:
                if not safe_ask_yes_no(
                    "收藏已存在",
                    f"已存在同名收藏：\n{name}\n\n是否覆盖原收藏内容？",
                    self.parent.winfo_toplevel(),
                ):
                    return
                # 覆盖只改变正文，不丢失该条目的置顶事实和位置。
                values[index] = {**values[index], "content": content}
            else:
                values.append({"name": name, "content": content})
            self.submit(values)

    def load_favorite(self, value):
        if not self.can_edit():
            return False
        with self.interaction():
            if self.popup is not None:
                close_registered_popup(self.popup, restore_focus=False)
            replace_text_keep_undo(self.text_widget, value["content"])
            self.content_completed(value["content"])
            return True

    def rename_favorite(self, value):
        if not self.can_edit():
            return
        with self.interaction():
            old_name = value["name"]
            name = ask_favorite_name(self.parent, old_name)
            if name is None or name == old_name:
                return
            current = next((
                favorite for favorite in self.favorites()
                if favorite["name"] == old_name
            ), None)
            if current is None:
                return
            content = current["content"]
            prepare = getattr(self.text_widget, "_prepare_favorite_content", None)
            if prepare is not None:
                content = prepare(content, display=False)
                if content is None:
                    return
            # 保持既有同名改名替换语义，但源条目的状态和位置不改变。
            renamed = {**current, "name": name, "content": content}
            values = [
                renamed if favorite["name"] == old_name else favorite
                for favorite in self.favorites()
                if favorite["name"] != name
            ]
            self.submit(values)

    def delete_favorite(self, value):
        if not self.can_edit():
            return
        with self.interaction():
            if not safe_ask_yes_no(
                "确认删除", f"确定删除收藏：\n{value['name']}",
                self.parent.winfo_toplevel(),
            ):
                return
            self.submit([
                favorite for favorite in self.favorites()
                if favorite["name"] != value["name"]
            ])

    def toggle_favorite_pin(self, value):
        """新置顶成为整个名单第一项；取消置顶回到普通区首位。"""
        if not self.can_edit():
            return
        with self.interaction():
            values = list(self.favorites())
            index = next((
                number for number, favorite in enumerate(values)
                if favorite["name"] == value["name"]
            ), None)
            if index is None:
                return
            selected = dict(values.pop(index))
            if selected.get("pinned", False):
                selected.pop("pinned")
                # 稳定分区会把其放在仍置顶的项目之后、普通项目之前。
                values.insert(0, selected)
            else:
                selected["pinned"] = True
                values.insert(0, selected)
            self.submit(values)

    def toggle_popup(self):
        if not self.can_edit():
            return
        if self.popup is not None:
            close_registered_popup(self.popup, restore_focus=False)
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
            self.render_popup(self.popup)
            self.position_popup(self.popup)

    def render_popup(self, popup):
        """只更新派生内容，不销毁或重新登记浮窗。"""
        body = popup.winfo_children()[0]
        focused = popup.focus_displayof()
        # 原列表按钮可能在本次重绘中销毁。将其焦点交给保留的添加按钮，
        # 不强制切回正文，也不让焦点继续指向已经销毁的控件。
        if focused is not None and focused.master is not None:
            current = focused
            while current is not None and current is not popup:
                if current is body:
                    body.winfo_children()[0].focus_set()
                    break
                current = current.master

        for child in body.winfo_children()[1:]:
            child.destroy()

        favorites = self.favorites()
        if not favorites:
            tk.Label(
                body, text="暂无收藏", bg=THEME["bg"],
                font=THEME["font_main"],
            ).pack(pady=8)

        for value in favorites:
            row = tk.Frame(
                body, bg=THEME["bg_input"], height=FAVORITE_ROW_HEIGHT,
                bd=0, highlightthickness=0,
            )
            row.pack(fill="x", padx=8, pady=1)
            row.pack_propagate(False)
            tk.Button(
                row, text=value["name"], anchor="w",
                command=lambda current=value: self.load_favorite(current),
                bg=THEME["bg_input"], fg=THEME["fg"],
                activebackground=THEME["bg_btn_hover"],
                activeforeground=THEME["fg_on_dark"],
                relief="flat", bd=0, highlightthickness=0,
                font=THEME["font_main"], padx=8, pady=0,
                cursor="hand2",
            ).pack(side="left", fill="both", expand=True)
            # 右侧 pack 从最右开始；四个点击区域等高、横向零间距。
            favorite_action_button(
                row, "pin",
                lambda current=value: self.toggle_favorite_pin(current),
                pinned=value.get("pinned", False),
            ).pack(side="right", fill="y")
            favorite_action_button(
                row, "delete",
                lambda current=value: self.delete_favorite(current),
            ).pack(side="right", fill="y")
            favorite_action_button(
                row, "rename",
                lambda current=value: self.rename_favorite(current),
            ).pack(side="right", fill="y")

    def position_popup(self, popup):
        """首次显示和内容变化共用定位，不改变窗口的打开事实。"""
        popup.update_idletasks()
        width = 420
        height = min(popup.winfo_reqheight(), 320)
        x = max(0, self.button.winfo_rootx() + self.button.winfo_width() - width)
        y = self.button.winfo_rooty() + self.button.winfo_height() + 2
        x = min(x, max(0, popup.winfo_screenwidth() - width - 8))
        if y + height > popup.winfo_screenheight() - 40:
            y = max(0, self.button.winfo_rooty() - height - 2)
        popup.geometry(f"{width}x{height}+{x}+{y}")

    def on_popup_destroyed(self, event):
        if event.widget is self.popup:
            # 标题关闭、直接 destroy 和父窗口销毁共用同一失效边界。
            # 不保留“窗口已不可见但引用仍表示打开”的第二份状态。
            self.popup = None

    def show_popup(self):
        if self.popup is not None:
            self.popup.lift(self.text_widget.winfo_toplevel())
            return
        owner = self.text_widget.winfo_toplevel()
        owner.update_idletasks()
        opening_focus = owner.focus_displayof()
        popup = tk.Toplevel(owner)
        popup.withdraw()
        self.popup = popup
        popup.bind("<Destroy>", self.on_popup_destroyed, add="+")
        popup.overrideredirect(True)
        popup.configure(bg=THEME["border"], padx=1, pady=1)
        popup.transient(owner)
        body = styled_frame(popup)
        body.pack(fill="both", expand=True)
        title_bar_button(
            body, "添加当前文本为收藏",
            self.save_current_text_as_favorite, width=32,
        ).pack(fill="x", padx=8, pady=6)
        self.render_popup(popup)
        self.position_popup(popup)
        guards = [self.button, self.text_widget]
        if opening_focus is not None and opening_focus.winfo_toplevel() is owner:
            guards.append(opening_focus)
        register_popup(
            popup, self.close_popup, focus_on_register=False,
            close_on_focus_out=True, focus_guard_widgets=guards,
            dismissal_allowed=lambda: not self.commits.operation_depth,
        )
        popup.deiconify()
        popup.lift(owner)