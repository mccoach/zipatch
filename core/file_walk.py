# -*- coding: utf-8 -*-

import os
from pathlib import Path


def iter_project_files(
    source_folder,
    exclude_folders=None,
    exclude_files=None,
    exclude_extensions=None,
    output_file=None,
):
    """
    统一文件遍历器。

    用于：
    - 全景扫描；
    - 常规代码合并。

    规则：
    - 支持排除文件夹；
    - 支持排除文件名；
    - 支持排除扩展名；
    - 自动跳过输出文件本身；
    - 返回绝对路径字符串。
    """
    source_folder = Path(source_folder)
    exclude_folders = set(exclude_folders or [])
    exclude_files = set(exclude_files or [])
    exclude_extensions = set(exclude_extensions or [])
    output_abs = str(Path(output_file).resolve()) if output_file else None

    for root, dirs, files in os.walk(source_folder, topdown=True):
        if exclude_folders:
            dirs[:] = [d for d in dirs if d not in exclude_folders]

        for filename in files:
            if filename in exclude_files:
                continue

            file_abs = str((Path(root) / filename).resolve())

            if output_abs and file_abs == output_abs:
                continue

            ext = Path(filename).suffix

            if ext in exclude_extensions:
                continue

            yield file_abs


def iter_all_files_with_skip_reason(
    source_folder,
    exclude_folders=None,
    exclude_files=None,
    exclude_extensions=None,
    output_file=None,
    skip_by_name_message="",
    skip_by_ext_message="",
):
    """
    合并功能专用遍历器。

    与 iter_project_files 不同：
    这里不会直接丢弃被排除的文件，而是返回“跳过原因”，
    这样合并输出中仍然可以保留文件段落和“内容略”提示。
    """
    source_folder = Path(source_folder)
    exclude_folders = set(exclude_folders or [])
    exclude_files = set(exclude_files or [])
    exclude_extensions = set(exclude_extensions or [])
    output_abs = str(Path(output_file).resolve()) if output_file else None

    for root, dirs, files in os.walk(source_folder, topdown=True):
        if exclude_folders:
            dirs[:] = [d for d in dirs if d not in exclude_folders]

        for filename in files:
            file_abs = str((Path(root) / filename).resolve())

            if output_abs and file_abs == output_abs:
                continue

            ext = Path(filename).suffix

            if filename in exclude_files:
                yield file_abs, False, skip_by_name_message
                continue

            if ext in exclude_extensions:
                yield file_abs, False, skip_by_ext_message
                continue

            yield file_abs, True, ""


def count_dirs(source_folder, exclude_folders=None):
    source_folder = Path(source_folder)
    exclude_folders = set(exclude_folders or [])

    total = 0

    for _, dirs, _ in os.walk(source_folder, topdown=True):
        if exclude_folders:
            dirs[:] = [d for d in dirs if d not in exclude_folders]

        total += len(dirs)

    return total


def sorted_paths(paths):
    return sorted(paths, key=lambda p: str(p).lower())
