# -*- coding: utf-8 -*-

"""业务页设置行的共同位置政策；不拥有业务执行或配置保存逻辑。"""

import tkinter as tk

from core.constants import THEME
from ui.theme import styled_button, styled_frame
from ui.widgets import make_checkbutton


# 数字只决定共同选项的相对顺序，不代表必须保留的物理列位置。
COMMON_OPTION_COLUMNS = {
    "open_after_done": 0,
    "open_backup_after_done": 0,
    "normalize_text_newlines": 1,
    "show_actual_path": 2,
}



def create_page_settings(parent, commits, config, options, actions=()):
    """返回设置行、专属设置容器及变量映射。

    options为(field, label, tooltip)序列。
    actions为(key, label, callback)序列，只包含设置入口；
    newline固定在最右侧，展示说明由页面放在执行按钮左侧。
    共同项保持相对顺序，缺项不占位，优先保证选项连续；
    打开选项直接区分成果与备份，不只依靠悬浮提示。
    """
    options = tuple(options)
    actions = tuple(actions)
    row = styled_frame(parent, bg=THEME["bg_panel"])
    row.pack(fill="x", pady=(6, 4))
    row.grid_columnconfigure(1, weight=1)

    checks = styled_frame(row, bg=THEME["bg_panel"])
    checks.grid(row=0, column=0, sticky="w")
    specific = styled_frame(row, bg=THEME["bg_panel"])
    specific.grid(row=0, column=1, sticky="w", padx=(8, 0))
    buttons = styled_frame(row, bg=THEME["bg_panel"])
    buttons.grid(row=0, column=2, sticky="e", padx=(12, 0))

    common_columns = [
        COMMON_OPTION_COLUMNS[field]
        for field, _, _ in options if field in COMMON_OPTION_COLUMNS
    ]
    if len(common_columns) != len(set(common_columns)):
        raise ValueError("同一页面不能重复占用共同选项位置")
    # 保持共同选项的相对顺序，但缺项不占位，实际列连续排列。
    # 不再设置固定列宽，避免较短标签之后留下大块空白。
    common_fields = [
        field for field, _, _ in sorted(
            options,
            key=lambda option: COMMON_OPTION_COLUMNS.get(option[0], 999),
        )
        if field in COMMON_OPTION_COLUMNS
    ]
    actual_columns = {
        field: column for column, field in enumerate(common_fields)
    }
    open_labels = {
        "open_after_done": "完成后打开成果",
        "open_backup_after_done": "完成后打开备份",
    }

    variables = {}
    for field, label, tooltip in options:
        label = open_labels.get(field, label)
        variable = tk.BooleanVar(master=parent, value=config[field])
        variables[field] = variable
        common = field in COMMON_OPTION_COLUMNS
        widget = make_checkbutton(
            checks if common else specific, label, variable,
            commits=commits, config=config, field=field,
            tooltip_text=tooltip,
        )
        if common:
            widget.grid(
                row=0, column=actual_columns[field],
                sticky="w", pady=2,
                padx=(0, 8) if actual_columns[field] < len(common_fields) - 1 else 0,
            )
        else:
            widget.pack(side="left", padx=(0, 8))

    # 只编排设置入口；换行设置靠右，其他业务设置保持关联顺序。
    ordered_actions = sorted(
        actions, key=lambda action: action[0] == "newline",
    )
    for key, label, callback in ordered_actions:
        button = styled_button(buttons, label, callback, width=9)
        button._page_setting_key = key
        button.pack(side="left", padx=(4, 0))
    return row, specific, variables