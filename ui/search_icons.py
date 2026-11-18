# -*- coding: utf-8 -*-

"""查找和全局设置共用的小尺寸线性图标，保留原生Button交互。"""

import math
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from core.constants import THEME
from ui.widgets import add_tooltip


ICON_SEGMENTS = {
    "previous": (
        (8, 13, 8, 3), (8, 3, 4, 7), (8, 3, 12, 7),
    ),
    "next": (
        (8, 3, 8, 13), (8, 13, 4, 9), (8, 13, 12, 9),
    ),
    "replace": (
        (2, 5, 10, 5), (10, 5, 7, 2), (10, 5, 7, 8),
        (4, 12, 13, 12), (13, 10, 13, 14),
    ),
    "replace_all": (
        (2, 4, 11, 4), (11, 4, 8, 1), (11, 4, 8, 7),
        (2, 10, 11, 10), (11, 10, 8, 7), (11, 10, 8, 13),
        (14, 2, 14, 14),
    ),
    "close": ((4, 4, 12, 12), (4, 12, 12, 4)),
    "case": (
        (1, 13, 4, 3), (4, 3, 7, 13), (2, 9, 6, 9),
        (10, 7, 13, 7), (13, 7, 13, 13),
        (13, 9, 10, 9), (10, 9, 9, 11),
        (9, 11, 10, 13), (10, 13, 13, 13),
        (13, 13, 14, 13),
    ),
    "whole": (
        (1, 3, 1, 13), (15, 3, 15, 13),
        (4, 7, 7, 7), (7, 7, 7, 12),
        (7, 9, 4, 9), (4, 9, 4, 12), (4, 12, 7, 12),
        (10, 3, 10, 12), (10, 7, 12, 7),
        (12, 7, 13, 8), (13, 8, 13, 11),
        (13, 11, 12, 12), (12, 12, 10, 12),
    ),
    "wildcard": (
        (4, 3, 4, 11), (1, 5, 7, 9), (1, 9, 7, 5),
        (10, 4, 12, 3), (12, 3, 14, 4), (14, 4, 14, 6),
        (14, 6, 12, 8), (12, 8, 12, 10), (12, 13, 12, 13),
    ),
}

_gear = []
for number in range(8):
    angle = number * math.pi / 4
    first = (
        8 + 5 * math.cos(angle),
        8 + 5 * math.sin(angle),
    )
    second = (
        8 + 5 * math.cos(angle + math.pi / 4),
        8 + 5 * math.sin(angle + math.pi / 4),
    )
    _gear.append((*first, *second))
    _gear.append((
        8 + 5 * math.cos(angle), 8 + 5 * math.sin(angle),
        8 + 7 * math.cos(angle), 8 + 7 * math.sin(angle),
    ))
for number in range(12):
    first = number * math.pi / 6
    second = (number + 1) * math.pi / 6
    _gear.append((
        8 + 2 * math.cos(first), 8 + 2 * math.sin(first),
        8 + 2 * math.cos(second), 8 + 2 * math.sin(second),
    ))
ICON_SEGMENTS["settings"] = tuple(_gear)
del _gear


# 样式只属于查找工具栏，不增加全局主题或用户配置。
MODE_SELECTED_BACKGROUND = "#d5e5da"
MODE_SELECTED_BORDER = "#587762"
ACTION_BACKGROUND = "#d2d2d2"
BUTTON_HOVER_BACKGROUND = "#e8e8e8"
ICON_SUPERSAMPLING = 4


def make_icon(parent, name):
    """高分辨率统一绘制后缩小，获得平滑线条与圆润线端。"""
    scale = max(1.0, float(parent.tk.call("tk", "scaling")) / (4 / 3))
    size = max(16, round(16 * scale))
    drawing_scale = size / 16 * ICON_SUPERSAMPLING
    image = Image.new(
        "RGBA", (size * ICON_SUPERSAMPLING,) * 2, (0, 0, 0, 0),
    )
    drawing = ImageDraw.Draw(image)
    width = max(1, round(1.0 * drawing_scale))
    radius = width / 2
    for x1, y1, x2, y2 in ICON_SEGMENTS[name]:
        coordinates = tuple(
            coordinate * drawing_scale
            for coordinate in (x1, y1, x2, y2)
        )
        drawing.line(coordinates, fill=THEME["fg"], width=width)
        for x, y in (coordinates[:2], coordinates[2:]):
            drawing.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=THEME["fg"],
            )
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(image, master=parent)


def linear_icon_button(parent, name, command, description, *, text=""):
    """原生按钮保留焦点与键盘交互；模式和动作采用不同视觉语义。"""
    image = make_icon(parent, name)
    scale = image.width() / 16
    mode = name in ("case", "whole", "wildcard")
    background = THEME["bg"] if mode or text else ACTION_BACKGROUND
    button = tk.Button(
        parent, image=image, text=text, compound="left", command=command,
        bg=background, fg=THEME["fg"],
        activebackground=BUTTON_HOVER_BACKGROUND,
        activeforeground=THEME["fg"],
        relief="flat", bd=1, highlightthickness=1,
        highlightbackground=background,
        highlightcolor=THEME["accent"],
        padx=round((5 if text else 2) * scale),
        pady=round((4 if text else 2) * scale),
        takefocus=True, cursor="hand2", font=THEME["font_main"],
    )
    button._linear_icon = image
    button._mode_selected = False
    button._normal_background = background
    add_tooltip(button, description)

    def enter(event=None):
        if str(button.cget("state")) != "disabled":
            button.configure(
                bg=MODE_SELECTED_BACKGROUND
                if button._mode_selected else BUTTON_HOVER_BACKGROUND,
            )

    def leave(event=None):
        button.configure(
            bg=MODE_SELECTED_BACKGROUND
            if button._mode_selected else button._normal_background,
        )

    button.bind("<Enter>", enter, add="+")
    button.bind("<Leave>", leave, add="+")
    return button


def set_mode_button_state(button, label, selected):
    """开启态同时使用边框、按下效果及背景，不只依赖颜色。"""
    button._mode_selected = selected
    button.configure(
        relief="sunken" if selected else "flat",
        bg=MODE_SELECTED_BACKGROUND
        if selected else button._normal_background,
        activebackground=MODE_SELECTED_BACKGROUND
        if selected else BUTTON_HOVER_BACKGROUND,
        highlightbackground=MODE_SELECTED_BORDER
        if selected else button._normal_background,
    )
    button._tooltip.text = f"{label}：{'已开启' if selected else '已关闭'}；点击切换"