# -*- coding: utf-8 -*-

import os
from pathlib import Path

from core.exclusion_rules import PreparedExclusions


def _raise_walk_error(error):
    raise error


def iter_project_entries(source_folder, exclusions, output_file=None):
    """一次遍历消费已编译规则；命中目录实际剪枝，不再进入。"""
    if not isinstance(exclusions, PreparedExclusions):
        raise TypeError("文件遍历必须消费 PreparedExclusions")

    source = Path(source_folder).resolve()
    if not source.is_dir():
        raise ValueError(f"源文件夹不存在或不是文件夹：{source}")
    output = (
        os.path.normcase(str(Path(output_file).resolve()))
        if output_file is not None else None
    )

    for root, directories, filenames in os.walk(
        source, topdown=True, onerror=_raise_walk_error,
    ):
        folder = Path(root)
        relative_parts = folder.relative_to(source).parts
        kept_directories = []
        for name in directories:
            if exclusions.directories.matches(relative_parts + (name,)):
                continue
            kept_directories.append(name)
            yield str(folder / name), True, False
        directories[:] = kept_directories

        for name in filenames:
            absolute = str(folder / name)
            if output is not None and os.path.normcase(str(Path(absolute).resolve())) == output:
                continue
            yield (
                absolute, False,
                exclusions.files.matches(relative_parts + (name,)),
            )


def sorted_paths(paths):
    return sorted(paths, key=lambda path: str(path).lower())