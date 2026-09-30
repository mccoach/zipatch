# -*- coding: utf-8 -*-

import re
import shlex

from core.constants import (
    PATCH_PROTOCOL_NAME,
    PATCH_BOUNDARY_PREFIX,
    PATCH_START_PREFIX,
    PATCH_END,
    SUPPORTED_PATCH_OPS,
    PATCH_TEXT_BLOCK_STARTERS,
)
from core.text_io import split_lines_keep_text
from services.patch_ops import (
    OP_ALLOWED_HEAD_PARAMS,
    OP_REQUIRED_TEXT_BLOCKS,
    parse_required_positive_int,
)


def validate_boundary(boundary: str):
    if not isinstance(boundary, str) or not boundary:
        raise ValueError("boundary 不能为空")

    if len(boundary) < 32:
        raise ValueError("boundary 太短，至少需要 32 个字符")


    if not re.fullmatch(r"[A-Za-z0-9_.-]+", boundary):
        raise ValueError("boundary 只能包含字母、数字、下划线、短横线和点")

    forbidden = {
        PATCH_END,
        "---OP",
        *PATCH_TEXT_BLOCK_STARTERS.keys(),
        "---END_OP",
    }

    if boundary in forbidden:
        raise ValueError(f"boundary 不能等于协议关键字：{boundary}")

    if not boundary.startswith(PATCH_BOUNDARY_PREFIX):
        raise ValueError(f"boundary 必须以 {PATCH_BOUNDARY_PREFIX} 开头")

    if not re.search(r"\d{14}", boundary):
        raise ValueError("boundary 必须包含 14 位创建时间码 YYYYMMDDHHMMSS")


def validate_op_id(op_id: str, line_no: int):
    if not isinstance(op_id, str) or not op_id:
        raise ValueError(f"第 {line_no} 行 OP id 不能为空")

    if not re.fullmatch(r"[A-Za-z0-9_-]+", op_id):
        raise ValueError(
            f"第 {line_no} 行 OP id 格式非法：{op_id}。"
            "id 只能包含字母、数字、下划线和短横线"
        )


def is_patch_start_candidate(line: str):
    return line.strip().startswith(PATCH_START_PREFIX)


def parse_start_line(line: str):
    pattern = (
        rf'^<<{re.escape(PATCH_PROTOCOL_NAME)}\s+'
        r'boundary="([^"]+)">>$'
    )
    match = re.match(pattern, line.strip())

    if not match:
        expected = (
            f'<<{PATCH_PROTOCOL_NAME} '
            f'boundary="{PATCH_BOUNDARY_PREFIX}...">>'
        )
        raise ValueError(
            f"V3 修改包首行格式非法。应为：{expected}"
        )

    boundary = match.group(1)
    validate_boundary(boundary)

    return boundary


def parse_op_line(line: str, line_no: int):
    try:
        parts = shlex.split(line, posix=True)
    except ValueError as e:
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

        if key in attrs:
            if key == "id":
                raise ValueError(
                    f'第 {line_no} 行 OP 参数重复：id。'
                    f'请在修改包中搜索：id="{value}"'
                )
            raise ValueError(f"第 {line_no} 行 OP 参数重复：{key}")

        attrs[key] = value

    if "id" not in attrs:
        raise ValueError(f"第 {line_no} 行 OP 缺少 id 参数")

    validate_op_id(attrs["id"], line_no)

    allowed_params = OP_ALLOWED_HEAD_PARAMS[op_type]
    actual_params = set(attrs) - {"op", "_line_no"}
    unknown_params = actual_params - allowed_params

    if unknown_params:
        names = "、".join(sorted(unknown_params))
        raise ValueError(
            f'第 {line_no} 行 id="{attrs["id"]}" {op_type} '
            f"包含不允许的 OP 头参数：{names}。"
            "路径、目标路径和源码锚点必须使用对应的 boundary 文本块。"
        )

    return attrs


def join_text_block(lines):
    return "\n".join(lines)


def ensure_no_duplicate_block(op, key, line_no):
    if key in op:
        raise ValueError(
            f'第 {line_no} 行 id="{op.get("id", "")}" 重复出现文本块 {key}，'
            f"当前操作开始于第 {op.get('_line_no', '?')} 行"
        )


def validate_required_source_text(op, key, block_name, op_index):
    if key not in op:
        return

    if op[key] == "":
        raise ValueError(
            f'第 {op_index} 个 id="{op["id"]}" {op["op"]} '
            f"的 {block_name} 文本块不能为空"
        )


def validate_completed_op(op, op_index):
    op_type = op["op"]
    op_id = op["id"]

    required_blocks = OP_REQUIRED_TEXT_BLOCKS[op_type]
    present_blocks = {
        block_key
        for block_key in PATCH_TEXT_BLOCK_STARTERS.values()
        if block_key in op
    }

    missing_blocks = required_blocks - present_blocks
    unexpected_blocks = present_blocks - required_blocks

    if missing_blocks:
        block_names = "、".join(
            key
            for marker, key in PATCH_TEXT_BLOCK_STARTERS.items()
            if key in missing_blocks
        )
        raise ValueError(
            f'第 {op_index} 个 id="{op_id}" {op_type} '
            f"缺少必要文本块：{block_names}"
        )

    if unexpected_blocks:
        block_names = "、".join(
            key
            for marker, key in PATCH_TEXT_BLOCK_STARTERS.items()
            if key in unexpected_blocks
        )
        raise ValueError(
            f'第 {op_index} 个 id="{op_id}" {op_type} '
            f"包含不允许的文本块：{block_names}"
        )

    validate_required_source_text(op, "path", "---PATH", op_index)
    validate_required_source_text(op, "new_path", "---NEW_PATH", op_index)
    validate_required_source_text(
        op,
        "start_marker",
        "---START_MARKER",
        op_index,
    )
    validate_required_source_text(
        op,
        "end_marker",
        "---END_MARKER",
        op_index,
    )
    validate_required_source_text(op, "old", "---OLD", op_index)

    if op_type == "replace_exact":
        parse_required_positive_int(op.get("count"), "count")


def parse_patch_v3(text: str):
    """
    V3 状态机解析器。

    所有取自文件系统或源代码、必须保持原文语义的执行要素，
    都通过 boundary 文本块读取。OP 头只保留受严格字符集、整数
    或枚举约束的结构参数。
    """
    lines = split_lines_keep_text(text)

    state = "WAIT_START"
    boundary = None
    operations = []
    seen_op_ids = set()
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
                f"第 {idx} 行不是 V3 修改包开始行。"
                f"首个非空行必须是 <<{PATCH_PROTOCOL_NAME} "
                f'boundary="{PATCH_BOUNDARY_PREFIX}...">>'
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
                        f'第 {idx} 行遇到包结束标记，但 id="{current_op.get("id", "")}" '
                        f"操作尚未 ---END_OP："
                        f"操作开始于第 {current_op.get('_line_no', '?')} 行"
                    )

                seen_end = True
                state = "END"
                continue

            if stripped.startswith("---OP "):
                if current_op is not None:
                    raise ValueError(
                        f'第 {idx} 行出现新 OP，但 id="{current_op.get("id", "")}" '
                        f"上一个 OP 尚未 ---END_OP："
                        f"上一个操作开始于第 {current_op.get('_line_no', '?')} 行"
                    )

                current_op = parse_op_line(stripped, idx)
                continue

            if stripped in PATCH_TEXT_BLOCK_STARTERS:
                if current_op is None:
                    raise ValueError(f"第 {idx} 行出现 {stripped}，但当前没有 OP")

                block_key = PATCH_TEXT_BLOCK_STARTERS[stripped]
                op_type = current_op["op"]
                op_id = current_op["id"]
                allowed_blocks = OP_REQUIRED_TEXT_BLOCKS[op_type]

                if block_key not in allowed_blocks:
                    raise ValueError(
                        f'第 {idx} 行 id="{op_id}" {op_type} '
                        f"不允许使用 {stripped}"
                    )

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

                op_id = current_op["id"]

                if op_id in seen_op_ids:
                    raise ValueError(
                        f'第 {op_index} 个操作 id 重复：id="{op_id}"。'
                        f'请在修改包中搜索：id="{op_id}"'
                    )

                seen_op_ids.add(op_id)
                operations.append(current_op)
                current_op = None
                continue

            raise ValueError(
                f"第 {idx} 行结构区出现非法内容：{line}\n"
                "结构区只允许空行、---OP、协议定义的文本块、"
                "---END_OP 和包结束标记。"
            )

        if state == "IN_BLOCK":
            if line == boundary:
                if current_op is None or current_block_key is None:
                    raise ValueError(f"第 {idx} 行内部状态错误：文本块无所属 OP")

                current_op[current_block_key] = join_text_block(
                    current_block_lines
                )
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
        raise ValueError("未找到 V3 修改包开始标记")

    if state == "IN_BLOCK":
        raise ValueError(
            "文本块未用 boundary 单独成行结束。"
            f"当前文本块：{current_block_key}；"
            f'所属操作 id="{current_op.get("id", "") if current_op else ""}"；'
            f"所属操作开始于第 "
            f'{current_op.get("_line_no", "?") if current_op else "?"} 行；'
            f"期望 boundary：{boundary}"
        )

    if state == "STRUCT":
        if current_op is not None:
            raise ValueError(
                f'修改包结束前仍有未关闭 OP：id="{current_op.get("id", "")}"，'
                f"操作开始于第 {current_op.get('_line_no', '?')} 行，"
                "缺少 ---END_OP 或包结束标记位置错误"
            )

        if not seen_end:
            raise ValueError(f"缺少包结束标记：{PATCH_END}")

    if not operations:
        raise ValueError("修改包中没有任何操作")

    return {
        "version": "3.0",
        "boundary": boundary,
        "operations": operations,
    }