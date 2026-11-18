# -*- coding: utf-8 -*-

"""文本查找的字面／有限通配符解释、严格边界及非重叠结果。

位置始终是Python Unicode码点半开区间。
不读取控件、不提交配置、不展开替换正文。
"""

import re
import unicodedata
from dataclasses import dataclass


WHITE_SPACE_CODEPOINTS = (
    *range(0x0009, 0x000E),
    0x0020, 0x0085, 0x00A0, 0x1680,
    *range(0x2000, 0x200B),
    0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
)
ASCII_BOUNDARY_CHARACTERS = (
    "`~!@#$%^&*()-=+[]{}\\|;:'\",.<>/?"
)
CJK_BOUNDARY_CHARACTERS = (
    "，。、；：！？"
    "（）［］｛｝"
    "【】〔〕〖〗"
    "《》〈〉"
    "「」『』"
    "“”‘’"
    "—–…·"
    "｀～＠＃＄％＾＆＊－＝＋"
    "＼｜＇＂．＜＞／"
)
# 不可变代码默认值；用户保存的名单不能反向修改它。
DEFAULT_BOUNDARY_CODEPOINTS = tuple(sorted({
    *WHITE_SPACE_CODEPOINTS,
    *(ord(character) for character in ASCII_BOUNDARY_CHARACTERS),
    *(ord(character) for character in CJK_BOUNDARY_CHARACTERS),
}))
WILDCARD_LINE_SEPARATORS = "\r\n\x85\u2028\u2029"


@dataclass(frozen=True)
class SearchResult:
    ranges: tuple = ()
    issue: str = ""


def validate_boundary_codepoints(values):
    """持久化契约：合法、唯一、升序的Unicode标量值数组。"""
    if type(values) is not list:
        raise ValueError("全词匹配边界必须是Unicode码点数组")
    for value in values:
        if (
            type(value) is not int
            or not 0 <= value <= 0x10FFFF
            or 0xD800 <= value <= 0xDFFF
        ):
            raise ValueError("全词匹配边界包含无效Unicode码点或代理项")
    if values != sorted(set(values)):
        raise ValueError("全词匹配边界码点必须去重并按升序保存")
    return tuple(values)


def validate_search_settings(settings):
    if type(settings) is not dict or set(settings) != {"boundary_codepoints"}:
        raise ValueError("search配置必须且只能包含boundary_codepoints")
    return validate_boundary_codepoints(settings["boundary_codepoints"])


def is_invisible_boundary(codepoint):
    character = chr(codepoint)
    return (
        codepoint in WHITE_SPACE_CODEPOINTS
        or unicodedata.category(character)[0] in ("C", "Z", "M")
    )


def _wildcard_expression(query):
    pieces = []
    index = 0
    only_stars = True
    wildcard_class = "[^" + re.escape(WILDCARD_LINE_SEPARATORS) + "]"
    while index < len(query):
        character = query[index]
        if (
            character == "\\"
            and index + 1 < len(query)
            and query[index + 1] in ("*", "?", "\\")
        ):
            pieces.append(re.escape(query[index + 1]))
            only_stars = False
            index += 2
            continue
        if character == "*":
            if not pieces or pieces[-1] != wildcard_class + "*":
                pieces.append(wildcard_class + "*")
        elif character == "?":
            pieces.append(wildcard_class)
            only_stars = False
        else:
            pieces.append(re.escape(character))
            only_stars = False
        index += 1
    if only_stars:
        return "", "仅由未转义星号组成的查询没有明确内容"
    return "".join(pieces), ""


def find_text_matches(
    source, query, *, case_sensitive=False, whole_word=False,
    wildcard=False, boundary_codepoints=DEFAULT_BOUNDARY_CODEPOINTS,
):
    """完整匹配一次；边界属于表达式约束，允许贪婪星号正确回退。"""
    if not isinstance(source, str) or not isinstance(query, str):
        raise TypeError("查找正文和查询必须是字符串")
    for switch in (case_sensitive, whole_word, wildcard):
        if type(switch) is not bool:
            raise TypeError("查找模式必须是布尔值")
    if not query:
        return SearchResult()
    if wildcard:
        expression, issue = _wildcard_expression(query)
        if issue:
            return SearchResult(issue=issue)
    else:
        expression = re.escape(query)

    if whole_word:
        boundaries = "".join(chr(value) for value in boundary_codepoints)
        if boundaries:
            character_class = re.escape(boundaries)
            left = "(?<![^" + character_class + "])"
            right = "(?=[" + character_class + "]|\\Z)"
        else:
            left = "(?<![\\s\\S])"
            right = "\\Z"
        # 查询的Unicode大小写策略不能扩大用户的边界码点白名单。
        # 两侧约束独立关闭IGNORECASE，正文查询仍按所选模式匹配。
        expression = "(?-i:" + left + ")(?:" + expression + ")(?-i:" + right + ")"

    compiled = re.compile(
        expression, 0 if case_sensitive else re.IGNORECASE,
    )
    ranges = tuple(
        (match.start(), match.end())
        for match in compiled.finditer(source)
        if match.end() > match.start()
    )
    return SearchResult(ranges)


def compose_search_replacement(source, ranges, replacement):
    """只消费本次非重叠位置；替换内容完全按字面插入。"""
    if not isinstance(source, str) or not isinstance(replacement, str):
        raise TypeError("替换正文必须是字符串")
    fragments = []
    previous = 0
    for start, end in ranges:
        if not previous <= start < end <= len(source):
            raise ValueError("查找替换位置重叠、越界或长度为零")
        fragments.extend((source[previous:start], replacement))
        previous = end
    fragments.append(source[previous:])
    return "".join(fragments)