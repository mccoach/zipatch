# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME



def apply_global_theme(root):
    root.configure(bg=THEME["bg"])
    root.option_add("*Font", THEME["font_main"])
    root.option_add("*Background", THEME["bg"])
    root.option_add("*Foreground", THEME["fg"])

    root.option_add("*Entry.Background", THEME["bg_input"])
    root.option_add("*Entry.Foreground", THEME["fg"])
    root.option_add("*Entry.InsertBackground", THEME["fg"])
    root.option_add("*Entry.Relief", "flat")
    root.option_add("*Entry.BorderWidth", "0")

    root.option_add("*Text.Background", THEME["bg_input"])
    root.option_add("*Text.Foreground", THEME["fg"])
    root.option_add("*Text.InsertBackground", THEME["fg"])
    root.option_add("*Text.Relief", "flat")
    root.option_add("*Text.BorderWidth", "0")

    root.option_add("*Label.Background", THEME["bg"])
    root.option_add("*Label.Foreground", THEME["fg_label"])

    root.option_add("*Checkbutton.Background", THEME["bg"])
    root.option_add("*Checkbutton.Foreground", THEME["fg_label"])
    root.option_add("*Checkbutton.ActiveBackground", THEME["bg"])
    root.option_add("*Checkbutton.ActiveForeground", THEME["fg"])
    root.option_add("*Checkbutton.SelectColor", THEME["bg_btn_accent"])

    root.option_add("*Radiobutton.Background", THEME["bg"])
    root.option_add("*Radiobutton.Foreground", THEME["fg_label"])
    root.option_add("*Radiobutton.ActiveBackground", THEME["bg"])
    root.option_add("*Radiobutton.ActiveForeground", THEME["fg"])
    root.option_add("*Radiobutton.SelectColor", THEME["accent"])


def button_width(text, *, compact=False):
    """文字按钮采用有限尺寸档；不测量字体，不让长按钮扩大整组。"""
    if compact and text == "选中 OP":
        return 8
    length = len(text)
    if compact:
        if length <= 2:
            return 4
        if length <= 4:
            return 6
        return min(16, 2 * ((length + 2) // 2))
    if length <= 2:
        return 6
    if length <= 4:
        return 8
    if length <= 6:
        return 10
    if length <= 8:
        return 12
    if length <= 10:
        return 14
    return 16


def styled_button(
    parent, text, command, width=8, accent=False, danger=False, *,
    compact=False, bold=False,
):
    # width参数保留调用兼容；有限尺寸由共同规则决定，
    # 避免各页历史参数使同类按钮大小不一致。
    if danger:
        bg_normal = THEME["danger"]
        fg_normal = THEME["fg_on_dark"]
        bg_hover = "#91463f"
        bg_active = "#824039"
    else:
        bg_normal = THEME["bg_btn_accent"] if accent else THEME["bg_btn"]
        fg_normal = THEME["fg_on_dark"] if accent else THEME["fg"]
        bg_hover = THEME["bg_btn_hover"] if accent else "#e0dbd2"
        bg_active = THEME["bg_btn_selected"] if accent else "#c5c0b6"

    btn = tk.Button(
        parent,
        text=text,
        command=command,
        width=button_width(text, compact=compact),
        bg=bg_normal,
        fg=fg_normal,
        activebackground=bg_active,
        activeforeground=THEME["fg_on_dark"] if danger or accent else THEME["fg"],
        relief="raised",
        overrelief="raised",
        bd=1,
        highlightthickness=1,
        highlightbackground=parent.cget("bg"),
        highlightcolor=THEME["accent"],
        padx=4 if compact else 6,
        pady=1 if compact else 3,
        cursor="hand2",
        takefocus=True,
        font=(
            (THEME["font_main"][0], THEME["font_main"][1], "bold")
            if bold else THEME["font_main"]
        ),
    )
    btn._normal_bg = bg_normal
    btn._normal_fg = fg_normal

    def enter(event=None):
        if str(btn.cget("state")) != "disabled":
            btn.configure(
                bg=bg_hover,
                fg=THEME["fg_on_dark"] if danger or accent else THEME["fg"],
            )

    def leave(event=None):
        btn.configure(bg=btn._normal_bg, fg=btn._normal_fg)

    btn.bind("<Enter>", enter, add="+")
    btn.bind("<Leave>", leave, add="+")
    return btn


def styled_frame(parent, bg=None):
    return tk.Frame(parent, bg=bg or THEME["bg"])



def styled_label_frame(parent, text):
    return tk.LabelFrame(
        parent,
        text=f"  {text}  ",
        bg=THEME["bg_panel"],
        fg=THEME["fg_dim"],
        font=THEME["font_main"],
        bd=1,
        relief="flat",
        highlightbackground=THEME["border"],
        highlightthickness=1,
        labelanchor="nw",
    )


def styled_entry(parent, textvariable):
    frame = tk.Frame(parent, bg=THEME["border_light"], padx=1, pady=1)

    entry = tk.Entry(
        frame,
        width=1,
        textvariable=textvariable,
        bg=THEME["bg_input"],
        fg=THEME["fg"],
        insertbackground=THEME["fg"],
        relief="flat",
        bd=4,
        font=THEME["font_main"],
    )
    entry.pack(side="left", fill="x", expand=True)

    return frame, entry


def styled_text_with_scrollbars(
    parent,
    height,
    mono=False,
    wrap="word",
    readonly=False,
):
    """
    带横向/纵向滚动条的 Text。

    替代 ScrolledText：
    - 竖向滚动条；
    - 横向滚动条；
    - wrap=none 时可横向滚动；
    - wrap=word 时自动换行。
    """
    font = THEME["font_mono"] if mono else THEME["font_main"]

    outer = tk.Frame(parent, bg=THEME["border"], padx=1, pady=1)

    inner = tk.Frame(outer, bg=THEME["bg_input"])
    inner.pack(fill="both", expand=True)

    text = tk.Text(
        inner,
        width=1,
        height=height,
        wrap=wrap,
        bg=THEME["bg_input"],
        fg=THEME["fg"],
        insertbackground=THEME["fg"],
        selectbackground=THEME["select_bg"],
        selectforeground=THEME["select_fg"],
        relief="flat",
        bd=4,
        font=font,
        undo=not readonly,
        maxundo=0 if readonly else -1,
        autoseparators=not readonly,
    )

    y_scroll = tk.Scrollbar(
        inner,
        orient="vertical",
        command=text.yview,
        bg=THEME["bg_btn"],
        troughcolor=THEME["bg_input"],
        relief="flat",
    )
    x_scroll = tk.Scrollbar(
        inner,
        orient="horizontal",
        command=text.xview,
        bg=THEME["bg_btn"],
        troughcolor=THEME["bg_input"],
        relief="flat",
    )

    text.configure(
        yscrollcommand=y_scroll.set,
        xscrollcommand=x_scroll.set,
    )

    text.grid(row=0, column=0, sticky="nsew")
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll.grid(row=1, column=0, sticky="ew")

    inner.grid_rowconfigure(0, weight=1)
    inner.grid_columnconfigure(0, weight=1)

    if readonly:
        text.configure(state="disabled")

    return outer, text

