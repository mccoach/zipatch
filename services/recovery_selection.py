# -*- coding: utf-8 -*-

"""本次恢复对象及子项决定投影；不访问磁盘或执行恢复。"""

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType


RESTORE_DECISION_LABELS = {
    "undecided": "未决定",
    "restore": "恢复原始状态",
    "keep": "保留当前状态",
    "no_operation": "无需操作",
    "subitems": "按固定子项清理／保留",
}


def initial_restore_decisions(plan):
    decisions = {}
    for action in plan.actions:
        identifier = action.payload["id"]
        if identifier in decisions:
            raise ValueError("恢复对象编号重复")
        if action.comparison is None:
            raise ValueError("恢复对象缺少固定比较")
        decisions[identifier] = (
            "undecided" if action.comparison.requires_restore else "no_operation"
        )
    return MappingProxyType(decisions)


def validate_restore_decisions(plan, decisions):
    if not isinstance(decisions, Mapping):
        raise TypeError("恢复决定必须是对象编号到决定的映射")
    required = {}
    for action in plan.actions:
        identifier = action.payload["id"]
        if identifier in required:
            raise ValueError("恢复对象编号重复")
        if action.comparison is None:
            raise ValueError("恢复对象缺少固定比较")
        required[identifier] = action
    if set(decisions) != set(required):
        raise ValueError("恢复决定必须准确覆盖本次全部正式对象")
    for identifier, action in required.items():
        needed = action.comparison.requires_restore
        decision = decisions[identifier]
        if type(decision) is not str or decision not in RESTORE_DECISION_LABELS:
            raise ValueError(f"恢复决定非法：{identifier}")
        if needed and decision == "no_operation":
            raise ValueError("尚未满足原状态的对象不能声明无需操作")
        if not needed and decision != "no_operation":
            raise ValueError("已满足原状态的对象必须保持无需操作")
        if decision == "subitems" and action.subitem_scope is None:
            raise ValueError(
                "此对象必须整体恢复或保留，不支持子项清理；"
                "不能保留后代同时整体替换原对象"
            )
    return required


def change_restore_decisions(
    plan, decisions, identifiers, decision, *,
    scope="selected", replace_decisions=False,
):
    required = validate_restore_decisions(plan, decisions)
    if type(decision) is not str or decision not in ("restore", "keep"):
        raise ValueError("只能选择恢复原始状态或保留当前状态")
    if type(scope) is not str or scope not in ("selected", "remaining", "all"):
        raise ValueError("决定范围必须是 selected、remaining 或 all")
    if type(replace_decisions) is not bool:
        raise TypeError("全部重设许可必须是布尔值")
    if not isinstance(identifiers, (tuple, list)):
        raise TypeError("选中对象编号必须是数组或元组")
    identifiers = tuple(identifiers)
    if any(type(identifier) is not str for identifier in identifiers):
        raise ValueError("选中对象编号必须是字符串")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("选中对象编号重复")
    if scope == "selected":
        if not identifiers:
            raise ValueError("请先选择至少一个待决定对象")
        if any(identifier not in required for identifier in identifiers):
            raise ValueError("选中对象不属于本次正式恢复任务")
        if any(
            not required[identifier].comparison.requires_restore
            for identifier in identifiers
        ):
            raise ValueError("无需操作对象不能设置恢复或保留")
        replaces_subitems = any(
            decisions[identifier] == "subitems" for identifier in identifiers
        )
        if replace_decisions and not replaces_subitems:
            raise ValueError("全部重设许可只用于全部对象设置或替换子项方案")
        if replaces_subitems and not replace_decisions:
            raise ValueError("整体决定将替换子项方案，必须明确确认重设")
        selected = identifiers
    else:
        if identifiers:
            raise ValueError("剩余或全部设置不得夹带选中对象编号")
        if scope == "remaining":
            if replace_decisions:
                raise ValueError("剩余设置不能覆盖已有决定")
            selected = tuple(
                identifier for identifier, value in decisions.items()
                if value == "undecided"
            )
        else:
            selected = tuple(
                identifier for identifier, action in required.items()
                if action.comparison.requires_restore
            )
            if not replace_decisions and any(
                decisions[identifier] in ("restore", "keep", "subitems")
                and decisions[identifier] != decision
                for identifier in selected
            ):
                raise ValueError(
                    "全部设置将重设已有决定；必须明确确认后再提交全部重设"
                )
    candidate = dict(decisions)
    for identifier in selected:
        candidate[identifier] = decision
    return MappingProxyType(candidate)


def selected_restore_plan(plan, decisions, *, subitem_decisions=None):
    """最终决定纯投影；保护范围只由共享执行核心形成。"""
    from core.protection_projection import ContentState, FixedEffect, FixedUnit
    from services.recovery import RestorePlan
    from services.recovery_cleanup import cleanup_effects
    from services.recovery_subitems import selected_cleanup_plan

    if not isinstance(plan, RestorePlan):
        raise TypeError("最终恢复决定必须消费RestorePlan")
    if plan.finalized:
        raise ValueError("恢复方案已经固定")
    validate_restore_decisions(plan, decisions)
    if any(value == "undecided" for value in decisions.values()):
        raise ValueError("仍有恢复对象未决定，不能形成最终方案")
    selected_subitems = {} if subitem_decisions is None else subitem_decisions
    if not isinstance(selected_subitems, Mapping):
        raise TypeError("子项决定必须按正式对象编号关联")
    if set(selected_subitems) != {
        identifier for identifier, decision in decisions.items()
        if decision == "subitems"
    }:
        raise ValueError("子项决定必须准确对应本次选择子项模式的对象")

    actions, units, kept, unchanged = [], [], [], []
    for action in plan.actions:
        identifier = action.payload["id"]
        decision = decisions[identifier]
        projected = replace(
            action, restore_decision=decision, cleanup_plan=None,
        )
        effects = ()
        if decision == "subitems":
            cleanup = selected_cleanup_plan(
                action.subitem_scope, selected_subitems[identifier],
            )
            projected = replace(projected, cleanup_plan=cleanup)
            effects = cleanup_effects(projected)
            if cleanup.retained_records:
                # 核心消费完整首次当前树，从真实删除步骤核验剩余范围。
                kept.append(action.current.observed)
        elif decision == "restore":
            original = action.payload["original"]
            effects = (FixedEffect(
                action.current.observed,
                ContentState(
                    original["kind"], original["records"],
                    original["integrity"],
                ),
            ),)
        elif decision == "keep":
            kept.append(action.current.observed)
        elif decision == "no_operation":
            unchanged.append(action.current.observed)
        else:
            raise ValueError("未收齐合法恢复决定")
        if effects:
            units.append(FixedUnit((identifier,), effects))
        actions.append(projected)
    return RestorePlan(
        plan.root, tuple(actions), tuple(units),
        tuple(kept), tuple(unchanged), True,
    )


def restore_content_loss_paths(plan):
    """仅时间不满足不冒充内容损失。"""
    paths = []
    for action in plan.actions:
        if not action.execute:
            continue
        if action.cleanup_plan is not None:
            paths.extend(
                str(step.item.path) for step in action.cleanup_plan.steps
                if step.removal == "file"
            )
            continue
        current = action.current
        original = action.payload["original"]
        if current.kind != "absent" and (
            current.kind != original["kind"]
            or current.content_records
            != tuple(tuple(record) for record in original["records"])
        ):
            paths.append(str(current.path))
    return tuple(paths)


def restore_plan_complete_original_state(plan):
    """固定方案能否满足全部原状态，不把部分清理当成整体恢复。"""
    return all(
        action.restore_decision != "keep"
        and (
            action.cleanup_plan is None
            or action.cleanup_plan.complete_original_state
        )
        for action in plan.actions
    )