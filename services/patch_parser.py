# -*- coding: utf-8 -*-

import re
import shlex

from core.constants import (
    PATCH_END,
    SUPPORTED_PATCH_OPS,
    PATCH_TEXT_BLOCK_STARTERS,
)
from core.text_io import split_lines_keep_text
from services.patch_ops import PATH_ONLY_OPS, parse_required_positive_int


def validate_boundary(boundary: str):
    if not isinstance(boundary, str) or not boundary:
        raise ValueError("boundary 不能为空")

    if len(boundary) < 32:
        raise ValueError("boundary 太短，至少需要 32 个字符")

    if len(boundary) > 160:
        raise ValueError("boundary 太长，建议不超过 160 个字符")

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", boundary):
        raise ValueError("boundary 只能包含字母、数字、下划线、短横线和点")

    forbidden = {
        PATCH_END,
        "---OP",
        "---CONTENT",
        "---OLD",
        "---NEW",
        "---END_OP",
    }

    if boundary in forbidden:
        raise ValueError(f"boundary 不能等于协议关键字：{boundary}")

    if not boundary.startswith("AI_PATCH_BOUNDARY_"):
        raise ValueError("boundary 必须以 AI_PATCH_BOUNDARY_ 开头")


def is_patch_start_candidate(line: str):
    return line.strip().startswith("<<AI_FILE_PATCH_V2")


def parse_start_line(line: str):
    pattern = r'^<<AI_FILE_PATCH_V2\s+boundary="([^"]+)">>$'
    match = re.match(pattern, line.strip())

    if not match:
        raise ValueError(
            "V2 修改包首行格式非法。应为："
            '<<AI_FILE_PATCH_V2 boundary="AI_PATCH_BOUNDARY_...">>'
        )

    boundary = match.group(1)
    validate_boundary(boundary)

    return boundary


def parse_op_line(line: str, line_no: int):
    try:
        parts = shlex.split(line, posix=True)
    except Exception as e:
        raise ValueError(f"第 {line_no} 行 OP 头解析失败：{e}")

    if len(parts) < 2 or parts[0] != "---OP":
        raise ValueError(f"第 {line_no} 行 OP 头格式非法：{line}")

    op_type = parts[1]

    if op_type not in SUPPORTED_PATCH_OPS:
        raise ValueError(f"第 {line_no} 行操作类型不支持：{op_type}")

    attrs = {
        "op": op_type,
        "_line_no": line_no,
    }

    for token in parts[2:]:
        if "=" not in token:
            raise ValueError(f"第 {line_no} 行 OP 参数非法，必须是 key=value：{token}")

        key, value = token.split("=", 1)
        key = key.strip()

        if not key:
            raise ValueError(f"第 {line_no} 行 OP 参数名不能为空：{token}")

        attrs[key] = value

    if "path" not in attrs:
        raise ValueError(f"第 {line_no} 行 OP 缺少 path 参数")

    return attrs


def join_text_block(lines):
    return "\n".join(lines)


def ensure_no_duplicate_block(op, key, line_no):
    if key in op:
        raise ValueError(
            f"第 {line_no} 行重复出现文本块 {key}，"
            f"当前操作开始于第 {op.get('_line_no', '?')} 行"
        )


def validate_completed_op(op, op_index):
    op_type = op["op"]

    if op_type in ("write_file", "append_text", "replace_between"):
        if "content" not in op:
            raise ValueError(f"第 {op_index} 个操作 {op_type} 缺少 ---CONTENT 文本块")

        if "old" in op or "new" in op:
            raise ValueError(f"第 {op_index} 个操作 {op_type} 不允许包含 ---OLD 或 ---NEW")

        if op_type == "replace_between" and "include_markers" in op:
            raise ValueError("replace_between 已固定包含 start_marker 和 end_marker，不允许 include_markers 参数")

    elif op_type == "replace_exact":
        if "old" not in op:
            raise ValueError(f"第 {op_index} 个 replace_exact 缺少 ---OLD 文本块")

        if "new" not in op:
            raise ValueError(f"第 {op_index} 个 replace_exact 缺少 ---NEW 文本块")

        if "count" not in op:
            raise ValueError(f"第 {op_index} 个 replace_exact 必须显式声明 count")

        parse_required_positive_int(op.get("count"), "count")

        if "content" in op:
            raise ValueError("replace_exact 不允许包含 ---CONTENT 文本块")

    elif op_type in PATH_ONLY_OPS:
        if "content" in op or "old" in op or "new" in op:
            raise ValueError(f"{op_type} 不允许包含正文文本块")


def parse_patch_v2(text: str):
    """
    V2 状态机解析器。
    """
    lines = split_lines_keep_text(text)

    state = "WAIT_START"
    boundary = None
    operations = []
    current_op = None
    current_block_key = None
    current_block_lines = []
    seen_start = False
    seen_end = False

    for idx, raw_line in enumerate(lines, 1):
        line = raw_line

        if state == "WAIT_START":
            if not line.strip():
                continue

            if is_patch_start_candidate(line):
                if seen_start:
                    raise ValueError(f"第 {idx} 行重复出现修改包开始标记")

                boundary = parse_start_line(line)
                seen_start = True
                state = "STRUCT"
                continue

            raise ValueError(
                f"第 {idx} 行不是 V2 修改包开始行。"
                f"首个非空行必须是 <<AI_FILE_PATCH_V2 boundary=\"...\">>"
            )

        if state == "STRUCT":
            stripped = line.strip()

            if not stripped:
                continue

            if is_patch_start_candidate(stripped):
                raise ValueError(f"第 {idx} 行在结构区重复出现修改包开始标记")

            if stripped == PATCH_END:
                if current_op is not None:
                    raise ValueError(
                        f"第 {idx} 行遇到包结束标记，但当前操作尚未 ---END_OP："
                        f"操作开始于第 {current_op.get('_line_no', '?')} 行"
                    )

                seen_end = True
                state = "END"
                continue

            if stripped.startswith("---OP "):
                if current_op is not None:
                    raise ValueError(
                        f"第 {idx} 行出现新 OP，但上一个 OP 尚未 ---END_OP："
                        f"上一个操作开始于第 {current_op.get('_line_no', '?')} 行"
                    )

                current_op = parse_op_line(stripped, idx)
                continue

            if stripped in PATCH_TEXT_BLOCK_STARTERS:
                if current_op is None:
                    raise ValueError(f"第 {idx} 行出现 {stripped}，但当前没有 OP")

                block_key = PATCH_TEXT_BLOCK_STARTERS[stripped]
                op_type = current_op["op"]

                if op_type in PATH_ONLY_OPS:
                    raise ValueError(f"第 {idx} 行 {op_type} 不允许包含文本块")

                if block_key == "content" and op_type not in (
                    "write_file",
                    "append_text",
                    "replace_between",
                ):
                    raise ValueError(f"第 {idx} 行 {op_type} 不允许使用 ---CONTENT")

                if block_key in ("old", "new") and op_type != "replace_exact":
                    raise ValueError(f"第 {idx} 行 {op_type} 不允许使用 {stripped}")

                ensure_no_duplicate_block(current_op, block_key, idx)

                current_block_key = block_key
                current_block_lines = []
                state = "IN_BLOCK"
                continue

            if stripped == "---END_OP":
                if current_op is None:
                    raise ValueError(f"第 {idx} 行出现 ---END_OP，但当前没有 OP")

                op_index = len(operations) + 1
                validate_completed_op(current_op, op_index)
                operations.append(current_op)
                current_op = None
                continue

            raise ValueError(
                f"第 {idx} 行结构区出现非法内容：{line}\n"
                "结构区只允许空行、---OP、---CONTENT、---OLD、---NEW、---END_OP、包结束标记。"
            )

        if state == "IN_BLOCK":
            if line == boundary:
                if current_op is None or current_block_key is None:
                    raise ValueError(f"第 {idx} 行内部状态错误：文本块无所属 OP")

                current_op[current_block_key] = join_text_block(current_block_lines)
                current_block_key = None
                current_block_lines = []
                state = "STRUCT"
                continue

            current_block_lines.append(line)
            continue

        if state == "END":
            if not line.strip():
                continue

            raise ValueError(f"第 {idx} 行包结束标记之后仍有非空内容：{line}")

    if state == "WAIT_START":
        raise ValueError("未找到 V2 修改包开始标记")

    if state == "IN_BLOCK":
        raise ValueError(
            f"文本块未用 boundary 单独成行结束。"
            f"当前文本块：{current_block_key}；"
            f"所属操作开始于第 {current_op.get('_line_no', '?') if current_op else '?'} 行；"
            f"期望 boundary：{boundary}"
        )

    if state == "STRUCT":
        if current_op is not None:
            raise ValueError(
                f"修改包结束前仍有未关闭 OP：操作开始于第 {current_op.get('_line_no', '?')} 行，"
                f"缺少 ---END_OP 或包结束标记位置错误"
            )

        if not seen_end:
            raise ValueError(f"缺少包结束标记：{PATCH_END}")

    if not operations:
        raise ValueError("修改包中没有任何操作")

    return {
        "version": "2.0",
        "boundary": boundary,
        "operations": operations,
    }