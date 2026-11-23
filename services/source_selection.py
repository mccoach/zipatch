# -*- coding: utf-8 -*-

"""固定来源的共同授权边界、决定投影及未选报告纯投影。

不读取磁盘、不授予权限、不重新选择来源。
目录边界来自原实际访问经过的最外层目录链接，
同一输出项使用的全部别名共同决定。
"""

from core.recovery_paths import identity
from core.work_selection import group_related_indices, project_group_decisions


def source_boundary_positions(accesses):
    """保留全部不同名义授权边界；不将目录后代展开成文件清单。"""
    positions = {}
    for access in accesses:
        if not access.has_links:
            continue
        directory = next((
            definition.entry_path for definition in access.followed_links
            if definition.kind in ("directory_symlink", "junction")
        ), None)
        if directory is None and access.kind in (
            "directory_symlink", "junction",
        ):
            directory = access.nominal_path
        position = directory if directory is not None else access.actual_path
        kind = "directory" if directory is not None else "file"
        positions.setdefault((kind, identity(position)), (position, kind))
    return tuple(positions.values())


def source_choice_groups(routes):
    """来源只负责提供链接边界键；关联归组使用共同纯算法。"""
    boundaries = tuple(
        source_boundary_positions(tuple(accesses))
        for accesses in routes
    )
    groups = group_related_indices(tuple(
        tuple((kind, identity(position)) for position, kind in members)
        for members in boundaries
    ))
    return tuple(
        (boundaries[numbers[0]][0][0], numbers)
        for numbers in groups if boundaries[numbers[0]]
    )


def project_source_choices(groups, selected, count):
    """来源展示组消费共同决定投影，不另写选择算法。"""
    return project_group_decisions(
        tuple(numbers for position, numbers in groups),
        selected, count, description="来源目录共同选择",
    )


def source_target_key(access):
    return (
        ("file", access.object_identity)
        if access.kind == "file"
        else ("entry", identity(access.actual_path))
    )


def unselected_merge_records(prepared, accesses, retained):
    """只折叠全部关联输出均未选的目录范围。

    自定义服务选择器仍可逐项决定；目录内有保留项时不能误报整目录
    被取消。多个名义别名分别保留其目录边界，不因正文去重而隐藏。
    """
    prepared = tuple(prepared)
    retained = set(retained)
    owners = {
        source_target_key(access): number
        for number, (_, access) in enumerate(prepared)
    }
    directory_members = {}
    directory_paths = {}
    for access in accesses:
        number = owners[source_target_key(access)]
        for path, kind in source_boundary_positions((access,)):
            if kind != "directory":
                continue
            key = identity(path)
            directory_paths.setdefault(key, path)
            directory_members.setdefault(key, set()).add(number)

    collapsed = {
        key for key, numbers in directory_members.items()
        if numbers and not numbers.intersection(retained)
    }
    records = [
        (str(directory_paths[key]), "directory", "本次目录来源未授权")
        for key in directory_paths if key in collapsed
    ]
    for number, (entry, access) in enumerate(prepared):
        if number in retained:
            continue
        # 报告对应实际输出的首次名义位置；其全部相关目录范围另外列出。
        boundaries = source_boundary_positions((access,))
        if any(
            kind == "directory" and identity(path) in collapsed
            for path, kind in boundaries
        ):
            continue
        records.append((
            entry["path"],
            "directory" if access.kind in (
                "directory", "directory_symlink", "junction",
            ) else "file",
            "本次来源未授权",
        ))
    return tuple(records)