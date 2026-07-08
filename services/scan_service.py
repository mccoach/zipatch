# -*- coding: utf-8 -*-

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.file_walk import iter_project_files, count_dirs, sorted_paths
from core.paths import ensure_parent_dir
from core.time_utils import current_timestamp_text


@dataclass
class ScanResult:
    output_file: str
    file_count: int
    dir_count: int


def panoramic_scan(
    source_folder,
    output_file,
    exclude_folders,
    exclude_files,
    exclude_extensions,
    preamble_text,
    ending_text,
    include_size=False,
    include_date=False,
    log_func=None,
):
    """
    全景扫描业务主链条：
    校验输入 -> 遍历文件 -> 统计 -> 写结果 -> 返回结果。
    """
    source_folder = str(Path(source_folder).resolve())
    output_file = str(Path(output_file).resolve())

    if not Path(source_folder).is_dir():
        raise ValueError(f"源文件夹不存在或不是文件夹：{source_folder}")

    if log_func:
        log_func("开始全景扫描")
        log_func(f"源文件夹：{source_folder}")
        log_func(f"输出文件：{output_file}")

    files = list(
        iter_project_files(
            source_folder=source_folder,
            exclude_folders=exclude_folders,
            exclude_files=exclude_files,
            exclude_extensions=exclude_extensions,
            output_file=output_file,
        )
    )

    files = sorted_paths(files)
    dir_count = count_dirs(source_folder, exclude_folders)
    file_count = len(files)

    ensure_parent_dir(output_file)

    with open(output_file, "w", encoding="utf-8", errors="ignore") as f_out:
        f_out.write(f"{current_timestamp_text()}\n\n")

        if preamble_text:
            f_out.write(preamble_text.rstrip("\n") + "\n\n")

        f_out.write(f"统计：文件夹总数: {dir_count}，文件总数: {file_count}\n\n")

        for idx, fp in enumerate(files, start=1):
            f_out.write(f"{idx}. {fp}")

            if include_size or include_date:
                file_stat = os.stat(fp)

                if include_size:
                    f_out.write(f" (大小: {file_stat.st_size} bytes)")

                if include_date:
                    f_out.write(
                        f" (日期: {datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')})"
                    )

            f_out.write("\n")

        if ending_text:
            f_out.write("\n" + ending_text.rstrip("\n") + "\n")

    if log_func:
        log_func(f"全景扫描完成：目录 {dir_count} 个，文件 {file_count} 个")

    return ScanResult(
        output_file=output_file,
        file_count=file_count,
        dir_count=dir_count,
    )
