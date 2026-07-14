# -*- coding: utf-8 -*-

from pathlib import Path, PurePosixPath

from core.path_validation import safe_join, normalize_rel_path
from core.text_io import read_text_auto
from services.patch_ops import (
    CONTENT_OPS,
    DUAL_PATH_OPS,
    REPLACE_OPS,
    parse_required_positive_int,
)


def normalize_text_newlines(text: str):
    """
    将文本换行统一为 LF。
    """
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def normalize_contract_path(path):
    return normalize_rel_path(path).replace("\\", "/")


def is_same_or_parent_path(left, right):
    left_path = PurePosixPath(normalize_contract_path(left))
    right_path = PurePosixPath(normalize_contract_path(right))

    if left_path == right_path:
        return True

    left_parts = left_path.parts
    right_parts = right_path.parts

    return (
        len(left_parts) < len(right_parts)
        and right_parts[:len(left_parts)] == left_parts
    ) or (
        len(right_parts) < len(left_parts)
        and left_parts[:len(right_parts)] == right_parts
    )


def iter_op_related_paths(op):
    yield normalize_contract_path(op.get("path", ""))

    if op.get("op") in DUAL_PATH_OPS:
        if "new_path" not in op:
            raise ValueError(f"{op.get('op')} 缺少 new_path 参数")
        yield normalize_contract_path(op.get("new_path", ""))


def collect_path_contract_errors(operations):
    errors = []
    items = []

    for index, op in enumerate(operations, 1):
        op_type = op.get("op", "")

        try:
            paths = list(iter_op_related_paths(op))
        except Exception as e:
            errors.append(f"第 {index} 个 {op_type} 路径参数非法：{e}")
            continue

        if len(paths) == 2 and is_same_or_parent_path(paths[0], paths[1]):
            errors.append(
                f"第 {index} 个 {op_type} 的 path 与 new_path 路径冲突："
                f"{paths[0]} / {paths[1]}"
            )

        items.append({
            "index": index,
            "op": op,
            "op_type": op_type,
            "paths": paths,
        })

    for left_pos, left in enumerate(items):
        for right in items[left_pos + 1:]:
            if left["op_type"] in CONTENT_OPS and right["op_type"] in CONTENT_OPS:
                continue

            for left_path in left["paths"]:
                for right_path in right["paths"]:
                    if is_same_or_parent_path(left_path, right_path):
                        errors.append(
                            f"第 {left['index']} 个 {left['op_type']} 路径 {left_path} 与 "
                            f"第 {right['index']} 个 {right['op_type']} 路径 {right_path} 冲突。"
                        )

    return errors


def validate_path_contracts(operations):
    errors = collect_path_contract_errors(operations)

    if errors:
        raise ValueError("修改包存在路径互斥冲突：\n" + "\n".join(f"- {item}" for item in errors))


def find_text_ranges(text, old):
    ranges = []
    start = 0

    while True:
        index = text.find(old, start)

        if index < 0:
            break

        end = index + len(old)
        ranges.append((index, end))
        start = end

    return ranges


def collect_content_contract_errors(root, operations):
    errors = []
    ranges_by_path = {}

    for index, op in enumerate(operations, 1):
        op_type = op.get("op", "")

        if op_type not in REPLACE_OPS:
            continue

        try:
            rel_path = normalize_contract_path(op.get("path", ""))
            target = safe_join(root, rel_path)
            text, enc = read_text_auto(target)
            text = normalize_text_newlines(text)

            if op_type == "replace_exact":
                old = normalize_text_newlines(op.get("old", ""))

                if not old:
                    errors.append(f"第 {index} 个 replace_exact 的 OLD 文本不能为空")
                    continue

                expected_count = parse_required_positive_int(op.get("count"), "count")
                ranges = find_text_ranges(text, old)

                if len(ranges) != expected_count:
                    errors.append(
                        f"第 {index} 个 replace_exact 命中次数不符："
                        f"expected={expected_count}, actual={len(ranges)}, path={rel_path}"
                    )
                    continue

            else:
                start_marker = op.get("start_marker", "")
                end_marker = op.get("end_marker", "")

                if not start_marker or not end_marker:
                    errors.append(f"第 {index} 个 replace_between 缺少 start_marker 或 end_marker")
                    continue

                s_count = text.count(start_marker)
                e_count = text.count(end_marker)

                if s_count != 1 or e_count != 1:
                    errors.append(
                        f"第 {index} 个 replace_between 锚点不唯一："
                        f"start_count={s_count}, end_count={e_count}, path={rel_path}"
                    )
                    continue

                s_idx = text.find(start_marker)
                e_idx = text.find(end_marker)

                if s_idx >= e_idx:
                    errors.append(f"第 {index} 个 replace_between 起始锚点在结束锚点之后：{rel_path}")
                    continue

                ranges = [(s_idx, e_idx + len(end_marker))]

            for start, end in ranges:
                ranges_by_path.setdefault(rel_path, []).append({
                    "index": index,
                    "op_type": op_type,
                    "start": start,
                    "end": end,
                })

        except Exception as e:
            errors.append(f"第 {index} 个 {op_type} 内容互斥校验失败：{e}")

    for rel_path, ranges in ranges_by_path.items():
        ranges.sort(key=lambda item: (item["start"], item["end"]))

        for previous, current in zip(ranges, ranges[1:]):
            if current["start"] < previous["end"]:
                errors.append(
                    f"{rel_path} 中第 {previous['index']} 个 {previous['op_type']} "
                    f"与第 {current['index']} 个 {current['op_type']} "
                    "旧文本位置区间发生重叠。"
                )

    return errors


def validate_content_contracts(root, operations):
    errors = collect_content_contract_errors(root, operations)

    if errors:
        raise ValueError("修改包存在内容互斥冲突：\n" + "\n".join(f"- {item}" for item in errors))


def validate_global_patch_contracts(root, operations):
    path_errors = collect_path_contract_errors(operations)
    content_errors = collect_content_contract_errors(root, operations)

    errors = []
    errors.extend(f"路径互斥：{item}" for item in path_errors)
    errors.extend(f"内容互斥：{item}" for item in content_errors)

    if errors:
        raise ValueError("修改包存在全局冲突：\n" + "\n".join(f"- {item}" for item in errors))