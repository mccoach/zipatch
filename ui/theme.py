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


def styled_button(parent, text, command, width=8, accent=False, danger=False):
    if danger:
        bg_normal = THEME["danger"]
        fg_normal = THEME["fg_on_dark"]
    else:
        bg_normal = THEME["bg_btn_accent"] if accent else THEME["bg_btn"]
        fg_normal = THEME["fg_on_dark"] if accent else THEME["fg"]

    bg_hover = THEME["bg_btn_hover"]
    bg_active = THEME["bg_btn_selected"]

    btn = tk.Button(
        parent,
        text=text,
        command=command,
        width=width,
        bg=bg_normal,
        fg=fg_normal,
        activebackground=bg_active,
        activeforeground=THEME["fg_on_dark"],
        relief="flat",
        bd=0,
        padx=8,
        pady=4,
        cursor="hand2",
        font=THEME["font_main"],
    )

    btn._normal_bg = bg_normal
    btn._normal_fg = fg_normal

    btn.bind(
        "<Enter>",
        lambda e: btn.config(bg=bg_hover, fg=THEME["fg_on_dark"]),
    )
    btn.bind(
        "<Leave>",
        lambda e: btn.config(bg=btn._normal_bg, fg=btn._normal_fg),
    )

    return btn


def styled_frame(parent, bg=None):
    return tk.Frame(parent, bg=bg or THEME["bg"])


def styled_panel(parent):
    return tk.Frame(
        parent,
        bg=THEME["bg_panel"],
        highlightbackground=THEME["border"],
        highlightthickness=1,
    )


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
        textvariable=textvariable,
        bg=THEME["bg_input"],
        fg=THEME["fg"],
        insertbackground=THEME["fg"],
        relief="flat",
        bd=4,
        font=THEME["font_main"],
    )
    entry.pack(fill="x", expand=True)

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
        maxundo=-1,
        autoseparators=True,
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


def styled_scrolled_text(parent, height, mono=False, wrap="word"):
    """
    兼容旧调用。
    """
    return styled_text_with_scrollbars(
        parent,
        height=height,
        mono=mono,
        wrap=wrap,
        readonly=False,
    )
