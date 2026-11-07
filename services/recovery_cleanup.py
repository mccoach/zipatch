# -*- coding: utf-8 -*-

"""固定子项决定到最少完整删除effects；不读取磁盘或执行删除。"""

from dataclasses import replace
from pathlib import PurePosixPath

from core.file_observation import TreeObservation
from core.protection_projection import FixedEffect, absent_state
from core.recovery_content import content_integrity
from core.recovery_paths import contains
from services.recovery_subitems import RestoreCleanupPlan, selected_cleanup_plan


def validate_cleanup_action(action):
    cleanup = action.cleanup_plan
    if not isinstance(cleanup, RestoreCleanupPlan):
        raise TypeError("选择性恢复必须消费RestoreCleanupPlan")
    observed = action.current.observed
    scope = cleanup.scope
    if (
        not isinstance(observed, TreeObservation)
        or action.payload["original"]["kind"] != "absent"
        or action.subitem_scope is not scope
        or scope.object_id != action.payload["id"]
        or scope.target != observed.path
        or scope.current_records != observed.content_records
        or scope.current_file_times != observed.file_times
    ):
        raise ValueError("清理方案不对应本次完整固定当前树")
    if cleanup != selected_cleanup_plan(scope, cleanup.decisions):
        raise ValueError("清理步骤或保留结果不符合固定决定投影")
    return cleanup


def _subtree(observed, relative):
    """完整子树纯投影，保留原成员身份、bytes、属性和正式时间。"""
    prefix = relative + "/" if relative else ""

    def within(position):
        return (
            not relative or position == relative
            or position.startswith(prefix)
        )

    def local(position):
        return "" if position == relative else position[len(prefix):]

    parts = PurePosixPath(relative).parts if relative else ()
    target = observed.path.joinpath(*parts)
    chain = (
        *observed.chain,
        *(
            (observed.path.joinpath(*parts[:number]), "directory")
            for number in range(1, len(parts) + 1)
        ),
    )
    records = tuple(
        (local(position), kind, digest)
        for position, kind, digest in observed.content_records
        if within(position)
    )
    directories = tuple(
        (local(position), member)
        for position, member in observed.directories
        if within(position)
    )
    if not directories or directories[0][0] != "":
        raise ValueError("固定清理子树缺少原观察中的普通目录根")
    return replace(
        observed, path=target, chain=chain,
        directories=directories,
        files=tuple(
            (local(position), member)
            for position, member in observed.files
            if within(position)
        ),
        content_records=records,
        integrity=content_integrity("directory", records),
        file_times=tuple(
            (local(position), modified)
            for position, modified in observed.file_times
            if within(position)
        ),
    )


def cleanup_effects(action):
    """纯决定形成完整删除根；共享核心仍逐名称删除，不递归兜底。"""
    cleanup = validate_cleanup_action(action)
    observed = action.current.observed
    roots = []
    for step in sorted(
        cleanup.steps,
        key=lambda member: (
            len(member.item.path.parts), str(member.item.path),
        ),
    ):
        if not any(
            contains(root.item.path, step.item.path) for root in roots
        ):
            roots.append(step)

    indexed_files = dict(observed.files)
    effects = []
    for step in roots:
        relative = step.item.relative
        if step.removal == "file":
            before = indexed_files.get(relative)
            if before is None or before.path != step.item.path:
                raise ValueError("清理文件不对应首次正式文件观察")
        elif step.removal == "empty_directory":
            before = _subtree(observed, relative)
            if any(
                contains(before.path, observed.path / position)
                for position, _, _ in cleanup.retained_records
            ):
                raise ValueError("完整清理子树不得包含保留结果")
        else:
            raise ValueError("未知清理名称语义")
        effects.append(FixedEffect(before, absent_state()))
    return tuple(effects)
