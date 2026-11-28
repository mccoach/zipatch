# -*- coding: utf-8 -*-

"""扫描合并的完整来源链接树；只交付选择，不读取正文或规划发布。"""

import tkinter as tk
from tkinter import ttk

from core.constants import THEME
from core.work_selection import (
    change_hierarchical_selection, validate_selection_parents,
)
from ui.dialogs import create_managed_text_box
from ui.theme import styled_button, styled_frame
from ui.window_manager import (
    center_window, register_popup, take_modal_control, unregister_popup,
)
from ui.work_scope import LINK_LABELS


def select_source_tree(
    parent, commits, nodes, *, related_groups=(), selectable=True,
    title="集中选择本次来源链接", introduction=None,
):
    """共用对象树；业务提供固定节点、关联组和明确交互政策。"""
    nodes = tuple(nodes)
    parents = validate_selection_parents(node.parent_index for node in nodes)
    if not nodes:
        return ()
    states = (True,) * len(nodes)
    decision = None
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)

    controls = styled_frame(dialog)
    controls.pack(side="bottom", fill="x", padx=14, pady=12)
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=14, pady=(12, 0))
    tk.Label(
        body,
        text=introduction or (
            "已完成全部可用来源的目录元信息预扫描，包括链接目标及根外目录；"
            "尚未读取普通文件正文。\n"
            "取消父链接会取消全部下游；选中子链接自动选中父链接；"
            "重新选中父链接会选中整个子树。\n"
            "同一实际目录的不同名义路线分别选择；普通来源自动保留。"
            "点击节点或按空格切换，双击展开／折叠。\n"
            "取消不能撤销已经发生的预扫描；只有所选路线用于后续正文获取。"
        ),
        bg=THEME["bg"], fg=THEME["fg_label"],
        anchor="w", justify="left", wraplength=1000,
        font=THEME["font_main"],
    ).pack(fill="x", pady=(0, 10))

    area = styled_frame(body)
    area.pack(fill="both", expand=True)
    # 样式只用于来源授权树，不改变其他页面或授权窗口。
    # 行背景表达授权状态；当前操作节点由focus定位，
    # 不使用系统选中条覆盖已选／未选颜色。
    style = ttk.Style(dialog)
    style.configure(
        "ZipatchSource.Treeview",
        font=(THEME["font_main"][0], 11),
        rowheight=34,
        background="#eeebe6",
        fieldbackground="#eeebe6",
        foreground="#77736c",
    )
    style.configure(
        "ZipatchSource.Treeview.Heading",
        font=(THEME["font_main"][0], 11, "bold"),
    )
    tree = ttk.Treeview(
        area, columns=("kind", "target"), show="tree headings",
        selectmode="none", height=15, style="ZipatchSource.Treeview",
    )
    tree.tag_configure(
        "authorized", background="#dce5dd", foreground="#30483b",
    )
    tree.tag_configure(
        "not_authorized", background="#eeebe6", foreground="#77736c",
    )
    tree.heading("#0", text="选择 / 名义链接路线")
    tree.heading("kind", text="类型")
    tree.heading("target", text="原定义 / 文件硬链接数量")
    tree.column("#0", width=450, minwidth=180, stretch=True)
    tree.column("kind", width=160, minwidth=130, stretch=False)
    tree.column("target", width=350, minwidth=180, stretch=True)
    vertical = tk.Scrollbar(area, orient="vertical", command=tree.yview)
    horizontal = tk.Scrollbar(area, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    area.grid_columnconfigure(0, weight=1)
    area.grid_rowconfigure(0, weight=1)
    for number, node in enumerate(nodes):
        tree.insert(
            "" if node.parent_index is None else str(node.parent_index),
            "end", iid=str(number), open=True,
            values=(
                LINK_LABELS[node.kind],
                node.target_description,
            ),
        )
    statistics = tk.StringVar(master=dialog)
    tk.Label(
        body, textvariable=statistics, anchor="w",
        bg=THEME["bg"], fg=THEME["fg_dim"], font=THEME["font_main"],
    ).pack(fill="x", pady=(8, 0))

    def refresh():
        dialog._source_tree_states = states
        for number, node in enumerate(nodes):
            tree.item(
                str(number),
                text=(
                    ("☑ " if states[number] else "☐ ") if selectable else ""
                ) + str(node.nominal_path),
                tags=("authorized" if states[number] else "not_authorized",),
            )
        statistics.set(
            f"所选链接路线 {sum(states)} / {len(states)}；"
            "节点数按名义路线展示，成果按不同实际链接入口统计。"
        )

    def toggle(number):
        nonlocal states
        if not selectable:
            return
        enabled = not states[number]
        pending = [number]
        processed = set()
        while pending:
            current = pending.pop()
            if current in processed:
                continue
            processed.add(current)
            previous = states
            states = change_hierarchical_selection(
                states, parents, current, enabled,
            )
            affected = {
                index for index, (old, new) in enumerate(zip(previous, states))
                if old != new
            } | {current}
            for group in related_groups:
                if affected.intersection(group):
                    pending.extend(index for index in group if index not in processed)
        refresh()

    def clicked(event):
        item = tree.identify_row(event.y)
        if not item or tree.identify_region(event.x, event.y) not in ("tree", "cell"):
            return None
        if tree.identify_element(event.x, event.y).endswith("indicator"):
            return None
        tree.focus(item)
        tree.focus_set()
        toggle(int(item))
        return "break"

    def keyboard(event=None):
        item = tree.focus()
        if item:
            toggle(int(item))
        return "break"

    tree.bind("<ButtonRelease-1>", clicked)
    tree.bind("<space>", keyboard)
    dialog._source_tree = tree
    dialog._source_tree_toggle = toggle

    def set_all(value):
        nonlocal states
        states = (value,) * len(nodes)
        refresh()

    def show_details():
        current = tree.focus()
        number = int(current) if current else 0
        node = nodes[number]
        definition = node.definition
        window = tk.Toplevel(dialog)
        window.title("来源链接固定详情")
        window.configure(bg=THEME["bg"])
        window.transient(dialog)
        footer = styled_frame(window)
        footer.pack(side="bottom", fill="x", padx=14, pady=12)
        content = styled_frame(window)
        content.pack(fill="both", expand=True, padx=14, pady=(12, 0))
        lines = (
            f"名义节点：{node.nominal_path}\n"
            f"实际入口：{node.entry_path}\n"
            f"类型：{LINK_LABELS[node.kind]}\n"
            f"父链接编号：{None if node.parent_index is None else node.parent_index + 1}\n"
        )
        if node.metadata is not None:
            lines += (
                f"文件对象身份：{node.metadata.object_identity}\n"
                f"文件系统报告的链接数量：{node.metadata.link_count}\n"
                "硬链接没有独立指向定义，各名称共享同一文件对象。\n"
                "正文写回会被其他关联名称共同看到；名称移除与正文写回不同。\n"
                "未枚举范围外的其他名称，数量不代表已发现的路径数。\n"
            )
        elif node.specification is not None:
            from core.link_paths import validate_link_specification

            specification = node.specification
            lines += (
                f"保存原定义：{specification.raw_target!r}\n"
                f"相对定义：{specification.relative}\n"
                "在恢复位置的直接引用："
                f"{validate_link_specification(specification, node.entry_path)}\n"
                "这是清单保存的待恢复定义，不是当前现场入口观察；"
                "不提供当前入口身份或版本，也不查询引用目标。\n"
            )
        else:
            lines += (
                f"原定义：{definition.raw_target!r}\n"
                f"直接引用位置：{definition.direct_target}\n"
                f"相对定义：{definition.relative}\n"
                f"入口身份：{definition.object_identity}\n"
                "直接引用位置不等于最终实际文件对象；不排除范围外反向引用风险。\n"
            )
        if node.impact:
            lines += "\n【本次影响】\n" + node.impact
        if node.work_indices:
            lines += "\n关联工作编号：" + "、".join(
                str(index + 1) for index in node.work_indices
            )
        lines += (
            "\n\n这里只展示固定事实；确认后仍复核身份、定义和链接数量。"
            "窗口不读取正文，不重新扫描或扩大工作范围。"
        )
        create_managed_text_box(
            content, "本次固定节点", lines,
            height=15, readonly=True, commits=commits,
            enable_favorites=False, enable_clear=False,
            enable_wrap_toggle=False, enable_copy=True,
        )

        def close():
            unregister_popup(window)
            window.destroy()
            return True

        styled_button(footer, "关闭", close).pack(side="right")
        try:
            center_window(window, 880, min(600, window.winfo_screenheight() - 100))
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

    if selectable:
        styled_button(controls, "全选", lambda: set_all(True)).pack(
            side="left", padx=(0, 8),
        )
        styled_button(controls, "全不选", lambda: set_all(False)).pack(
            side="left", padx=(0, 8),
        )
    styled_button(controls, "节点详情", show_details).pack(side="left")
    cancel = styled_button(
        controls, "取消任务" if selectable else "拒绝整包",
        lambda: finish(None),
    )
    cancel.pack(side="right", padx=(8, 0))
    styled_button(
        controls, "按所选继续" if selectable else "授权整包",
        lambda: finish(tuple(
            "authorize" if value else "skip" for value in states
        )),
        danger=True,
    ).pack(side="right")
    refresh()
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


def make_source_node_selector(parent, commits):
    def select(nodes):
        return select_source_tree(parent, commits, nodes)

    return select