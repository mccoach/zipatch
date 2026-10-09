# -*- coding: utf-8 -*-

"""Windows 排除名单的唯一解析、标准化、校验与匹配能力。"""

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


@dataclass(frozen=True)
class CompiledRule:
    rooted: bool
    no_extension: bool
    segments: tuple
    object_kind: str

    def matches(self, path_parts):
        parts = tuple(path_parts)
        if not parts:
            return False
        if self.no_extension and PureWindowsPath(parts[-1]).suffix:
            return False
        if not self.segments:
            return True

        size = len(parts)
        file_object = self.object_kind == "file"
        reachable = {0} if self.rooted else set(range(size))

        for segment_index, segment in enumerate(self.segments):
            if not reachable:
                return False
            if segment is None:
                if segment_index == len(self.segments) - 1:
                    # 文件名单的末尾 ** 必须仍消费一个文件对象；
                    # 目录名单允许 tools\** 在 tools 本身处匹配零层。
                    return any(index < size for index in reachable) if file_object else True
                maximum = size - 1 if file_object else size
                first = min(reachable)
                reachable = set(range(first, maximum + 1))
            else:
                reachable = {
                    index + 1
                    for index in reachable
                    if index < size and segment.fullmatch(parts[index]) is not None
                }

        return size in reachable


@dataclass(frozen=True)
class ExclusionListResult:
    normalized_text: str
    rules: tuple
    diagnostics: tuple
    line_map: tuple

    @property
    def has_errors(self):
        return any(item.state == "error" for item in self.diagnostics)

    @property
    def has_warnings(self):
        return any(item.state == "warning" for item in self.diagnostics)

    def matches(self, path_parts):
        return any(rule.matches(path_parts) for rule in self.rules)


class ExclusionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedExclusions:
    directories: ExclusionListResult
    files: ExclusionListResult

    def __post_init__(self):
        if self.directories.has_errors or self.files.has_errors:
            raise ExclusionValidationError(
                format_exclusion_diagnostics(
                    (
                        ("目录名单", self.directories),
                        ("文件名单", self.files),
                    ),
                    use_input_lines=True,
                )
            )


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
    # Win32 设备名包括带扩展名形式及设备名和扩展名前的空格。
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
        if segment and all(character == " " for character in segment):
            errors.append("名称正文不能只包含半角空格")
        if segment.rstrip(" ").endswith("."):
            errors.append("名称组件不能以句点结尾")
        if index < len(segments) - 1 and segment.endswith(" "):
            errors.append("中间路径段不能以空格结尾")
        if _is_reserved_device_component(segment):
            errors.append(f"Windows 保留设备名：{segment}")

    if errors:
        # 非法路径不通过转换或清理伪装合法；引号删除由外层统一完成。
        return line, tuple(dict.fromkeys(errors)), "", None

    prefix = ":" if no_extension else ""
    normalized = prefix + ("\\" if rooted else "") + "\\".join(segments)
    leading_spaces = len(pattern) - len(pattern.lstrip(" "))
    trailing_spaces = len(pattern) - len(pattern.rstrip(" "))
    warning = ""
    if leading_spaces or trailing_spaces:
        warning = (
            f"模式正文首部 {leading_spaces} 个、尾部 {trailing_spaces} 个半角空格；"
            "空格参与匹配，可能导致未命中"
        )

    rule = CompiledRule(
        rooted, no_extension,
        tuple(_compile_segment(segment) for segment in segments),
        object_kind,
    )
    return normalized, (), warning, rule


def analyze_exclusion_list(text, object_kind):
    if object_kind not in ("directory", "file"):
        raise ValueError("排除名单对象类型必须为 directory 或 file")
    if not isinstance(text, str):
        raise TypeError("排除名单必须是字符串")

    normalized_lines = []
    rules = []
    diagnostics = []
    line_map = []

    # 只识别 LF 和 CRLF；孤立 CR、Tab 等保留并参与非法字符校验。
    for input_line, raw_line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = raw_line.replace('"', "")
        if line == "":
            continue

        display_line = len(normalized_lines) + 1
        line_map.append(input_line)
        if line.startswith("|"):
            normalized_lines.append(line)
            diagnostics.append(
                RuleDiagnostic(input_line, display_line, line, "disabled", "行首竖线停用")
            )
            continue

        normalized, errors, warning, rule = _analyze_enabled_line(line, object_kind)
        normalized_lines.append(normalized)
        if errors:
            diagnostics.append(
                RuleDiagnostic(
                    input_line, display_line, normalized, "error", "；".join(errors),
                )
            )
        else:
            rules.append(rule)
            if warning:
                diagnostics.append(
                    RuleDiagnostic(input_line, display_line, normalized, "warning", warning)
                )

    return ExclusionListResult(
        "\n".join(normalized_lines),
        tuple(rules), tuple(diagnostics), tuple(line_map),
    )


def prepare_exclusions(directory_text, file_text):
    return PreparedExclusions(
        analyze_exclusion_list(directory_text, "directory"),
        analyze_exclusion_list(file_text, "file"),
    )


def format_exclusion_diagnostics(named_results, use_input_lines=False):
    lines = []
    for name, result in named_results:
        for diagnostic in result.diagnostics:
            if diagnostic.state == "disabled":
                continue
            number = diagnostic.input_line if use_input_lines else diagnostic.display_line
            state = "无效" if diagnostic.state == "error" else "有效但有风险"
            lines.extend((
                f"{name}，第 {number} 行：{state}",
                diagnostic.reason,
                # repr 明确显示首尾空格、控制字符及反斜杠，而非要求用户输入转义。
                f"条目原文的转义展示：{diagnostic.text!r}",
                "",
            ))
    return "\n".join(lines).rstrip("\n")