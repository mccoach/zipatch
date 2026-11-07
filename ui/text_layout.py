# -*- coding: utf-8 -*-

"""文字控件在已分配宽度内换行，不让动态正文拥有横向布局。"""


def bind_allocated_text_wrap(widget, *, width=1):
    """固定字符请求宽度，实际换行宽度只消费布局分配结果。

    适用于Label和Button；不修改父容器尺寸或运行嵌套事件循环。
    高度仍由完整文字决定，因此不会用截断掩盖信息。
    """
    widget.configure(width=width, wraplength=300)

    def resize(event):
        if event.widget is not widget:
            return
        inset = sum(
            widget.winfo_pixels(widget.cget(option))
            for option in ("padx", "borderwidth", "highlightthickness")
        )
        available = max(1, event.width - inset * 2)
        if widget.winfo_pixels(widget.cget("wraplength")) != available:
            widget.configure(wraplength=available)

    widget.bind("<Configure>", resize, add="+")
