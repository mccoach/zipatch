# -*- coding: utf-8 -*-

import os

from core.exclusion_rules import PreparedExclusions
from core.recovery_paths import checked_chain, entity_type, identity


def _raise_walk_error(error):
    raise error


def iter_project_entries(source_folder, exclusions, output_file=None):
    """单次剪枝遍历；使用对象前拒绝链接和特殊实体。"""
    if not isinstance(exclusions, PreparedExclusions):
        raise TypeError("文件遍历必须消费 PreparedExclusions")

    source, observation = checked_chain(
        source_folder, include_observation=True,
    )
    if observation[-1][1] != "directory":
        raise ValueError(f"源文件夹不存在或不是普通文件夹：{source}")
    output = identity(output_file) if output_file is not None else None

    for root, directories, filenames in os.walk(
        source, topdown=True, followlinks=False, onerror=_raise_walk_error,
    ):
        folder, observation = checked_chain(root, include_observation=True)
        if observation[-1][1] != "directory":
            raise ValueError(f"遍历目录不再是普通目录：{folder}")
        relative_parts = folder.relative_to(source).parts
        kept_directories = []
        for name in directories:
            if exclusions.directories.matches(relative_parts + (name,)):
                # 排除目录不进入，不枚举或检查其后代。
                continue
            child = folder / name
            if entity_type(child) != "directory":
                raise ValueError(f"遍历目录项不再是普通目录：{child}")
            kept_directories.append(name)
            yield str(child), True, False
        directories[:] = kept_directories

        for name in filenames:
            child = folder / name
            if output is not None and identity(child) == output:
                continue
            # 合并即使排除正文，也可能使用该路径和修改时间。
            # 因此排除文件不能被解释成支持链接或特殊对象。
            if entity_type(child) != "file":
                raise ValueError(f"遍历文件项不再是普通文件：{child}")
            yield (
                str(child), False,
                exclusions.files.matches(relative_parts + (name,)),
            )


def sorted_paths(paths):
    return sorted(paths, key=lambda path: str(path).lower())