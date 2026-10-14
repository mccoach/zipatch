# -*- coding: utf-8 -*-

"""全局路径互斥和已定位旧文本区间的互斥检查。"""

import os
from pathlib import PurePosixPath

from core.path_validation import normalize_rel_path
from services.patch_ops import CONTENT_OPS, DUAL_PATH_OPS


def normalize_contract_path(path):
    normalized = normalize_rel_path(path)
    return os.path.normcase(os.path.normpath(normalized)).replace("\\", "/")


def is_same_or_parent_path(left, right):
    left_path = PurePosixPath(normalize_contract_path(left))
    right_path = PurePosixPath(normalize_contract_path(right))
    left_parts, right_parts = left_path.parts, right_path.parts
    return (
        left_path == right_path
        or len(left_parts) < len(right_parts) and right_parts[:len(left_parts)] == left_parts
        or len(right_parts) < len(left_parts) and left_parts[:len(right_parts)] == right_parts
    )


def iter_op_related_paths(op):
    yield normalize_contract_path(op.get("path", ""))
    if op.get("op") in DUAL_PATH_OPS:
        if "new_path" not in op:
            raise ValueError(f'id="{op.get("id", "")}" {op.get("op")} 缺少 new_path 参数')
        yield normalize_contract_path(op["new_path"])


def collect_path_contract_errors(operations):
    errors, items = [], []
    for op in operations:
        kind, identifier = op.get("op", ""), op.get("id", "")
        try:
            paths = tuple(iter_op_related_paths(op))
        except ValueError as error:
            errors.append(f'id="{identifier}" {kind} 路径参数非法：{error}')
            continue
        if len(paths) == 2 and is_same_or_parent_path(paths[0], paths[1]):
            errors.append(
                f'id="{identifier}" {kind} 的 path 与 new_path 路径冲突：'
                f"{paths[0]} / {paths[1]}"
            )
        items.append((kind, identifier, paths))
    for index, (left_kind, left_id, left_paths) in enumerate(items):
        for right_kind, right_id, right_paths in items[index + 1:]:
            if left_kind in CONTENT_OPS and right_kind in CONTENT_OPS:
                continue
            for left in left_paths:
                for right in right_paths:
                    if is_same_or_parent_path(left, right):
                        errors.append(
                            f'id="{left_id}" {left_kind} 路径 {left} 与 '
                            f'id="{right_id}" {right_kind} 路径 {right} 冲突。'
                        )
    return errors


def collect_content_contract_errors(located_operations):
    """消费预演已定位区间，不读取正文、不重新搜索。"""
    by_path = {}
    for operation, location in located_operations:
        path = normalize_contract_path(operation["path"])
        for start, end in location.original_ranges:
            by_path.setdefault(path, []).append(
                (start, end, operation["id"], operation["op"])
            )
    errors = []
    for path, ranges in by_path.items():
        ranges.sort()
        previous = None
        for current in ranges:
            if previous is not None and current[0] < previous[1]:
                errors.append(
                    f'{path} 中 id="{previous[2]}" {previous[3]} '
                    f'与 id="{current[2]}" {current[3]} 旧文本位置区间发生重叠。'
                )
            if previous is None or current[1] > previous[1]:
                previous = current
    return errors