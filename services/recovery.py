# -*- coding: utf-8 -*-

"""完整原状态和当前固定观察形成整包恢复计划。"""

from dataclasses import dataclass
from pathlib import Path

from core.file_observation import (
    observe_file, observe_tree, recheck_file, recheck_tree, TreeObservation,
)
from core.recovery_execution import matches_original_content
from core.recovery_paths import checked_chain, overlaps
from core.recovery_protection import RecoveryEvidence, recheck_recovery_evidence


@dataclass(frozen=True)
class RestoreAction:
    original: object
    current: object
    execute: bool

    @property
    def target(self):
        return self.current.path


@dataclass(frozen=True)
class RestorePlan:
    actions: tuple


def plan_restore(evidence):
    if not isinstance(evidence, RecoveryEvidence):
        raise TypeError("恢复规划必须消费正式恢复依据")
    actions = []
    for original in evidence.document.value["objects"]:
        target = Path(original["target"])
        if overlaps(target, evidence.task_root):
            raise ValueError("恢复目标与源备份重叠")
        target, chain = checked_chain(target, include_observation=True)
        current = (
            observe_tree(target, include_bytes=True, include_mtime=True)
            if chain[-1][1] == "directory" else
            observe_file(
                target, include_bytes=True, include_digest=True,
                include_mtime=True,
            )
        )
        if current.chain != chain:
            raise ValueError(f"恢复规划期间路径前提变化：{target}")
        actions.append(RestoreAction(
            original, current,
            not matches_original_content(current, original["original"]),
        ))
    recheck_recovery_evidence(evidence)
    for action in actions:
        if isinstance(action.current, TreeObservation):
            recheck_tree(action.current)
        else:
            recheck_file(action.current)
    return RestorePlan(tuple(actions))
