# -*- coding: utf-8 -*-

"""业务页面固定区优先分配；只有正文框吸收高度变化。"""

import tkinter as tk


MINIMUM_TEXT_LINES = 3


def protect_page_controls(parent):
    """创建完成后一次整理业务面板，不处理其他弹窗。

    固定头部保持原顺序；固定底部先分配，正文取得剩余高度。
    重新登记时保留padding、fill、expand等原参数，
    不依赖多次before插入隐式形成最终顺序。
    """
    for child in parent.winfo_children():
        if isinstance(child, tk.Text):
            child.configure(height=MINIMUM_TEXT_LINES)
        protect_page_controls(child)

    packed = parent.pack_slaves()
    options = {child: dict(child.pack_info()) for child in packed}
    flexible = [
        child for child in packed
        if parent.tk.getboolean(options[child].get("expand", False))
        and options[child].get("fill") in ("both", "y")
    ]
    if not flexible:
        return
    first_position = packed.index(flexible[0])
    trailing = [
        child for child in packed[first_position + 1:]
        if child not in flexible and options[child].get("side") == "top"
    ]
    if not trailing:
        return

    existing_bottom = [
        child for child in packed if options[child].get("side") == "bottom"
    ]
    retained = [
        child for child in packed
        if child not in trailing and child not in existing_bottom
        and child not in flexible
    ]
    # bottom从下向上分配；后显示的固定行必须先登记。
    ordered = [
        *retained, *existing_bottom, *reversed(trailing), *flexible,
    ]
    for child in packed:
        child.pack_forget()
    for child in ordered:
        settings = dict(options[child])
        settings.pop("in", None)
        if child in trailing:
            settings["side"] = "bottom"
        child.pack(**settings)