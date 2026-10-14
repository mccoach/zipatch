# -*- coding: utf-8 -*-

"""修改与恢复的原路径链、普通实体及目录树安全检查。"""

import os
import re
import stat
from pathlib import Path

from core.path_validation import normalize_rel_path


_REPARSE_POINT = 0x400
_WINDOWS_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')
_WINDOWS_DEVICE = re.compile(
    r"(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])",
    re.IGNORECASE,
)


def absolute_path(path):
    return Path(os.path.abspath(os.fspath(path)))


def identity(path):
    return os.path.normcase(os.path.normpath(str(absolute_path(path))))


def contains(parent, child):
    parent_key, child_key = identity(parent), identity(child)
    try:
        return os.path.commonpath((parent_key, child_key)) == parent_key
    except ValueError:
        return False


def overlaps(left, right):
    return contains(left, right) or contains(right, left)


def validate_component(name):
    if os.name == "nt" and (
        not name
        or name.endswith((" ", "."))
        or _WINDOWS_INVALID.search(name)
        or _WINDOWS_DEVICE.fullmatch(name.split(".", 1)[0].rstrip(" "))
    ):
        raise ValueError(f"不支持的 Windows 名称或路径别名：{name!r}")


def entity_type(path):
    try:
        attributes = Path(path).lstat()
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(attributes.st_mode):
        raise ValueError(f"不支持符号链接：{path}")
    if getattr(attributes, "st_file_attributes", 0) & _REPARSE_POINT:
        raise ValueError(f"不支持 Windows 重解析点：{path}")
    if stat.S_ISREG(attributes.st_mode):
        return "file"
    if stat.S_ISDIR(attributes.st_mode):
        return "directory"
    raise ValueError(f"不支持特殊文件系统对象：{path}")


def checked_chain(path):
    # abspath 在 Windows 上可能消除尾随句点、空格及上级跳转。
    # 必须先检查原始名称与原路径链，不能用转换后的目标冒充原路径。
    original = Path(path)
    if os.name == "nt" and original.drive and not original.root:
        raise ValueError(f"不支持依赖驱动器当前目录的路径：{path}")
    if not original.is_absolute():
        original = Path.cwd() / original

    for component in (*reversed(original.parents), original):
        # .. 本身是导航段，但到达它之前经过的目录仍须逐层检查。
        if component.name and component.name not in (".", ".."):
            validate_component(component.name)
        kind = entity_type(component)
        if component != original and kind not in ("directory", "absent"):
            raise ValueError(f"父路径不是普通目录：{component}")
    return absolute_path(original)


def checked_root(path):
    root = checked_chain(path)
    if entity_type(root) != "directory":
        raise ValueError(f"项目根目录不存在或不是普通目录：{root}")
    return root


def project_path(root, relative):
    relative = normalize_rel_path(relative)
    if any(ord(character) < 32 for character in relative):
        raise ValueError("项目相对路径包含控制字符")
    root = checked_chain(root)
    target = checked_chain(root / relative)
    if not contains(root, target) or identity(root) == identity(target):
        raise ValueError(f"路径越界或作用于项目根目录：{relative}")
    return target


def relative_path(root, target):
    return absolute_path(target).relative_to(absolute_path(root)).as_posix()


def tree_inventory(path):
    """单次目录枚举，拒绝链接和特殊对象；结果供完整性计算消费。"""
    root = checked_chain(path)
    kind = entity_type(root)
    if kind != "directory":
        return kind, ()
    records = [("", root, "directory")]
    pending = [root]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                validate_component(entry.name)
                child = Path(entry.path)
                child_kind = entity_type(child)
                if child_kind == "absent":
                    raise OSError(f"检查期间目录项消失：{child}")
                records.append((
                    child.relative_to(root).as_posix(), child, child_kind,
                ))
                if child_kind == "directory":
                    pending.append(child)
    records.sort(key=lambda record: record[0].encode("utf-8"))
    return kind, tuple(records)