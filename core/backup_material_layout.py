# -*- coding: utf-8 -*-

"""固定保护对象到直观材料位置的纯投影，不查询或改变文件系统。"""

import os
from pathlib import Path
from types import MappingProxyType

from core.protection_projection import (
    ProtectionProjection, validate_absolute_path, validate_relative_position,
)
from core.recovery_paths import contains, identity


def build_backup_material_paths(projection, *, base=None):
    """任务开始前一次固定布局；不存在对象也参与基准计算。

    明确业务根优先。保护对象包含业务根自身或其祖先时，
    基准上移至所有对象和业务根的共同父目录，保留对象原名。
    无业务根时按盘符或UNC共享根分组，分别取对象父目录的共同路径。
    返回值按对象编号关联；编号不成为材料文件名。
    """
    if not isinstance(projection, ProtectionProjection):
        raise TypeError("备份布局必须消费正式ProtectionProjection")
    if base is not None:
        if not isinstance(base, Path) or not base.is_absolute():
            raise ValueError("备份业务基准必须是明确绝对Path")
        validate_absolute_path(base)

    objects = projection.objects
    if not objects:
        return MappingProxyType({})

    groups = {}
    if base is not None:
        if any(
            not contains(base, member.target)
            and not contains(member.target, base)
            for member in objects
        ):
            raise ValueError("明确业务基准不能覆盖全部保护对象")
        candidates = [str(base)]
        candidates.extend(
            str(member.target.parent)
            for member in objects
            if contains(member.target, base)
        )
        root = Path(os.path.commonpath(candidates))
        groups[""] = (root, objects)
    else:
        by_volume = {}
        for member in objects:
            anchor = member.target.anchor
            if not anchor:
                raise ValueError("保护对象缺少明确文件系统根")
            by_volume.setdefault(identity(Path(anchor)), []).append(member)

        for members in by_volume.values():
            first = members[0].target
            root = Path(os.path.commonpath([
                str(member.target.parent) for member in members
            ]))
            drive = first.drive
            if len(drive) == 2 and drive[1] == ":":
                prefix = drive[0].upper()
            elif drive.startswith("\\\\"):
                # UNC名称已经过正式路径检查；共享根保持可辨认的层级。
                parts = drive.lstrip("\\").split("\\")
                if len(parts) != 2 or not all(parts):
                    raise ValueError("备份UNC分组缺少服务器及共享名")
                prefix = "UNC/" + "/".join(parts)
            else:
                prefix = "ROOT"
            groups[prefix] = (root, tuple(members))

    result = {}
    positions = []
    for prefix, (root, members) in groups.items():
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
