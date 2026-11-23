# -*- coding: utf-8 -*-

"""同一只读来源复核边界的共同目录、链接和末级身份比较。

消费原固定路径事实，不重新枚举、接受新指向或授予读取资格。
每次调用拥有独立检查集合，不跨交互或任务缓存成功结论。
正文版本仍由正式文件摘要观察负责。
"""

from core.link_paths import (
    LinkPathObservation, _attributes_or_absent, _confirm_directory,
    _confirm_link, _entry_kind, _object_identity,
)
from core.recovery_paths import identity


def recheck_source_paths(accesses):
    """共同依赖检查一次；随后核对全部原实际入口，不重新解析原定义。

    原定义读取后的revision已经固定。复核使用同一正式元信息比较，
    不重读定义；父目录身份、全部经过的链接及实际末级共同约束原路线。
    任一不一致停止，不探查变化后定义指向的新位置。
    """
    directories = {}
    definitions = {}
    targets = {}
    missing = {}
    for access in accesses:
        if not isinstance(access, LinkPathObservation):
            raise TypeError("来源批量复核必须消费正式路径观察")
        for directory in access.directories:
            key = identity(directory.path)
            previous = directories.setdefault(key, directory)
            if previous != directory:
                raise ValueError("共同来源父目录具有矛盾的固定身份")
        for definition in (
            *access.followed_links,
            *((access.entry_link,) if access.entry_link is not None else ()),
        ):
            key = identity(definition.entry_path)
            previous = definitions.setdefault(key, definition)
            if previous != definition:
                raise ValueError("共同来源链接具有矛盾的固定原定义")
        if access.first_missing is not None:
            missing.setdefault(identity(access.first_missing), access.first_missing)
        else:
            key = identity(access.actual_path)
            expected = (access.actual_path, access.kind, access.object_identity)
            previous = targets.setdefault(key, expected)
            if previous != expected:
                raise ValueError("共同来源末级具有矛盾的固定身份或类别")

    # 先检查普通目录和原链接，再使用固定实际位置。
    # 不调用解析器追踪后来出现的新指向。
    for directory in sorted(
        directories.values(), key=lambda member: len(member.path.parts),
    ):
        _confirm_directory(directory)
    for definition in definitions.values():
        _confirm_link(definition)
    for path in missing.values():
        if _attributes_or_absent(path) is not None:
            raise ValueError(f"固定来源缺失位置已经出现：{path}")
    for key, (path, kind, identifier) in targets.items():
        if kind == "directory" and key in directories:
            continue
        if key in definitions:
            continue
        attributes = _attributes_or_absent(path)
        if (
            attributes is None
            or _entry_kind(path, attributes) != kind
            or _object_identity(attributes, path) != identifier
        ):
            raise ValueError(f"固定来源实际入口存在性、类别或身份变化：{path}")