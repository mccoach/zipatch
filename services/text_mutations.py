# -*- coding: utf-8 -*-

"""修改包替换参数到共用定位能力的正式映射。"""

from core.text_locations import locate_between, locate_exact


def locate_replacement(snapshot, operation, expected_count):
    """消费当前阶段参数校验已经准备的次数，不再次解析 OP。"""
    kind = operation["op"]
    if kind == "replace_exact":
        return locate_exact(snapshot, operation["old"], expected_count)
    if kind == "replace_between":
        return locate_between(
            snapshot, operation["start_marker"], operation["end_marker"],
        )
    raise ValueError(f"不是文本替换操作：{kind}")