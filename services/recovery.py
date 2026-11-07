# -*- coding: utf-8 -*-

"""正式恢复的完整当前观察、比较及固定业务方案。

依据获取归core.recovery_protection，决定归recovery_selection，
保护和工作执行归core.recovery_execution。
不解析旧格式、不建立第二保护计划、不改变工作目标。
"""

from dataclasses import dataclass, replace
from pathlib import Path

from core.file_observation import (
    FileObservation, TreeObservation, observe_file, observe_tree,
    recheck_file, recheck_tree,
)
from core.protection_projection import ContentState, observation_state
from core.recovery_paths import (
    checked_chain, checked_root, contains, identity, overlaps,
)
from core.recovery_protection import RecoveryEvidence, recheck_recovery_evidence
from services.recovery_comparison import (
    RESTORE_DIFFERENCE_LABELS, RESTORE_STATUS_LABELS,
    compare_restore_object, index_restore_units,
)


@dataclass(frozen=True)
class RestoreTarget:
    """首次正式观察及同次内容、时间视图，不是另一份执行前提。"""

    observed: FileObservation | TreeObservation
    state: ContentState
    file_times: tuple

    @property
    def path(self):
        return self.observed.path

    @property
    def kind(self):
        return self.state.kind

    @property
    def content_records(self):
        return self.state.records


@dataclass(frozen=True)
class RestoreAction:
    payload: object
    current: RestoreTarget
    comparison: object
    restore_decision: str | None = None
    subitem_scope: object = None
    cleanup_plan: object = None

    @property
    def label(self):
        return str(self.current.path)

    @property
    def execute(self):
        if self.restore_decision is None:
            return self.comparison.requires_restore
        if self.restore_decision == "subitems":
            return bool(self.cleanup_plan.steps)
        return self.restore_decision == "restore"


@dataclass(frozen=True)
class RestorePlan:
    root: Path
    actions: tuple
    units: tuple = ()
    kept: tuple = ()
    unchanged: tuple = ()
    finalized: bool = False


def observe_restore_target(path):
    """类型查询不读取正文；随后一次取得完整必要文件或树观察。"""
    target, chain = checked_chain(path, include_observation=True)
    observed = (
        observe_tree(target, include_bytes=True, include_mtime=True)
        if chain[-1][1] == "directory" else
        observe_file(
            target, include_bytes=True, include_digest=True,
            include_mtime=True,
        )
    )
    if observed.chain != chain:
        raise ValueError(f"恢复目标准备期间原路径前提变化：{target}")
    state, times = observation_state(observed)
    return RestoreTarget(observed, state, times)


def recheck_restore_target(current, *, check_mtime):
    if not isinstance(current, RestoreTarget):
        raise TypeError("恢复当前复核必须消费RestoreTarget")
    observed = current.observed
    if isinstance(observed, TreeObservation):
        return recheck_tree(observed, check_mtime=check_mtime)
    return recheck_file(
        observed,
        check_mtime=check_mtime and observed.kind == "file",
    )


def plan_restore(root, evidence):
    """严格依据和完整当前影响共同形成一次业务预检。"""
    if not isinstance(evidence, RecoveryEvidence):
        raise TypeError("正式恢复准备必须消费RecoveryEvidence")
    root = checked_root(root)
    document = evidence.document.value
    associations = index_restore_units(document)
    actions = []
    for entry in document["objects"]:
        target = Path(entry["target"])
        if contains(target, root):
            raise ValueError(f"恢复范围包含业务定位根：{target}")
        if overlaps(target, evidence.task_root):
            raise ValueError(f"恢复范围与源清单或材料重叠：{target}")
        current = observe_restore_target(target)
        comparison = compare_restore_object(
            entry, associations[entry["id"]],
            current, current.file_times,
        )
        action = RestoreAction(entry, current, comparison)
        if (
            entry["original"]["kind"] == "absent"
            and current.kind == "directory"
        ):
            from services.recovery_subitems import build_restore_subitem_scope
            action = replace(
                action,
                subitem_scope=build_restore_subitem_scope(
                    action, associations[entry["id"]],
                ),
            )
        actions.append(action)

    plan = RestorePlan(root, tuple(actions))
    # 晚对象获取跨越早对象的时间边界；
    # 复核只比较，后续仍使用第一次当前观察。
    recheck_recovery_evidence(evidence)
    for action in plan.actions:
        recheck_restore_target(action.current, check_mtime=True)
    return plan


def restore_summary(plan, document):
    """展示消费固定对象和决定，不重新观察或推导保护范围。"""
    if not isinstance(plan, RestorePlan):
        raise TypeError("恢复摘要必须消费RestorePlan")
    comparisons = tuple(
        (action.label, action.comparison) for action in plan.actions
    )
    statuses = {key: 0 for key in RESTORE_STATUS_LABELS}
    differences = {key: 0 for key in RESTORE_DIFFERENCE_LABELS}
    insufficient = 0
    for _, comparison in comparisons:
        statuses[comparison.status] += 1
        insufficient += bool(comparison.uncertain_units)
        for category in {
            category for _, category in comparison.differences
        }:
            differences[category] += 1

    summary = {
        "restore_count": sum(action.execute for action in plan.actions),
        "keep_count": sum(
            action.restore_decision == "keep"
            or action.cleanup_plan is not None
            and not action.execute
            and bool(action.cleanup_plan.retained_records)
            for action in plan.actions
        ),
        "no_operation_count": statuses["no_operation"],
        "time_pending_count": sum(
            action.execute
            and action.comparison.status == "time_pending"
            for action in plan.actions
        ),
        "status_counts": statuses,
        "difference_counts": differences,
        "fact_insufficient_count": insufficient,
        "comparisons": comparisons,
        "has_risk": any(action.execute for action in plan.actions),
        "delete_paths": [],
        "overwrite_paths": [],
        "restore_paths": [],
        "restore_dirs": [],
        "create_parent_paths": [],
        "current_tree_impacts": (),
        "original_states": tuple(
            (
                entry["target"], entry["original"]["kind"],
                entry["file_times"],
            )
            for entry in document["objects"]
        ),
        "expected_states": tuple(
            (
                expected["target"], expected["state"]["kind"],
                expected["state"]["integrity"],
            )
            for unit in document["units"]
            for expected in unit["expected"]
        ),
        "execution_facts": tuple(
            (unit["id"], unit["members"], unit["fact"])
            for unit in document["units"]
        ),
    }
    parents = {}
    impacts = []
    for action in plan.actions:
        if not action.execute:
            continue
        current = action.current
        original = action.payload["original"]["kind"]
        if action.cleanup_plan is not None:
            summary["delete_paths"].extend(
                str(step.item.path) + (
                    "（仅移除空目录）"
                    if step.removal == "empty_directory" else ""
                )
                for step in action.cleanup_plan.steps
            )
            continue
        if current.kind == "directory":
            impacts.append((
                str(current.path),
                tuple(
                    (str(current.path / relative), kind)
                    for relative, kind, _ in current.content_records
                    if relative
                ),
            ))
        if original == "absent":
            summary["delete_paths"].append(
                str(current.path)
                + (
                    "（整个当前目录及全部内容）"
                    if current.kind == "directory" else ""
                )
            )
        else:
            summary["restore_paths"].append(str(current.path))
            if current.kind != "absent":
                summary["overwrite_paths"].append(str(current.path))
            if original == "directory":
                summary["restore_dirs"].append(
                    str(current.path) + "（整体原树）"
                )
            for path, kind in current.observed.chain[:-1]:
                if kind == "absent":
                    parents.setdefault(identity(path), path)
    summary["create_parent_paths"] = list(map(str, parents.values()))
    summary["current_tree_impacts"] = tuple(impacts)
    return summary
