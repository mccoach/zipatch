# -*- coding: utf-8 -*-

"""修改包源码标题栏的完整OP选择；不改变正文、配置或执行资格。"""

from services.patch_parser import parse_patch_v3
from ui.favorites import title_bar_button
from ui.message_dialogs import safe_show_error
from ui.window_manager import restore_editor_interaction


def locate_op_selection(source, *, start=0, end=None):
    """一次解析当前正文，按光标或选区返回完整OP字符半开区间。

    光标归属使用左闭右开范围；选区只选择与其实际相交的OP。
    END_OP后的实际分行符属于OP，不包含后续排版或包尾。
    """
    if not isinstance(source, str):
        raise TypeError("OP选择必须消费字符串正文")
    if type(start) is not int or end is not None and type(end) is not int:
        raise TypeError("OP选择位置必须是整数字符边界")
    end = start if end is None else end
    if not 0 <= start <= end <= len(source):
        raise ValueError("OP选择位置越出当前正文")

    ranges = parse_patch_v3(source)["op_ranges"]
    matches = [
        (left, right) for _, left, right in ranges
        if (
            left <= start < right
            if start == end else left < end and start < right
        )
    ]
    if not matches:
        raise ValueError("当前位置或选中范围没有 OP")
    return matches[0][0], matches[-1][1]


def _source_index(widget, source, offset):
    """Python字符边界转Tk索引；补充字符不假定占一个Tcl字符。"""
    prefix = source[:offset]
    line = prefix.count("\n") + 1
    tail = prefix.rsplit("\n", 1)[-1]
    column = int(widget.tk.call("string", "length", tail))
    return widget.index(f"{line}.{column}")


class PatchOpSelection:
    """只安装到修改包源码框；标题位置由共用文本框显式提供。"""

    def __init__(self, widget, title_row, commits):
        self.widget = widget
        self.commits = commits
        self.button = title_bar_button(
            title_row, "选中OP", self.select, width=7,
        )
        self.button.pack(side="right", padx=(0, 6))
        from ui.widgets import add_tooltip
        add_tooltip(
            self.button,
            "无选区时选中源码光标所在的完整 OP；"
            "有选区时扩展为该范围涉及的完整 OP。"
            "也可先用查找定位，再点击本按钮。"
            "只改变选区，不删除正文或执行修改包。",
        )
        # 点击标题栏或查找框后仍保留源码选区和插入位置。
        widget.configure(exportselection=False)
        widget._patch_op_selection = self

    def select(self, event=None):
        widget = self.widget
        if (
            self.commits.operation_depth
            or str(widget.cget("state")) != "normal"
        ):
            return "break"
        source = self.commits.read_text(
            widget, preserve_trailing_newlines=True,
        )
        selection = widget.tag_ranges("sel")
        if selection:
            start = len(widget.get("1.0", selection[0]))
            end = len(widget.get("1.0", selection[1]))
        else:
            start = end = len(widget.get("1.0", "insert"))
        start = min(start, len(source))
        end = min(end, len(source))
        try:
            left, right = locate_op_selection(
                source, start=start, end=end,
            )
        except ValueError as error:
            with self.commits.operation():
                safe_show_error(
                    "OP 未选中", str(error), widget.winfo_toplevel(),
                )
            return "break"

        first = _source_index(widget, source, left)
        last = _source_index(widget, source, right)
        widget._text_editor_controller.invalidate_current_match()
        widget.tag_remove("sel", "1.0", "end")
        widget.tag_add("sel", first, last)
        widget.tag_raise("sel")
        widget.mark_set("insert", first)
        widget.see(first)
        restore_editor_interaction(widget)
        return "break"
