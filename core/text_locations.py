# -*- coding: utf-8 -*-

"""LF 匹配视图、稀疏原文边界映射及非重叠定位。"""

import re
from bisect import bisect_left
from dataclasses import dataclass

from core.text_newlines import normalize_newlines


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


def _nonoverlapping_ranges(text, pattern):
    ranges = []
    start = 0
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
    start_pattern = normalize_newlines(start_marker, "\n")
    end_pattern = normalize_newlines(end_marker, "\n")
    if not start_pattern or not end_pattern:
        raise ValueError("replace_between 两个锚点不能为空")
    starts = _nonoverlapping_ranges(snapshot.view, start_pattern)
    ends = _nonoverlapping_ranges(snapshot.view, end_pattern)
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError(
            "replace_between 锚点不唯一："
            f"start_count={len(starts)}, end_count={len(ends)}"
        )
    if starts[0][0] >= ends[0][0]:
        raise ValueError("replace_between 起始锚点在结束锚点之后")
    return TextLocation.from_ranges(snapshot, ((starts[0][0], ends[0][1]),))