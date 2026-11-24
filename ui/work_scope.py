# -*- coding: utf-8 -*-

"""固定工作项的共同勾选视图；只返回决定，不规划或执行文件系统工作。"""

import tkinter as tk
from dataclasses import dataclass

from core.constants import THEME
from ui.dialogs import create_managed_text_box
from ui.theme import styled_button, styled_frame
from ui.window_manager import (
    center_window, register_popup, take_modal_control, unregister_popup,
)


GROUP_ORDER = (
    "文件软链接", "文件夹软链接", "文件夹联接（Junction）",
    "混合链接工作项", "普通工作项",
)
LINK_LABELS = {
    "file_symlink": "文件软链接",
    "directory_symlink": "文件夹软链接",
    "junction": "文件夹联接（Junction）",
}


@dataclass(frozen=True)
class WorkScopeChoice:
    label: str
    group: str
    fixed_skip: bool = False


def link_group(accesses=(), kinds=()):
    found = set(kinds)
    for access in accesses:
        found.update(
            definition.kind
            for definition in (
                *access.followed_links,
                *((access.entry_link,) if access.entry_link is not None else ()),
            )
        )
    found.intersection_update(LINK_LABELS)
    if len(found) > 1:
        return "混合链接工作项"
    return LINK_LABELS[next(iter(found))] if found else "普通工作项"


def select_work_scope(parent, commits, *, title, introduction, choices, details):
    """返回逐项authorize/skip；None是取消，全不选不是取消。"""
    choices = tuple(choices)
    if any(
        not isinstance(choice, WorkScopeChoice)
        or choice.group not in GROUP_ORDER
        or type(choice.fixed_skip) is not bool
        for choice in choices
    ):
        raise TypeError("选择窗口必须消费明确的工作展示项")
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    decision = None
    variables = tuple(
        tk.BooleanVar(master=dialog, value=not choice.fixed_skip)
        for choice in choices
    )
    dialog._scope_variables = variables
    controls = styled_frame(dialog)
    controls.pack(side="bottom", fill="x", padx=14, pady=12)
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=14, pady=(12, 0))
    tk.Label(
        body, text=introduction, anchor="w", justify="left",
        wraplength=1000, bg=THEME["bg"], fg=THEME["fg_label"],
        font=THEME["font_main"],
    ).pack(fill="x", pady=(0, 10))
    viewport = styled_frame(body)
    viewport.pack(fill="both", expand=True)
    canvas = tk.Canvas(viewport, bg=THEME["bg"], highlightthickness=0, width=1)
    scrollbar = tk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    # 只路由本列表区域的滚轮，不使用bind_all影响其他窗口。
    # 窗口路径作为绑定标签，区域销毁时其回调由Tkinter统一释放。
    scroll_tag = viewport._w

    def scroll_list(event):
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
        canvas.yview_scroll(units, "units")
        return "break"

    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        viewport.bind(sequence, scroll_list)

    def route_list_scroll(widget):
        widget.bindtags((
            scroll_tag,
            *(tag for tag in widget.bindtags() if tag != scroll_tag),
        ))
        for child in widget.winfo_children():
            route_list_scroll(child)

    def set_members(numbers, value):
        for number in numbers:
            variables[number].set(value and not choices[number].fixed_skip)

    rows = styled_frame(canvas)
    embedded = canvas.create_window((0, 0), window=rows, anchor="nw")
    rows.bind(
        "<Configure>",
        lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.bind(
        "<Configure>",
        lambda event: canvas.itemconfigure(embedded, width=event.width),
    )
    for group in GROUP_ORDER:
        members = tuple(
            (number, choice) for number, choice in enumerate(choices)
            if choice.group == group
        )
        if not members:
            continue
        heading = styled_frame(rows)
        heading.pack(fill="x", pady=(8, 4))
        tk.Label(
            heading, text=f"{group}（{len(members)}项）", anchor="w",
            bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_title"],
        ).pack(side="left", fill="x", expand=True)
        numbers = tuple(number for number, _ in members)
        enabled = any(not choice.fixed_skip for _, choice in members)
        for label, value in (("本类全选", True), ("本类全不选", False)):
            button = styled_button(
                heading, label,
                lambda selected=numbers, checked=value:
                set_members(selected, checked),
                width=10,
            )
            button._scope_group = group
            button._scope_group_value = value
            button.configure(state="normal" if enabled else "disabled")
            button.pack(side="left", padx=(8, 0))
        for number, choice in members:
            tk.Checkbutton(
                rows, text=choice.label, variable=variables[number],
                state="disabled" if choice.fixed_skip else "normal",
                anchor="w", justify="left", wraplength=940,
                bg=THEME["bg"], activebackground=THEME["bg"],
                fg=THEME["fg"], selectcolor=THEME["bg_input"],
                font=THEME["font_main"],
            ).pack(fill="x", pady=2)

    route_list_scroll(viewport)

    statistics = tk.StringVar(master=dialog)

    def refresh(*arguments):
        count = sum(
            variable.get() and not choice.fixed_skip
            for variable, choice in zip(variables, choices)
        )
        available = sum(not choice.fixed_skip for choice in choices)
        statistics.set(
            f"已选 {count} / {available} 项；"
            f"未选跳过 {available - count} 项；"
            f"既定跳过 {len(choices) - available} 项"
        )

    for variable in variables:
        variable.trace_add("write", refresh)
    refresh()
    tk.Label(
        body, textvariable=statistics, anchor="w",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
    ).pack(fill="x", pady=(10, 0))

    def set_all(value):
        for variable, choice in zip(variables, choices):
            variable.set(value and not choice.fixed_skip)

    def show_details():
        window = tk.Toplevel(dialog)
        window.title("本次固定范围详情")
        window.configure(bg=THEME["bg"])
        window.transient(dialog)
        footer = styled_frame(window)
        footer.pack(side="bottom", fill="x", padx=14, pady=12)
        content = styled_frame(window)
        content.pack(fill="both", expand=True, padx=14, pady=(12, 0))
        create_managed_text_box(
            content, "完整路径、链接定义及工作范围", details,
            height=24, readonly=True, mono=True, commits=commits,
            enable_favorites=False, enable_clear=False,
            enable_wrap_toggle=False, enable_copy=True,
        )

        def close():
            unregister_popup(window)
            window.destroy()
            return True

        styled_button(footer, "关闭", close, width=10).pack(side="right")
        try:
            center_window(window, 1000, min(740, window.winfo_screenheight() - 100))
            take_modal_control(window)
            register_popup(window, close)
            dialog.wait_window(window)
        finally:
            unregister_popup(window)
            if window.winfo_exists():
                window.destroy()

    def finish(value):
        nonlocal decision
        decision = value
        unregister_popup(dialog)
        dialog.destroy()
        return True

    styled_button(
        controls, "全选", lambda: set_all(True), width=8,
    ).pack(side="left", padx=(0, 8))
    styled_button(
        controls, "全不选", lambda: set_all(False), width=8,
    ).pack(side="left", padx=(0, 8))
    styled_button(
        controls, "查看详情", show_details, width=10,
    ).pack(side="left")
    cancel = styled_button(
        controls, "取消任务", lambda: finish(None), width=12,
    )
    cancel.pack(side="right", padx=(8, 0))
    styled_button(
        controls, "按所选继续",
        lambda: finish(tuple(
            "authorize" if variable.get() and not choice.fixed_skip else "skip"
            for variable, choice in zip(variables, choices)
        )),
        width=16, danger=True,
    ).pack(side="right")
    try:
        center_window(dialog, 1080, min(760, dialog.winfo_screenheight() - 100))
        take_modal_control(dialog)
        register_popup(
            dialog, lambda: finish(None),
            exit_blocker=True, focus_on_register=False,
        )
        dialog.deiconify()
        cancel.focus_set()
        parent.wait_window(dialog)
        return decision
    finally:
        unregister_popup(dialog)
        if dialog.winfo_exists():
            dialog.destroy()

def select_link_work_scope(
    parent, commits, *, title, introduction, choices, details,
):
    """统一链接筛选政策：普通项静默保留，只展示链接相关工作项。

    业务提供已归组的固定展示项；本能力不观察路径、不改变分组，
    不授予未展示的链接范围，也不替代最终执行确认。
    """
    from core.work_selection import project_group_decisions

    choices = tuple(choices)
    if any(
        not isinstance(choice, WorkScopeChoice)
        or choice.group not in GROUP_ORDER
        or type(choice.fixed_skip) is not bool
        for choice in choices
    ):
        raise TypeError("链接筛选必须消费明确的工作展示项")
    numbers = tuple(
        number for number, choice in enumerate(choices)
        if choice.group != "普通工作项"
    )
    selected = (
        select_work_scope(
            parent, commits, title=title,
            introduction=(
                introduction
                + "\n普通工作项自动保留；本窗口只列链接相关项。"
            ),
            choices=tuple(choices[number] for number in numbers),
            details=details,
        )
        if numbers else ()
    )
    if selected is None:
        return None
    return project_group_decisions(
        tuple((number,) for number in numbers),
        selected, len(choices),
        fixed_skips=tuple(
            number for number, choice in enumerate(choices)
            if choice.fixed_skip
        ),
        description="链接工作范围选择",
    )