# -*- coding: utf-8 -*-

"""合并容器的完整行结构、正文分隔和输出参数契约。"""

import re
from datetime import datetime

from core.constants import (
    CODE_FOOTER_LINE, CODE_HEADER_LINE, MESSAGE_FILE_EXCLUDED,
    MESSAGE_FILE_FAILED,
)
from core.text_newlines import normalize_newlines


STRUCTURAL_NEWLINE = "\r\n"
MTIME_PREFIX = "最后修改时间："
MTIME_UNAVAILABLE = "未取得"
MTIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_SECTION_LINE = re.compile(r"---第[0-9]+个文件---")
_MTIME_VALUE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"
)


class OutputSettingsError(ValueError):
    """用户输出参数不符合正式契约，不是磁盘保存失败或程序缺陷。"""


from core import text_locations


def validate_markers(start_marker, end_marker):
    for name, marker in (("开始标记", start_marker), ("结束标记", end_marker)):
        if not isinstance(marker, str) or marker == "":
            raise OutputSettingsError(f"{name}必须是非空字符串")
        if "\r" in marker or "\n" in marker:
            raise OutputSettingsError(f"{name}必须为单行，不能包含 CR 或 LF")
    if start_marker == end_marker:
        raise OutputSettingsError("开始标记与结束标记不能相同")
    if start_marker.startswith(MTIME_PREFIX):
        raise OutputSettingsError("开始标记不能使用“最后修改时间：”结构前缀")


def validate_placeholders(ignore_text, failure_text):
    for name, text in (
        ("主动忽略标识", ignore_text),
        ("被动失败标识", failure_text),
    ):
        if not isinstance(text, str) or text == "":
            raise OutputSettingsError(f"{name}必须是非空字符串；空文件不是占位")
    if normalize_newlines(ignore_text, "\n") == normalize_newlines(failure_text, "\n"):
        raise OutputSettingsError("主动忽略与被动失败标识不能相同或仅换行形式不同")


def validate_output_settings(section_name, settings):
    """显式错误不能被默认合并静默替代。"""
    if not isinstance(settings, dict):
        raise OutputSettingsError(f"配置 {section_name} 必须是对象；原文件未修改。")
    try:
        validate_markers(
            settings.get("code_header_line", CODE_HEADER_LINE),
            settings.get("code_footer_line", CODE_FOOTER_LINE),
        )
        validate_placeholders(
            settings.get("ignore_text", MESSAGE_FILE_EXCLUDED),
            settings.get("failure_text", MESSAGE_FILE_FAILED),
        )
        switches = ["normalize_text_newlines"]
        if section_name == "merge":
            switches.append("record_file_mtime")
        for field in switches:
            if field in settings and type(settings[field]) is not bool:
                raise OutputSettingsError(f"{field} 必须是布尔值")
    except OutputSettingsError as error:
        raise OutputSettingsError(
            f"配置 {section_name} 无效：{error}；原配置文件未因此修改。"
        ) from error


def validate_body_end_marker(body, end_marker, file_path):
    """检查封装后完整结束行，包括原文无尾换行末行。"""
    for number, line, _, _ in text_locations.iter_physical_lines(body + STRUCTURAL_NEWLINE):
        if line == end_marker:
            raise ValueError(
                f"合并正文与结束标记冲突：{file_path}\n"
                f"正文第 {number} 行形成完整结束标记行：{end_marker!r}。\n"
                "本次成果未发布。请换用更独特的结束标记；"
                "不会删除、转义或修改正文绕过冲突。"
            )


def classify_placeholder(body, ignore_text, failure_text):
    """仅生成比较视图，不改变提取出的权威正文。"""
    comparable = normalize_newlines(body, "\n")
    if comparable == normalize_newlines(ignore_text, "\n"):
        return "ignored"
    if comparable == normalize_newlines(failure_text, "\n"):
        return "failed"
    return None


def validate_mtime_line(line, number, path):
    value = line[len(MTIME_PREFIX):]
    if value == MTIME_UNAVAILABLE:
        return
    if _MTIME_VALUE.fullmatch(value) is not None:
        try:
            parsed = datetime.strptime(value, MTIME_FORMAT)
        except ValueError:
            pass
        else:
            if parsed.strftime(MTIME_FORMAT) == value:
                return
    raise ValueError(f"合并包第 {number} 行最后修改时间结构无效；文件：{path}")


def parse_file_blocks(full_text, start_marker, end_marker):
    """唯一完整行状态机；提取先于标准化，不裁剪路径或正文。"""
    validate_markers(start_marker, end_marker)
    blocks = []
    state = "SEEK_SECTION"
    section_number = None
    path = None
    body_start = None

    for number, line, line_start, next_start in text_locations.iter_physical_lines(full_text):
        if state == "SEEK_SECTION":
            if _SECTION_LINE.fullmatch(line) is not None:
                section_number = number
                state = "EXPECT_PATH"
            continue

        if state == "EXPECT_PATH":
            if line == "" or any(ord(character) < 32 for character in line):
                raise ValueError(
                    f"合并包第 {number} 行缺少有效的完整路径行；"
                    f"文件节头位于第 {section_number} 行。"
                )
            path = line
            state = "EXPECT_TIME_OR_START"
            continue

        if state == "EXPECT_TIME_OR_START" and line.startswith(MTIME_PREFIX):
            validate_mtime_line(line, number, path)
            state = "EXPECT_START"
            continue

        if state in ("EXPECT_TIME_OR_START", "EXPECT_START"):
            if line != start_marker:
                raise ValueError(
                    f"合并包第 {number} 行不是精确开始标记；文件：{path}。\n"
                    f"期望：{start_marker!r}；实际：{line!r}。"
                )
            body_start = next_start
            state = "IN_BODY"
            continue

        if state == "IN_BODY" and line == end_marker:
            enclosed = full_text[body_start:line_start]
            if enclosed.endswith("\r\n"):
                body = enclosed[:-2]
            elif enclosed.endswith(("\r", "\n")):
                body = enclosed[:-1]
            else:
                raise ValueError(
                    f"合并包第 {number} 行的结束标记之前缺少正文范围内"
                    f"的实际协议分行符；文件：{path}。"
                )
            blocks.append((path, body))
            state = "SEEK_SECTION"
            section_number = None
            path = None
            body_start = None

    if state != "SEEK_SECTION":
        raise ValueError(
            f"合并文件块未完整结束：文件节头位于第 {section_number} 行，"
            f"文件：{path!r}，缺失结构阶段：{state}。\n"
            "不会猜测块边界或通过旧正则继续解析。"
        )
    return blocks
