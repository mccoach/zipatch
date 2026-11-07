# -*- coding: utf-8 -*-

"""路径扫描的选择、统计及流式正文；提交消费共享输出协调。"""

import os
from dataclasses import dataclass
from datetime import datetime

from core.file_observation import observe_file
from core.file_walk import iter_project_entries, sorted_paths
from core.time_utils import current_timestamp_text
from services.stream_output import (
    StreamOutputPostError, StreamOutputResult, publish_stream_output,
)


@dataclass(frozen=True)
class ScanResult:
    output_result: StreamOutputResult
    file_count: int
    dir_count: int

    @property
    def output_file(self):
        return str(self.output_result.target)


def panoramic_scan(
    source_folder,
    output_file,
    exclusions,
    preamble_text,
    ending_text,
    include_size=False,
    include_date=False,
    log_func=None,
):
    """固定输出 -> 单次剪枝及排序 -> 完整流式成果 -> 唯一发布。"""
    for name, value in (
        ("输出文件大小", include_size), ("输出文件日期", include_date),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name}开关必须是布尔值")
    expected = observe_file(output_file, include_digest=True)

    if log_func:
        log_func("开始全景扫描")
        log_func(f"源文件夹：{source_folder}")
        log_func(f"输出文件：{expected.path}")

    files = []
    dir_count = 0
    for path, is_directory, excluded in iter_project_entries(
        source_folder, exclusions, expected.path,
    ):
        if is_directory:
            dir_count += 1
        elif not excluded:
            files.append(path)
    files = sorted_paths(files)
    file_count = len(files)

    def text_chunks():
        yield current_timestamp_text() + "\n\n"
        if preamble_text:
            yield preamble_text.rstrip("\n") + "\n\n"
        yield f"统计：文件夹总数: {dir_count}，文件总数: {file_count}\n\n"
        for index, path in enumerate(files, 1):
            line = f"{index}. {path}"
            if include_size or include_date:
                observed = observe_file(path)
                if observed.kind != "file":
                    raise FileNotFoundError(f"扫描文件已经不存在：{path}")
                if include_size:
                    line += f" (大小: {observed.attributes.st_size} bytes)"
                if include_date:
                    date = datetime.fromtimestamp(
                        observed.attributes.st_mtime,
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    line += f" (日期: {date})"
            yield line + "\n"
        if ending_text:
            yield "\n" + ending_text.rstrip("\n") + "\n"

    # 保留扫描原平台文本换行政策；共享发布只消费已编码bytes。
    output = publish_stream_output(
        expected,
        (
            chunk.replace("\n", os.linesep).encode("utf-8")
            for chunk in text_chunks()
        ),
    )
    result = ScanResult(output, file_count, dir_count)
    if log_func:
        try:
            log_func(f"全景扫描完成：目录 {dir_count} 个，文件 {file_count} 个")
        except (OSError, UnicodeError) as error:
            raise StreamOutputPostError(
                f"全景扫描成果已经提交：{result.output_file}；"
                "完成日志失败，不代表成果未发布，没有自动撤销。\n"
                f"{error}",
                result,
            ) from error
    return result
