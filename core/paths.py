# -*- coding: utf-8 -*-

"""程序位置、资源查找及路径候选；原对象检查使用共享路径政策。"""

import sys
from pathlib import Path

from core.constants import CONFIG_FILENAME
from core.recovery_paths import checked_chain


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_config_path():
    return get_app_dir() / CONFIG_FILENAME


def is_frozen_app():
    """封装环境隐藏维护入口，不允许更新exe内置协议模块。"""
    return bool(getattr(sys, "frozen", False))


def get_asset_path(relative_path):
    """外置资源优先，其次打包资源；只在候选不存在时继续查找。

    交给Tk或Pillow之前检查原路径和普通文件类型。
    不支持对象、权限或路径故障直接暴露，不回退到另一份资源掩盖。
    全部不存在时返回外置候选，供调用方表达资源缺失。
    """
    relative = Path(relative_path)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("静态资源必须使用程序内的明确相对路径")

    external = get_app_dir() / relative
    candidates = [external]
    if is_frozen_app() and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / relative
        if bundled != external:
            candidates.append(bundled)

    external_target = None
    for candidate in candidates:
        target, chain = checked_chain(candidate, include_observation=True)
        if external_target is None:
            external_target = target
        kind = chain[-1][1]
        if kind == "file":
            return target
        if kind != "absent":
            raise IsADirectoryError(f"静态资源不是普通文件：{target}")
    return external_target


def get_initial_dir_from_path(path):
    """选择器初始位置保留既有展开政策，检查前不resolve隐藏链接。"""
    selected = (path or "").strip()
    if not selected:
        return None
    target, chain = checked_chain(
        Path(selected).expanduser(), include_observation=True,
    )
    if chain[-1][1] == "directory":
        return str(target)
    parent = target.parent
    checked_parent, parent_chain = checked_chain(
        parent, include_observation=True,
    )
    if parent_chain[-1][1] == "directory":
        return str(checked_parent)
    return None


def build_output_path(output_folder, output_filename):
    """交互和正式输出共用原链检查，不先resolve隐藏链接入口。"""
    return str(checked_chain(Path(output_folder) / output_filename))


def get_available_renamed_path(file_path):
    """成果自动改名保留；每个实际候选均检查原链及普通文件政策。"""
    target, chain = checked_chain(file_path, include_observation=True)
    if chain[-1][1] == "absent":
        return str(target)
    if chain[-1][1] != "file":
        raise IsADirectoryError(f"成果目标不是普通文件：{target}")

    index = 1
    while True:
        candidate = target.parent / f"{target.stem}_{index}{target.suffix}"
        checked, candidate_chain = checked_chain(
            candidate, include_observation=True,
        )
        kind = candidate_chain[-1][1]
        if kind == "absent":
            return str(checked)
        if kind != "file":
            raise IsADirectoryError(f"成果改名候选不是普通文件：{checked}")
        index += 1


def validate_required_path(path, label):
    if not path:
        raise ValueError(f"{label}不能为空。")
    return path
