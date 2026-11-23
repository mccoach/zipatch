# -*- coding: utf-8 -*-

"""合并容器的完整行结构、正文分隔和输出参数契约。"""

import re
from datetime import datetime

from core.constants import CODE_FOOTER_LINE, CODE_HEADER_LINE, MERGE_MODES


STRUCTURAL_NEWLINE = "\r\n"
MTIME_PREFIX = "最后修改时间："
MTIME_UNAVAILABLE = "未取得"
MTIME_FORMAT = "%Y-%m-%d %H:%M:%S"

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
    if start_marker.startswith(STRUCTURE_PREFIX):
        raise OutputSettingsError("开始标记不能使用ZIPATCH_ENTRY:结构前缀")



def validate_output_settings(section_name, settings):
    """显式错误不能被默认合并静默替代。"""
    if not isinstance(settings, dict):
        raise OutputSettingsError(f"配置 {section_name} 必须是对象；原文件未修改。")
    try:
        if section_name == "merge":
            validate_markers(
                settings.get("code_header_line", CODE_HEADER_LINE),
                settings.get("code_footer_line", CODE_FOOTER_LINE),
            )

        switches = ["normalize_text_newlines"]
        if section_name == "merge":
            switches.append("record_file_mtime")
            mode = settings.get("merge_mode", MERGE_MODES[0])
            if type(mode) is not str or mode not in MERGE_MODES:
                raise OutputSettingsError(
                    "merge_mode 必须是 directory、list 或 combined"
                )
        for field in switches:
            if field in settings and type(settings[field]) is not bool:
                raise OutputSettingsError(f"{field} 必须是布尔值")
    except OutputSettingsError as error:
        raise OutputSettingsError(
            f"配置 {section_name} 无效：{error}；原配置文件未因此修改。"
        ) from error


CONTAINER_PREFIX = "ZIPATCH_MERGED:"
CONTAINER_END_PREFIX = "ZIPATCH_MERGED_END:"
CONTAINER_VERSION = 2
_CONTAINER_TOKEN = re.compile(r"[0-9A-Za-z]{2}")
_LEGACY_CONTAINER_TOKEN = re.compile(r"[0-9a-f]{32}")


def _container_token_version(token):
    if type(token) is str:
        if _CONTAINER_TOKEN.fullmatch(token) is not None:
            return CONTAINER_VERSION
        if _LEGACY_CONTAINER_TOKEN.fullmatch(token) is not None:
            return 1
    raise ValueError("容器标识必须是两位字母数字；旧版仅接受32位小写十六进制")


def container_marker_lines(token, start_label, end_label):
    """新格式直接追加两位标识；旧格式仅保留读取能力。"""
    version = _container_token_version(token)
    validate_markers(start_label, end_label)
    suffix = token if version == CONTAINER_VERSION else " [" + token + "]"
    return (
        start_label + suffix,
        end_label + suffix,
        CONTAINER_END_PREFIX + token,
    )


def encode_container_declaration(token, start_label, end_label):
    import json

    if _container_token_version(token) != CONTAINER_VERSION:
        raise ValueError("新成果只生成两位标识的version 2容器")
    container_marker_lines(token, start_label, end_label)
    return CONTAINER_PREFIX + json.dumps(
        {
            "version": CONTAINER_VERSION,
            "token": token,
            "start_label": start_label,
            "end_label": end_label,
        },
        ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    )


def decode_container_declaration(line):
    import json

    if not line.startswith(CONTAINER_PREFIX):
        raise ValueError(
            "拆分来源缺少第一行ZIPATCH_MERGED声明；"
            "只接受新声明式容器，不搜索正文中的声明或猜测旧标记"
        )

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"合并容器声明包含重复字段：{key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"合并容器声明包含非JSON数值：{value}")

    try:
        value = json.loads(
            line[len(CONTAINER_PREFIX):],
            object_pairs_hook=unique_fields,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"合并容器声明无法解析：{error}") from error
    if (
        type(value) is not dict
        or set(value) != {"version", "token", "start_label", "end_label"}
        or type(value["version"]) is not int
        or value["version"] not in (1, CONTAINER_VERSION)
    ):
        raise ValueError("合并容器声明版本或字段集合非法")
    if _container_token_version(value["token"]) != value["version"]:
        raise ValueError("合并容器声明版本与边界标识不匹配")
    container_marker_lines(
        value["token"], value["start_label"], value["end_label"],
    )
    return value["token"], value["start_label"], value["end_label"]



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


STRUCTURE_PREFIX = "ZIPATCH_ENTRY:"
LINK_ENTRY_KINDS = frozenset((
    "file_symlink", "directory_symlink", "junction",
))


from dataclasses import dataclass


@dataclass(frozen=True)
class MergeBlock:
    """正文和结构定义分别拥有语义；路径仍是容器记录的名义位置。"""

    path: str
    body: str
    kind: str = "file"
    specification: object = None


def encode_structure_line(kind, specification=None, *, entry_path):
    import json
    from core.link_paths import validate_link_specification

    if kind == "directory":
        if specification is not None:
            raise ValueError("普通目录不得携带链接定义")
        value = {"kind": kind}
    elif kind in LINK_ENTRY_KINDS:
        validate_link_specification(specification, entry_path)
        if specification.kind != kind:
            raise ValueError("结构类型与链接定义类型不匹配")
        value = {
            "kind": kind,
            "raw_target": specification.raw_target,
            "relative": specification.relative,
        }
    else:
        raise ValueError("结构行只用于普通目录或三类链接入口")
    return STRUCTURE_PREFIX + json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    )


def decode_structure_line(line, *, entry_path):
    import json
    from core.link_paths import LinkSpecification, validate_link_specification

    def unique_fields(pairs):
        value = {}
        for key, member in pairs:
            if key in value:
                raise ValueError(f"结构行包含重复字段：{key}")
            value[key] = member
        return value

    def reject_constant(value):
        raise ValueError(f"结构行包含非JSON数值：{value}")

    try:
        value = json.loads(
            line[len(STRUCTURE_PREFIX):],
            object_pairs_hook=unique_fields,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"结构行JSON无法解析：{error}") from error
    if type(value) is not dict or type(value.get("kind")) is not str:
        raise ValueError("结构行缺少准确类型字段")
    kind = value["kind"]
    if kind == "directory":
        if set(value) != {"kind"}:
            raise ValueError("普通目录结构行包含未知字段")
        return kind, None
    if kind not in LINK_ENTRY_KINDS or set(value) != {
        "kind", "raw_target", "relative",
    }:
        raise ValueError("链接结构行类型或字段集合非法")
    specification = LinkSpecification(
        kind, value["raw_target"], value["relative"],
    )
    validate_link_specification(specification, entry_path)
    return kind, specification


def parse_merge_blocks(full_text):
    """第一行声明固定本层；正文不递归解析，完整包尾是必要条件。"""
    lines = iter(text_locations.iter_physical_lines(full_text))
    _, declaration, _, _ = next(lines)
    # 导入解码通常已经消费UTF-8 BOM；粘贴的文件头允许一个BOM。
    if declaration.startswith("\ufeff"):
        declaration = declaration[1:]
    token, start_label, end_label = decode_container_declaration(declaration)
    start_marker, end_marker, container_end = container_marker_lines(
        token, start_label, end_label,
    )
    section_suffix = (
        re.escape(token)
        if _container_token_version(token) == CONTAINER_VERSION else
        r" \[" + re.escape(token) + r"\]"
    )
    section = re.compile(r"---第([0-9]+)个文件---" + section_suffix)
    blocks = []
    state = "SEEK_SECTION"
    section_number = None
    path = None
    body_start = None
    kind = "file"
    specification = None

    for number, line, line_start, next_start in lines:
        if state == "END":
            if line != "":
                raise ValueError(f"合并容器第 {number} 行：包尾之后仍有非空内容")
            continue

        if state == "SEEK_SECTION":
            if line == container_end:
                state = "END"
                continue
            matched = section.fullmatch(line)
            if matched is not None:
                if int(matched.group(1)) != len(blocks) + 1:
                    raise ValueError(f"合并容器第 {number} 行：文件编号不连续")
                section_number = number
                state = "EXPECT_PATH"
            elif token in line:
                raise ValueError(f"合并容器第 {number} 行：本层结构标识出现在非法位置")
            continue

        if state == "EXPECT_PATH":
            if line == "" or any(ord(character) < 32 for character in line):
                raise ValueError(
                    f"合并容器第 {number} 行缺少有效完整路径；"
                    f"文件节头位于第 {section_number} 行"
                )
            if token in line:
                raise ValueError("合并容器路径与本层标识冲突")
            path = line
            state = "EXPECT_TIME_OR_START"
            continue

        if state == "EXPECT_TIME_OR_START" and line.startswith(STRUCTURE_PREFIX):
            kind, specification = decode_structure_line(line, entry_path=path)
            state = "EXPECT_START"
            continue

        if state == "EXPECT_TIME_OR_START" and line.startswith(MTIME_PREFIX):
            validate_mtime_line(line, number, path)
            state = "EXPECT_START"
            continue

        if state in ("EXPECT_TIME_OR_START", "EXPECT_START"):
            if line != start_marker:
                raise ValueError(
                    f"合并容器第 {number} 行不是声明的完整开始行；文件：{path}"
                )
            body_start = next_start
            state = "IN_BODY"
            continue

        if state == "IN_BODY":
            if line != end_marker:
                continue
            enclosed = full_text[body_start:line_start]
            if enclosed.endswith("\r\n"):
                body = enclosed[:-2]
            elif enclosed.endswith(("\r", "\n")):
                body = enclosed[:-1]
            else:
                raise ValueError(
                    f"合并容器第 {number} 行结束标记前缺少独立协议分行符；"
                    f"文件：{path}"
                )
            if kind != "file" and body != "":
                raise ValueError("普通目录及链接结构块不得携带文件正文或占位")
            blocks.append(MergeBlock(path, body, kind, specification))
            kind = "file"
            specification = None
            section_number = None
            path = None
            body_start = None
            state = "SEEK_SECTION"

    if state != "END":
        raise ValueError(
            f"合并容器未完整结束：阶段={state}；"
            f"文件节头={section_number}；文件={path!r}。"
            "缺少本层完整包尾或文件块；不猜测边界，不读取旧格式"
        )
    return blocks


def parse_file_blocks(full_text):
    """普通正文消费者整份拒绝结构容器，不执行普通文件子集。"""
    blocks = parse_merge_blocks(full_text)
    if any(block.kind != "file" for block in blocks):
        raise ValueError(
            "本容器包含普通目录或链接结构记录；"
            "普通正文消费者不能只处理其中的文件子集"
        )
    return [(block.path, block.body) for block in blocks]
