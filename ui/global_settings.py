# -*- coding: utf-8 -*-

"""唯一全局边界设置草稿；保存成功才通知查找消费者。"""

import tkinter as tk
import unicodedata

from core.constants import THEME
from core.text_search import (
    DEFAULT_BOUNDARY_CODEPOINTS, WHITE_SPACE_CODEPOINTS,
    is_invisible_boundary, validate_boundary_codepoints,
)
from ui.message_dialogs import safe_show_error
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import (
    center_window, register_popup, take_modal_control, unregister_popup,
)


BOUNDARY_NAMES = {
    0x0009: "Tab",
    0x000A: "换行 LF",
    0x000B: "垂直制表 VT",
    0x000C: "换页 FF",
    0x000D: "回车 CR",
    0x0020: "普通空格",
    0x0085: "下一行 NEL",
    0x00A0: "不换行空格",
    0x1680: "OGHAM 空格",
    0x2028: "Unicode 行分隔符",
    0x2029: "Unicode 段落分隔符",
    0x202F: "窄不换行空格",
    0x205F: "中数学空格",
    0x3000: "全角空格",
}
COMMON_SPACE_CODEPOINTS = (
    0x0020, 0x0009, 0x3000, 0x00A0,
    0x000A, 0x000D, 0x0085, 0x2028, 0x2029,
)


class GlobalSettingsDialog:
    """控件拥有临时副本；现有配置草稿保存机制负责失败恢复。"""

    def __init__(self, parent, commits):
        self.commits = commits
        self.config = commits.manager.config_data["search"]
        self.dialog = tk.Toplevel(parent)
        dialog = self.dialog
        dialog.title("全词匹配边界")
        dialog.configure(bg=THEME["bg"])
        dialog.transient(parent)
        self.closed = False
        self.busy = False
        self.space_variables = {}
        self.extra_variables = {}
        self.other_open = tk.BooleanVar(master=dialog, value=False)
        take_modal_control(dialog)
        center_window(
            dialog, 760, min(740, max(320, dialog.winfo_screenheight() - 100)),
        )

        bottom = styled_frame(dialog)
        bottom.pack(side="bottom", fill="x", padx=16, pady=14)
        styled_button(
            bottom, "取消", self.close, width=10,
        ).pack(side="right", padx=(8, 0))
        styled_button(
            bottom, "保存设置", self.save, width=12, accent=True,
        ).pack(side="right")
        styled_button(
            bottom, "恢复默认边界", self.restore_defaults, width=14,
        ).pack(side="left")

        viewport = styled_frame(dialog)
        self.viewport = viewport
        viewport.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        canvas = tk.Canvas(
            viewport, bg=THEME["bg"], highlightthickness=0, width=1,
        )
        scrollbar = tk.Scrollbar(
            viewport, orient="vertical", command=canvas.yview,
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.canvas = canvas

        def scroll_with_mouse(event):
            if event.num == 4:
                units = -1
            elif event.num == 5:
                units = 1
            elif event.delta:
                units = -int(event.delta / 120)
                if units == 0:
                    units = -1 if event.delta > 0 else 1
            else:
                return None
            target = canvas
            current = event.widget
            while current is not None and current is not viewport:
                if current is self.visible_frame:
                    target = self.visible_text
                    break
                current = current.master
            # 内层到达边缘也不转交外层；一次滚轮只操作所属区域。
            target.yview_scroll(units, "units")
            return "break"

        # 一个页面入口按事件所属区域选择滚动对象；
        # 在原生类绑定前处理，避免同一事件同时滚动内外两层。
        # 不影响其他窗口或底部动作区。
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            viewport.bind(sequence, scroll_with_mouse)
        body = styled_frame(canvas)
        embedded = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(embedded, width=event.width),
        )


        tk.Label(
            body,
            text="全词匹配边界\n"
                 "整个实际匹配范围的左右两侧，均为名单中的边界字符"
                 "或全文起止位置时，才接受匹配。",
            bg=THEME["bg"], fg=THEME["fg_label"],
            anchor="w", justify="left", wraplength=680,
            font=THEME["font_main"],
        ).pack(fill="x", pady=(0, 12))

        tk.Label(
            body, text="可见字符", font=THEME["font_title"],
            bg=THEME["bg"], fg=THEME["fg_label"],
        ).pack(anchor="w")
        tk.Label(
            body,
            text="每个字符都是一个边界，不使用正则或转义。重复字符自动去重。\n"
                 "普通空格和换行只用于排版，是否作为边界由下方勾选控制。\n"
                 "不可见字符请通过勾选或添加码点设置，不要粘贴进本框。",
            bg=THEME["bg"], fg=THEME["fg_dim"],
            anchor="w", justify="left", wraplength=680,
        ).pack(fill="x", pady=(4, 6))
        frame, self.visible_text = styled_text_with_scrollbars(
            body, height=5, wrap="word",
        )
        self.visible_frame = frame
        frame.pack(fill="x", pady=(0, 14))

        tk.Label(
            body, text="空白及不可见字符", font=THEME["font_title"],
            bg=THEME["bg"], fg=THEME["fg_label"],
        ).pack(anchor="w", pady=(0, 6))
        common = styled_frame(body)
        common.pack(fill="x")
        for number, codepoint in enumerate(COMMON_SPACE_CODEPOINTS):
            self._space_check(common, codepoint, number)

        tk.Checkbutton(
            body, text="展开其他 Unicode 空白字符",
            variable=self.other_open, command=self.toggle_other,
            bg=THEME["bg"], selectcolor=THEME["bg_input"],
        ).pack(anchor="w", pady=(8, 4))
        self.other_frame = styled_frame(body)
        for number, codepoint in enumerate(
            value for value in WHITE_SPACE_CODEPOINTS
            if value not in COMMON_SPACE_CODEPOINTS
        ):
            self._space_check(self.other_frame, codepoint, number)

        self.extra_frame = styled_frame(body)
        self.extra_frame.pack(fill="x", pady=(4, 8))
        entry_row = styled_frame(body)
        entry_row.pack(fill="x", pady=(2, 10))
        tk.Label(
            entry_row, text="添加码点：", bg=THEME["bg"],
        ).pack(side="left")
        self.codepoint_text = tk.StringVar(master=dialog)
        tk.Entry(
            entry_row, textvariable=self.codepoint_text, width=14,
            bg=THEME["bg_input"],
        ).pack(side="left", padx=(0, 8))
        styled_button(
            entry_row, "添加", self.add_codepoint, width=7,
        ).pack(side="left")
        tk.Label(
            entry_row, text="例如 U+200B、FEFF；按十六进制读取",
            bg=THEME["bg"], fg=THEME["fg_dim"],
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            body,
            text="全文开头和结尾始终有效，不能取消。\n"
                 "边界与通配符禁止跨行的规则相互独立。\n"
                 "恢复默认只修改当前草稿；取消会丢弃草稿。"
                 "保存成功后才生效，并跨重启保留。",
            bg=THEME["bg"], fg=THEME["fg_dim"],
            justify="left", anchor="w", wraplength=680,
        ).pack(fill="x", pady=(4, 12))
        self.status = tk.StringVar(master=dialog, value="")
        tk.Label(
            body, textvariable=self.status,
            bg=THEME["bg"], fg=THEME["danger"],
            justify="left", anchor="w", wraplength=680,
        ).pack(fill="x", pady=(0, 12))

        self.load_draft(self.config["boundary_codepoints"])
        self._route_page_scroll(self.viewport)
        register_popup(dialog, self.close)

    def _route_page_scroll(self, widget):
        """本页面共用一个滚轮绑定，并在原生控件类绑定前执行。"""
        page_tag = self.viewport._w
        widget.bindtags((
            page_tag,
            *(tag for tag in widget.bindtags() if tag != page_tag),
        ))
        for child in widget.winfo_children():
            self._route_page_scroll(child)

    def _name(self, codepoint):
        return (
            BOUNDARY_NAMES.get(
                codepoint, unicodedata.name(chr(codepoint), "未命名字符"),
            )
            + f"  U+{codepoint:04X}"
        )

    def _space_check(self, parent, codepoint, number):
        variable = tk.BooleanVar(master=self.dialog)
        self.space_variables[codepoint] = variable
        tk.Checkbutton(
            parent, text=self._name(codepoint), variable=variable,
            bg=THEME["bg"], selectcolor=THEME["bg_input"],
            anchor="w",
        ).grid(row=number // 2, column=number % 2, sticky="w", padx=(0, 16))
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)

    def toggle_other(self):
        if self.other_open.get():
            self.other_frame.pack(
                fill="x", before=self.extra_frame, pady=(0, 8),
            )
        else:
            self.other_frame.pack_forget()

    def _add_extra(self, codepoint, selected=True):
        previous = self.extra_variables.get(codepoint)
        if previous is not None:
            previous.set(selected)
            return
        variable = tk.BooleanVar(master=self.dialog, value=selected)
        self.extra_variables[codepoint] = variable
        checkbox = tk.Checkbutton(
            self.extra_frame, text=self._name(codepoint), variable=variable,
            bg=THEME["bg"], selectcolor=THEME["bg_input"],
            anchor="w",
        )
        checkbox.pack(anchor="w")
        self._route_page_scroll(checkbox)

    def load_draft(self, values):
        values = set(values)
        visible = "".join(
            chr(value) for value in sorted(values)
            if not is_invisible_boundary(value)
        )
        self.visible_text.delete("1.0", "end")
        self.visible_text.insert("1.0", visible)
        for codepoint, variable in self.space_variables.items():
            variable.set(codepoint in values)
        for child in self.extra_frame.winfo_children():
            child.destroy()
        self.extra_variables.clear()
        for codepoint in sorted(values):
            if (
                is_invisible_boundary(codepoint)
                and codepoint not in self.space_variables
            ):
                self._add_extra(codepoint)

    def restore_defaults(self):
        if self.busy:
            return
        self.load_draft(DEFAULT_BOUNDARY_CODEPOINTS)
        self.status.set("已恢复代码中的默认边界，尚未保存。")

    def add_codepoint(self):
        if self.busy:
            return
        raw = self.codepoint_text.get().strip().upper()
        if raw.startswith("U+"):
            raw = raw[2:]
        elif raw.startswith("0X"):
            raw = raw[2:]
        try:
            if not raw or any(character not in "0123456789ABCDEF" for character in raw):
                raise ValueError("请输入十六进制Unicode码点")
            codepoint = int(raw, 16)
            validate_boundary_codepoints([codepoint])
        except ValueError as error:
            self.status.set(str(error))
            return
        if codepoint in self.space_variables:
            self.space_variables[codepoint].set(True)
        elif is_invisible_boundary(codepoint):
            self._add_extra(codepoint)
        else:
            current = self.visible_text.get("1.0", "end-1c")
            if chr(codepoint) not in current:
                self.visible_text.insert("end-1c", chr(codepoint))
        self.codepoint_text.set("")
        self.status.set(f"已加入 U+{codepoint:04X}，尚未保存。")

    def values(self):
        values = {
            codepoint for codepoint, variable in self.space_variables.items()
            if variable.get()
        }
        values.update(
            codepoint for codepoint, variable in self.extra_variables.items()
            if variable.get()
        )
        for character in self.visible_text.get("1.0", "end-1c"):
            if character in " \r\n":
                continue
            codepoint = ord(character)
            if is_invisible_boundary(codepoint):
                raise ValueError(
                    f"可见字符框含不可见字符 U+{codepoint:04X}；"
                    "请删除后通过下方勾选或添加码点设置"
                )
            values.add(codepoint)
        result = sorted(values)
        validate_boundary_codepoints(result)
        return {"boundary_codepoints": result}

    def save(self):
        if self.busy or self.closed or self.commits.operation_depth:
            return False
        try:
            values = self.values()
        except ValueError as error:
            self.status.set(str(error))
            safe_show_error("边界设置无效", str(error), self.dialog)
            return False
        self.busy = True
        try:
            with self.commits.operation():
                if not self.commits.accept_draft(self.config, values, self.dialog):
                    self.status.set("保存失败，草稿仍保留；本次边界设置未生效。")
                    return False
                # 消费保存成功事实；不通知尚未持久化的候选名单。
                self.dialog._root().event_generate("<<SearchBoundariesChanged>>")
                self._destroy()
                return True
        finally:
            self.busy = False

    def _destroy(self):
        unregister_popup(self.dialog)
        self.closed = True
        self.dialog.destroy()

    def close(self):
        if self.busy or self.commits.operation_depth:
            return False
        if not self.closed:
            self._destroy()
        return True


class GlobalSettingsHome:
    """全局设置只组织设置入口；白名单草稿由子窗口独立拥有。"""

    def __init__(self, parent, commits):
        self.commits = commits
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("全局设置")
        self.dialog.configure(bg=THEME["bg"])
        self.dialog.transient(parent)
        self.closed = False
        self.boundary_dialog = None
        take_modal_control(self.dialog)
        center_window(self.dialog, 420, 180)

        body = styled_frame(self.dialog)
        body.pack(fill="both", expand=True, padx=18, pady=(18, 8))
        styled_button(
            body, "全词匹配边界", self.open_boundaries, width=18,
        ).pack(anchor="w")

        bottom = styled_frame(self.dialog)
        bottom.pack(fill="x", padx=18, pady=(0, 14))
        styled_button(
            bottom, "关闭", self.close, width=10,
        ).pack(side="right")
        # 与其他弹窗共用登记机制：Esc关闭栈顶窗口，
        # 子窗口关闭后归还父窗口的模态控制。
        register_popup(self.dialog, self.close)

    def open_boundaries(self):
        if self.closed or self.commits.operation_depth:
            return None
        current = self.boundary_dialog
        if current is not None and not current.closed:
            current.dialog.lift()
            current.dialog.focus_set()
            return current
        self.boundary_dialog = GlobalSettingsDialog(
            self.dialog, self.commits,
        )
        return self.boundary_dialog

    def close(self):
        if self.commits.operation_depth:
            return False
        if self.closed:
            return True
        current = self.boundary_dialog
        if current is not None and not current.closed:
            if not current.close():
                return False
        unregister_popup(self.dialog)
        self.closed = True
        self.dialog.destroy()
        return True


def open_global_settings(parent, commits):
    return GlobalSettingsHome(parent, commits)