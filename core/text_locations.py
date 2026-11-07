# -*- coding: utf-8 -*-

"""LF 匹配视图、稀疏原文边界映射及非重叠定位。"""

import re
from bisect import bisect_left
from dataclasses import dataclass

from core.text_newlines import normalize_newlines


def iter_physical_lines(text):
    """识别CRLF、LF及单独CR，携带原字符边界，不重拼权威正文。"""
    if not isinstance(text, str):
        raise TypeError("待分行正文必须是字符串")
    start = 0
    number = 1
    for separator in re.finditer(r"\r\n|\r|\n", text):
        yield number, text[start:separator.start()], start, separator.end()
        start = separator.end()
        number += 1
    yield number, text[start:], start, len(text)


@dataclass(frozen=True)
class TextSnapshot:
    source: str
    view: str
    collapsed_positions: tuple

    @classmethod
    def build(cls, source):
        fragments, collapsed = [], []
        start, removed = 0, 0
        for match in re.finditer(r"\r\n|\r", source):
            fragments.extend((source[start:match.start()], "\n"))
            if match.group() == "\r\n":
                collapsed.append(match.start() - removed)
                removed += 1
            start = match.end()
        fragments.append(source[start:])
        return cls(source, "".join(fragments), tuple(collapsed))

    def original_boundary(self, position):
        if not 0 <= position <= len(self.view):
            raise ValueError("匹配视图边界越界")
        # 经过完整 CRLF 换行单位后，才增加对应原文偏移。
        return position + bisect_left(self.collapsed_positions, position)


@dataclass(frozen=True)
class TextLocation:
    snapshot: TextSnapshot
    view_ranges: tuple
    original_ranges: tuple

    @property
    def hit_count(self):
        return len(self.view_ranges)

    @classmethod
    def from_ranges(cls, snapshot, ranges):
        ranges = tuple(ranges)
        return cls(
            snapshot, ranges,
            tuple(
                (snapshot.original_boundary(start), snapshot.original_boundary(end))
                for start, end in ranges
            ),
        )

    def compose(self, replacement):
        fragments = []
        previous = 0
        for start, end in self.original_ranges:
            fragments.extend((self.snapshot.source[previous:start], replacement))
            previous = end
        fragments.append(self.snapshot.source[previous:])
        return "".join(fragments)


def _nonoverlapping_ranges(text, pattern, start=0):
    """在明确的匹配视图范围内定位，返回完整视图的绝对偏移。"""
    ranges = []
    while True:
        position = text.find(pattern, start)
        if position < 0:
            return tuple(ranges)
        end = position + len(pattern)
        ranges.append((position, end))
        start = end


def locate_exact(snapshot, old, expected_count):
    pattern = normalize_newlines(old, "\n")
    if not pattern:
        raise ValueError("replace_exact 的 OLD 文本不能为空")
    ranges = _nonoverlapping_ranges(snapshot.view, pattern)
    if len(ranges) != expected_count:
        raise ValueError(
            "replace_exact 命中次数不符："
            f"expected={expected_count}, actual={len(ranges)}"
        )
    return TextLocation.from_ranges(snapshot, ranges)


def locate_between(snapshot, start_marker, end_marker):
    """起点全文唯一；终点只在起点完整结束后的有效范围内定位。"""
    start_pattern = normalize_newlines(start_marker, "\n")
    end_pattern = normalize_newlines(end_marker, "\n")
    if not start_pattern or not end_pattern:
        raise ValueError("replace_between 两个锚点不能为空")

    starts = _nonoverlapping_ranges(snapshot.view, start_pattern)
    if len(starts) != 1:
        raise ValueError(
            "replace_between 起始锚点全文未命中或不唯一："
            f"start_count={len(starts)}；必须恰好命中一处。"
        )

    start, after_start = starts[0]
    # 偏移始终属于匹配视图；最后由 TextLocation 统一映射回原文。
    # 不先做全文终点检查，也不从失败中回退选择某个候选。
    ends = _nonoverlapping_ranges(snapshot.view, end_pattern, after_start)
    if len(ends) != 1:
        raise ValueError(
            "replace_between 后置终止锚点未命中或不唯一："
            f"end_count={len(ends)}；"
            "搜索范围从起始锚点完整结束位置开始，必须恰好命中一处。"
            "前置及与起始锚点重叠的终点不参与候选，"
            "不会自动选择第一处、最近或最远终点。"
        )
    return TextLocation.from_ranges(snapshot, ((start, ends[0][1]),))