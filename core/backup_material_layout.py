# -*- coding: utf-8 -*-

"""固定保护对象及明确根基准到材料位置的唯一纯投影。"""

import os
from pathlib import Path
from types import MappingProxyType

from core.protection_projection import (
    ProtectionProjection, validate_absolute_path, validate_relative_position,
)
from core.recovery_paths import identity


def _volume_prefix(path):
    drive = path.drive
    if len(drive) == 2 and drive[1] == ":":
        return drive[0].upper()
    if drive.startswith("\\\\"):
        parts = drive.lstrip("\\").split("\\")
        if len(parts) != 2 or not all(parts):
            raise ValueError("备份UNC分组缺少服务器及共享名")
        return "UNC/" + "/".join(parts)
    return "ROOT"


def build_backup_material_paths(projection, *, base=None):
    """一次固定布局，不查询文件系统，不按业务类别选择算法。

    不存在对象同样参与位置计算，但材料持久化由保护协调决定。
    单卷且具有同卷根基准时，以根和所有对象父目录的共同路径
    为基准，不添加卷层；根自身、祖先及同卷根外对象自然上移。
    无适用根或跨卷时，各卷分别取共同父目录并添加卷层。
    """
    if not isinstance(projection, ProtectionProjection):
        raise TypeError("备份布局必须消费正式ProtectionProjection")
    if base is not None:
        if not isinstance(base, Path) or not base.is_absolute():
            raise ValueError("备份根基准必须是明确绝对Path")
        validate_absolute_path(base)

    objects = projection.objects
    if not objects:
        return MappingProxyType({})

    by_volume = {}
    for member in objects:
        anchor = member.target.anchor
        if not anchor:
            raise ValueError("保护对象缺少明确文件系统根")
        by_volume.setdefault(identity(Path(anchor)), []).append(member)

    base_volume = None if base is None else identity(Path(base.anchor))
    groups = []
    for volume, members in by_volume.items():
        use_base = len(by_volume) == 1 and volume == base_volume
        candidates = [str(member.target.parent) for member in members]
        if use_base:
            candidates.append(str(base))
        root = Path(os.path.commonpath(candidates))
        prefix = "" if use_base else _volume_prefix(members[0].target)
        groups.append((prefix, root, tuple(members)))

    result = {}
    positions = []
    for prefix, root, members in groups:
        for member in members:
            relative = member.target.relative_to(root).as_posix()
            if relative in ("", "."):
                raise ValueError("备份材料必须保留保护对象自身的名称")
            material = prefix + "/" + relative if prefix else relative
            validate_relative_position(material)
            key = material.casefold()
            if any(
                key == previous
                or key.startswith(previous + "/")
                or previous.startswith(key + "/")
                for previous in positions
            ):
                raise ValueError("备份材料位置重复或父子重叠")
            positions.append(key)
            result[member.object_id] = material
    return MappingProxyType(result)
