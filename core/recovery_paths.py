# -*- coding: utf-8 -*-

"""修改与恢复的原路径链、普通实体及目录树安全检查。"""

import os
import re
import stat
from pathlib import Path

from core.path_validation import normalize_rel_path


class UnsafePathError(ValueError):
    """明确安全阻塞；checked_chain补充到达阻塞位置的实际观察。"""

    def __init__(self, message, *, kind="unsafe"):
        super().__init__(message)
        self.kind = kind
        self.observation = ()


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
        raise UnsafePathError(
            f"不支持的 Windows 名称或路径别名：{name!r}",
            kind="invalid_name",
        )


def entity_type(path):
    try:
        attributes = Path(path).lstat()
    except FileNotFoundError:
        return "absent"
    return entity_type_from_attributes(path, attributes)


def entity_type_from_attributes(path, attributes):
    """消费不跟随的已观察属性，共用唯一普通对象政策。"""
    if stat.S_ISLNK(attributes.st_mode):
        raise UnsafePathError(
            f"不支持符号链接：{path}", kind="symbolic_link",
        )
    if getattr(attributes, "st_file_attributes", 0) & _REPARSE_POINT:
        raise UnsafePathError(
            f"不支持 Windows 重解析点：{path}", kind="reparse_point",
        )
    if stat.S_ISREG(attributes.st_mode):
        return "file"
    if stat.S_ISDIR(attributes.st_mode):
        return "directory"
    raise UnsafePathError(
        f"不支持特殊文件系统对象：{path}",
        kind=f"special_entity_{stat.S_IFMT(attributes.st_mode)}",
    )


def checked_chain(path, *, include_observation=False):
    # abspath 在 Windows 上可能消除尾随句点、空格及上级跳转。
    # 必须先检查原始名称与原路径链，不能用转换后的目标冒充原路径。
    original = Path(path)
    if os.name == "nt" and original.drive and not original.root:
        raise UnsafePathError(
            f"不支持依赖驱动器当前目录的路径：{path}",
            kind="drive_relative",
        )
    if not original.is_absolute():
        original = Path.cwd() / original

    observation = []
    for component in (*reversed(original.parents), original):
        # 失败观察止于实际阻塞位置，不跟随链接或访问受阻后代。
        # 安全模块拥有事实；消费者不解析诊断文字。
        try:
            if component.name and component.name not in (".", ".."):
                validate_component(component.name)
            kind = entity_type(component)
        except UnsafePathError as error:
            error.observation = tuple(observation) + (
                (component, error.kind),
            )
            raise
        observation.append((component, kind))
        if component != original and kind not in ("directory", "absent"):
            error = UnsafePathError(
                f"父路径不是普通目录：{component}",
                kind="parent_not_directory",
            )
            error.observation = tuple(observation)
            raise error
    target = absolute_path(original)
    return (target, tuple(observation)) if include_observation else target


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


