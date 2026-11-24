# -*- coding: utf-8 -*-

"""文件选择入口的单条目录偏好；不授予业务访问或改变权限。"""

from pathlib import Path

from core.paths import get_app_dir


def file_dialog_initial_directory(commits, key, *, fallback=None):
    """只使用本入口记录；失效时不借用其他按钮或系统共享历史。"""
    if type(key) is not str or not key:
        raise ValueError("文件选择历史必须具有稳定非空标识")
    directories = commits.manager.config_data["file_dialog_directories"]
    previous = directories.get(key, "")
    if previous:
        path = Path(previous)
        try:
            valid = path.is_absolute() and path.is_dir()
        except OSError:
            # 目录偏好不是业务前提；不可访问记录不阻断重新选择。
            valid = False
        if valid:
            return str(path)
    return str(get_app_dir()) if fallback is None else str(fallback)


def remember_file_dialog_directory(commits, key, directory):
    """成功选择或导入后接受一条有效目录；保存归所属UI操作收口。"""
    if type(key) is not str or not key:
        raise ValueError("文件选择历史必须具有稳定非空标识")
    path = Path(directory)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"选择位置已失效，不更新历史目录：{directory}")
    return commits.manager.accept(
        commits.manager.config_data["file_dialog_directories"],
        {key: str(path)},
    )