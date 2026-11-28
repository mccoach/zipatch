# -*- coding: utf-8 -*-

"""完整原状态、明确实际访问授权和当前固定观察形成整包恢复计划。

清单保留原始名义位置；工作观察只使用实际普通父链。
访问依赖独立保存，不追加为保护或恢复对象。
"""

from dataclasses import dataclass
from pathlib import Path

from core.file_observation import (
    TreeObservation, observe_file, observe_tree, recheck_file, recheck_tree,
)
from core.link_paths import (
    LinkPathObservation, inspect_link_path, recheck_link_path,
    validate_link_specification,
)
from core.recovery_document import content_state_from_value
from core.recovery_execution import matches_original_content
from core.recovery_paths import contains, identity, overlaps
from core.recovery_protection import RecoveryEvidence, recheck_recovery_evidence


@dataclass(frozen=True)
class RestoreAction:
    original: object
    current: object
    execute: bool
    access: LinkPathObservation

    @property
    def target(self):
        return self.current.path


@dataclass(frozen=True)
class RestorePlan:
    actions: tuple


def _validate_target_ranges(accesses, evidence):
    """只比较已定位范围；不读取正文、枚举目录或增加恢复授权。"""
    for number, access in enumerate(accesses):
        if overlaps(access.actual_path, evidence.task_root):
            raise ValueError("实际恢复目标与源备份重叠")
        for previous in accesses[:number]:
            if overlaps(previous.actual_path, access.actual_path):
                raise ValueError(
                    "清单中的不同名义目标对应重复或父子重叠的实际恢复范围："
                    f"{previous.nominal_path} -> {previous.actual_path}；"
                    f"{access.nominal_path} -> {access.actual_path}"
                )

    for access in accesses:
        for definition in access.followed_links:
            if overlaps(definition.entry_path, evidence.task_root):
                raise ValueError("恢复访问链接依赖与源备份重叠")
            if any(
                overlaps(other.actual_path, definition.entry_path)
                for other in accesses
            ):
                raise ValueError(
                    "实际恢复范围会改变本次链接访问依赖："
                    f"{definition.entry_path}"
                )
        for directory in access.directories:
            if contains(access.actual_path, directory.path):
                continue
            if any(
                contains(other.actual_path, directory.path)
                for other in accesses
            ):
                raise ValueError(
                    "实际恢复范围包含本次访问所依赖的原父目录："
                    f"{directory.path}"
                )


def _authorize_targets(entries, accesses, authorize):
    """第一次实际目标枚举或旧正文读取之前，决定明确的访问范围。"""
    if not any(access.followed_links for access in accesses):
        return
    if authorize is not None and not callable(authorize):
        raise TypeError("恢复实际目标访问授权必须可调用")

    lines = [
        "【本次备份恢复的全部固定目标】",
        "以下仅完成路径元信息定位，尚未枚举目标目录或读取旧目标正文。",
        "清单位置不改写；确认后只在列出的实际位置恢复保存的原状态。",
        "目录范围包括该实际普通目录的普通子树；途中链接仍作为结构叶子。",
        "末级链接只作为指定入口，不访问其引用目标。",
        "",
    ]
    for entry, access in zip(entries, accesses):
        lines.extend((
            f"清单名义位置：{access.nominal_path}",
            f"实际恢复位置：{access.actual_path}",
            f"当前类型：{access.kind}",
            f"保存原类型：{entry['original']['kind']}",
        ))
        for definition in access.followed_links:
            lines.extend((
                f"访问依赖入口：{definition.entry_path}",
                f"访问依赖类型：{definition.kind}",
                f"实际原定义：{definition.raw_target!r}",
                f"直接引用位置：{definition.direct_target}",
            ))
        desired = content_state_from_value(
            entry["original"], target=access.actual_path,
        )
        for relative, specification in desired.link_definitions:
            nominal_entry = access.nominal_path / relative
            actual_entry = access.actual_path / relative
            lines.extend((
                f"待恢复链接入口：{actual_entry}",
                f"保存原定义：{specification.raw_target!r}",
                "原名义位置解释的直接引用："
                f"{validate_link_specification(specification, nominal_entry)}",
                "实际恢复位置解释的直接引用："
                f"{validate_link_specification(specification, actual_entry)}",
                "原定义保持原样；父目录位置变化可能改变相对引用位置。",
            ))
        lines.append("")

    lines.extend((
        "授权允许为本次固定恢复规划读取实际旧正文及枚举上述普通目录范围。",
        "这不是工作执行确认；完整预检后仍须确认整包恢复。",
        "访问依赖不会追加为备份、删除、重建或恢复对象。",
        "任何目标别名、范围冲突、定义变化或后续复核失败都会停止。",
        "取消不会读取旧目标正文、枚举目标目录或开始还原前备份。",
        "授权只属于本次任务，不保存到用户配置。",
    ))
    description = "\n".join(lines)
    if authorize is None:
        raise ValueError(
            "恢复目标父链经过链接，尚未取得本次实际访问授权。\n"
            + description
        )
    decision = authorize("备份恢复实际目标访问授权", description)
    if type(decision) is not bool:
        raise TypeError("恢复实际访问授权必须返回明确布尔值")
    if not decision:
        raise ValueError("本次恢复实际访问授权已取消，未开始目标获取或工作")


def plan_restore(
    evidence, *, authorize_restore_scope=None, select_restore_scope=None,
):
    """元信息定位、联合范围检查、授权及固定当前状态获取。

    同一目标的普通正文只在授权后首次获取。
    后续重新读取或枚举仅用于新边界比较，不替换本次工作基线。
    自动回滚不提供交互；其保护清单已记录实际位置。
    """
    if not isinstance(evidence, RecoveryEvidence):
        raise TypeError("恢复规划必须消费正式恢复依据")
    if authorize_restore_scope is not None and not callable(authorize_restore_scope):
        raise TypeError("恢复实际目标访问授权必须可调用")
    if select_restore_scope is not None and not callable(select_restore_scope):
        raise TypeError("恢复固定对象选择必须可调用")
    recheck_recovery_evidence(evidence)
    entries = tuple(evidence.document.value["objects"])
    accesses = []
    for original in entries:
        target = Path(original["target"])
        if overlaps(target, evidence.task_root):
            raise ValueError("恢复名义目标与源备份重叠")
        access = inspect_link_path(target, follow_final_link=False)
        if access.kind not in (
            "absent", "file", "directory",
            "file_symlink", "directory_symlink", "junction",
        ):
            raise ValueError("恢复目标类型不支持")
        # 保存定义必须能在实际入口位置准确解释；纯校验不访问引用目标。
        content_state_from_value(
            original["original"], target=access.actual_path,
        )
        accesses.append(access)
    accesses = tuple(accesses)
    # 全部明确元信息先检查；非法清单或实际别名冲突不能靠局部选择逃避。
    _validate_target_ranges(accesses, evidence)
    metadata = None
    if select_restore_scope is None:
        _authorize_targets(entries, accesses, authorize_restore_scope)
    else:
        from core.work_selection import selected_indices
        from services.patch_scope import inspect_scope_tree
        from services.link_authorization import content_selection_groups
        from core.file_observation import FileObservation

        # 主动对象选择允许必要元信息预扫描，正文仍在选择后获取。
        # 自动回滚不进入交互分支，不增加扫描许可或二次确认。
        metadata = tuple(
            inspect_scope_tree(access.actual_path)
            if access.kind == "directory" else
            inspect_link_path(access.actual_path, follow_final_link=False)
            if access.entry_link is not None else
            observe_file(access.actual_path)
            for access in accesses
        )
        for access in accesses:
            recheck_link_path(access)
        written = {
            member.object_identity
            for entry, member in zip(entries, metadata)
            if isinstance(member, FileObservation)
            and member.kind == "file"
            and entry["original"]["kind"] == "file"
        }
        groups = content_selection_groups(
            ((member,) for member in metadata), written,
        )
        retained = set(selected_indices(
            select_restore_scope(entries, accesses, metadata), len(entries),
            description="备份还原范围选择",
        ))
        for group in groups:
            if any(number not in retained for number in group):
                retained.difference_update(group)
        retained = tuple(
            number for number in range(len(entries)) if number in retained
        )
        entries = tuple(entries[number] for number in retained)
        accesses = tuple(accesses[number] for number in retained)
        metadata = tuple(metadata[number] for number in retained)
        # 选择同时授予详情已经明确展示的所选旧状态获取范围。
        # 未选对象不获取正文、不枚举目录，不保留执行依赖。
        _validate_target_ranges(accesses, evidence)

    # 授权交互跨越新时间边界，任何旧位置改指向都拒绝。
    recheck_recovery_evidence(evidence)
    for access in accesses:
        recheck_link_path(access)

    actions = []
    for number, (original, access) in enumerate(zip(entries, accesses)):
        recheck_link_path(access)
        if metadata is not None:
            from services.patch_scope import materialize_scope_observation

            current = materialize_scope_observation(metadata[number])
        elif access.entry_link is not None:
            current = inspect_link_path(
                access.actual_path, follow_final_link=False,
            )
            if (
                current.followed_links
                or current.nominal_path != access.actual_path
                or current.actual_path != access.actual_path
                or current.kind != access.kind
                or current.object_identity != access.object_identity
                or current.entry_link != access.entry_link
            ):
                raise ValueError("恢复实际链接入口偏离固定名义访问事实")
        elif access.kind == "directory":
            current = observe_tree(
                access.actual_path, include_bytes=True,
                include_mtime=True, include_links=True,
            )
            if current.directories[0][1].object_identity != access.object_identity:
                raise ValueError("恢复目录获取期间实际根身份变化")
        else:
            current = observe_file(
                access.actual_path, include_bytes=True,
                include_digest=True, include_mtime=True,
            )
            if (
                current.kind != access.kind
                or current.object_identity != access.object_identity
            ):
                raise ValueError("恢复文件获取期间实际存在性或身份变化")
        recheck_link_path(access)
        actions.append(RestoreAction(
            original, current,
            not matches_original_content(current, original["original"]),
            access,
        ))

    recheck_recovery_evidence(evidence)
    for action in actions:
        recheck_link_path(action.access)
        if isinstance(action.current, LinkPathObservation):
            recheck_link_path(action.current)
        elif isinstance(action.current, TreeObservation):
            recheck_tree(action.current)
        else:
            recheck_file(action.current)
    return RestorePlan(tuple(actions))