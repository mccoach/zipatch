# -*- coding: utf-8 -*-

"""Windows 名单解释与匹配；具体业务决定排除或输出行为。"""

import re
from dataclasses import dataclass
from pathlib import PureWindowsPath


_SEPARATORS = "\\/"
_INVALID_CHARACTER = re.compile(r'[<>:|\x00-\x1f]')
_DEVICE_NAME = re.compile(
    r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RuleDiagnostic:
    input_line: int
    display_line: int
    text: str
    state: str
    reason: str
    requires_confirmation: bool = False


@dataclass(frozen=True)
class CompiledRule:
    rooted: bool
    no_extension: bool
    segments: tuple
    object_kind: str

    def matches(self, path_parts, *, ignore_root=False):
        parts = tuple(path_parts)
        if not parts:
            return False
        if self.no_extension and PureWindowsPath(parts[-1]).suffix:
            return False
        if not self.segments:
            return True
        size = len(parts)
        file_object = self.object_kind == "file"
        reachable = (
            {0} if self.rooted and not ignore_root else set(range(size))
        )
        for segment_index, segment in enumerate(self.segments):
            if not reachable:
                return False
            if segment is None:
                if segment_index == len(self.segments) - 1:
                    return any(index < size for index in reachable) if file_object else True
                maximum = size - 1 if file_object else size
                reachable = set(range(min(reachable), maximum + 1))
            else:
                reachable = {
                    index + 1 for index in reachable
                    if index < size and segment.fullmatch(parts[index]) is not None
                }
        return size in reachable


@dataclass(frozen=True, init=False)
class RuleListResult:
    """完整结果只由正式分析过程形成，消费方不能拼装矛盾的合法结果。"""

    normalized_text: str
    rules: tuple
    diagnostics: tuple
    line_map: tuple
    object_kind: str
    source_text: str
    semantics: str
    has_errors: bool
    has_warnings: bool
    requires_confirmation: bool

    def __init__(self, *args, **kwargs):
        raise TypeError("名单结果必须通过 analyze_rule_list 创建")

    def matches(self, path_parts, *, ignore_root=False):
        """默认根语义不变；名单合并显式请求绝对路径尾部匹配。"""
        return any(
            rule.matches(path_parts, ignore_root=ignore_root)
            for rule in self.rules
        )


class RuleValidationError(ValueError):
    pass


def _compile_segment(segment):
    if segment == "**":
        return None
    expression = "".join(
        "[^\\\\]*" if character == "*"
        else "[^\\\\]" if character == "?"
        else re.escape(character)
        for character in segment
    )
    return re.compile(expression, re.IGNORECASE)


def _is_reserved_device_component(component):
    if "*" in component or "?" in component:
        return False
    stem = component.split(".", 1)[0].rstrip(" ")
    return _DEVICE_NAME.fullmatch(stem) is not None


def _analyze_enabled_line(line, object_kind):
    no_extension = line.startswith(":")
    body = line[1:] if no_extension else line
    errors = []
    if no_extension and object_kind == "directory":
        errors.append("目录名单不支持无扩展名条件")
    if body == "":
        if no_extension and object_kind == "file":
            return line, (), "", CompiledRule(False, True, (), object_kind)
        return line, ("模式正文为空",), "", None

    rooted = body[0] in _SEPARATORS
    if len(body) > 1 and rooted and body[1] in _SEPARATORS:
        errors.append("重复开头分隔符／UNC 路径不允许")
    pattern = body[1:] if rooted else body
    pattern = pattern.rstrip(_SEPARATORS)
    if pattern == "":
        errors.append("根限定符或尾分隔符之后没有模式正文")
    if _INVALID_CHARACTER.search(pattern):
        errors.append("包含非法字符：尖括号、正文冒号、竖线或控制字符")

    segments = re.split(r"[\\/]", pattern) if pattern else []
    if any(segment == "" for segment in segments):
        errors.append("内部连续分隔符形成空路径段")
    for index, segment in enumerate(segments):
        if not segment:
            continue
        if segment in (".", ".."):
            errors.append("不允许独立 . 或 .. 路径段")
        if "**" in segment and segment != "**":
            errors.append("双星号必须独占路径段")
        if all(character == " " for character in segment):
            errors.append("名称正文不能只包含半角空格")
        if segment.rstrip(" ").endswith("."):
            errors.append("名称组件不能以句点结尾")
        if index < len(segments) - 1 and segment.endswith(" "):
            errors.append("中间路径段不能以空格结尾")
        if _is_reserved_device_component(segment):
            errors.append(f"Windows 保留设备名：{segment}")

    if errors:
        return line, tuple(dict.fromkeys(errors)), "", None
    normalized = (
        (":" if no_extension else "")
        + ("\\" if rooted else "") + "\\".join(segments)
    )
    leading_spaces = len(pattern) - len(pattern.lstrip(" "))
    trailing_spaces = len(pattern) - len(pattern.rstrip(" "))
    warning = ""
    if leading_spaces or trailing_spaces:
        warning = (
            f"模式正文首部 {leading_spaces} 个、尾部 {trailing_spaces} 个半角空格；"
            "空格参与匹配，可能导致未命中"
        )
    return normalized, (), warning, CompiledRule(
        rooted, no_extension,
        tuple(_compile_segment(segment) for segment in segments), object_kind,
    )


def clean_rule_quotes(text):
    """名单只清除半角双引号，其他字符和物理行保持不变。"""
    if not isinstance(text, str):
        raise TypeError("名单必须是字符串")
    return text.replace('"', "")


_ASCII_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz",
)


def is_exclusion_layout_line(line):
    """仅空行及纯 U+0020 行属于排除排版。"""
    return line.strip(" ") == ""


def has_exclusion_content(text):
    """收藏最低内容判断，不标准化、不编译、不回写控件。"""
    cleaned = clean_rule_quotes(text)
    return any(
        not is_exclusion_layout_line(line)
        for line in cleaned.replace("\r\n", "\n").split("\n")
    )


def analyze_rule_list(text, object_kind, *, semantics="standard"):
    """同次分析形成表达、编译规则和诊断，结果明确持有分析语义。"""
    if object_kind not in ("directory", "file"):
        raise ValueError("名单对象类型必须为 directory 或 file")
    if semantics not in ("standard", "exclusion"):
        raise ValueError("名单分析语义必须为 standard 或 exclusion")
    if not isinstance(text, str):
        raise TypeError("名单必须是字符串")

    normalized_lines, rules, diagnostics, line_map = [], [], [], []
    source_text = clean_rule_quotes(text)
    first_enabled_lines = {}

    # 孤立 CR 不作为分行形式；排版判断不扩大到其他 Unicode 空白。
    for input_line, line in enumerate(
        source_text.replace("\r\n", "\n").split("\n"), 1,
    ):
        if semantics == "exclusion" and is_exclusion_layout_line(line):
            normalized_lines.append(line)
            line_map.append(input_line)
            continue
        if line == "":
            continue

        display_line = len(normalized_lines) + 1
        line_map.append(input_line)
        if line.startswith("|"):
            normalized_lines.append(line)
            diagnostics.append(
                RuleDiagnostic(
                    input_line, display_line, line, "disabled", "行首竖线停用",
                )
            )
            continue

        normalized, errors, warning, rule = _analyze_enabled_line(
            line, object_kind,
        )
        normalized_lines.append(normalized)
        if errors:
            diagnostics.append(
                RuleDiagnostic(
                    input_line, display_line, normalized,
                    "error", "；".join(errors),
                )
            )
            continue

        # 每条合法启用表达仍编译，不增加编译去重或第二份执行正文。
        rules.append(rule)
        reasons = [warning] if warning else []
        if semantics == "exclusion":
            # 正式合法标准化表达保留根限定、无扩展名条件、路径段、
            # 通配符及实际空格结构；只折叠 ASCII 字母。
            duplicate_key = normalized.translate(_ASCII_LOWER)
            first_line = first_enabled_lines.setdefault(
                duplicate_key, input_line,
            )
            if first_line != input_line:
                reasons.append(
                    f"与第 {first_line} 行结构等价重复，不增加排除范围"
                )
        if reasons:
            diagnostics.append(
                RuleDiagnostic(
                    input_line, display_line, normalized,
                    "warning", "；".join(reasons),
                    requires_confirmation=bool(warning),
                )
            )

    result = object.__new__(RuleListResult)
    values = {
        "normalized_text": "\n".join(normalized_lines),
        "rules": tuple(rules),
        "diagnostics": tuple(diagnostics),
        "line_map": tuple(line_map),
        "object_kind": object_kind,
        "source_text": source_text,
        "semantics": semantics,
        "has_errors": any(value.state == "error" for value in diagnostics),
        "has_warnings": any(value.state == "warning" for value in diagnostics),
        "requires_confirmation": any(
            value.requires_confirmation for value in diagnostics
        ),
    }
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def format_rule_diagnostics(named_results, use_input_lines=False):
    lines = []
    for name, result in named_results:
        for diagnostic in result.diagnostics:
            if diagnostic.state == "disabled":
                continue
            number = diagnostic.input_line if use_input_lines else diagnostic.display_line
            state = (
                "无效" if diagnostic.state == "error"
                else "有效但有风险" if diagnostic.requires_confirmation
                else "有效提示"
            )
            lines.extend((
                f"{name}，第 {number} 行：{state}",
                diagnostic.reason,
                f"条目原文的转义展示：{diagnostic.text!r}",
                "",
            ))
    return "\n".join(lines).rstrip("\n")


def validate_file_rules(rules):
    """校验已准备文件名单；不读取配置、扫描或重新编译。"""
    if (
        not isinstance(rules, RuleListResult)
        or rules.object_kind != "file"
        or rules.semantics != "standard"
    ):
        raise TypeError("必须显式传入标准语义的已准备文件名单规则")
    if rules.has_errors:
        raise RuleValidationError(
            format_rule_diagnostics((("文件名单", rules),), use_input_lines=True)
        )