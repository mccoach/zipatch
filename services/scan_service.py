# -*- coding: utf-8 -*-

"""扫描授权实体的名义清单、附加实际路径与唯一成果发布。"""

import os
from dataclasses import dataclass
from datetime import datetime

from core.file_observation import observe_file
from core.time_utils import current_timestamp_text
from core.text_newlines import normalize_newlines
from services.scan_scope import prepare_scan_scope, recheck_scan_scope
from services.stream_output import (
    StreamOutputPostError, StreamOutputResult, publish_stream_output,
)


@dataclass(frozen=True)
class ScanResult:
    output_result: StreamOutputResult
    file_count: int
    dir_count: int
    link_count: int = 0
    file_symlink_count: int = 0
    directory_symlink_count: int = 0
    junction_count: int = 0
    hardlink_count: int = 0

    @property
    def output_file(self):
        return str(self.output_result.target)


def panoramic_scan(
    source_folder, output_file, exclusions, preamble_text, ending_text,
    include_size=False, include_date=False, log_func=None, *,
    select_source_links=None, authorize_scan_scope=None, show_actual_path=True,
):
    for name, value in (
        ("显示文件大小", include_size), ("显示修改时间", include_date),
        ("显示实际路径", show_actual_path),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name}必须是布尔值")
    expected = observe_file(output_file, include_digest=True)
    if log_func:
        log_func(f"开始全景扫描：{source_folder}")
    scope = prepare_scan_scope(
        source_folder, exclusions, expected,
        select_source_links=select_source_links, authorize=authorize_scan_scope,
        include_date=include_date,
    )
    counts = {
        kind: sum(definition.kind == kind for definition in scope.links)
        for kind in ("file_symlink", "directory_symlink", "junction")
    }
    records = sorted(
        zip(scope.file_accesses, scope.files),
        key=lambda member: (
            str(member[0].nominal_path).casefold(),
            str(member[0].nominal_path),
        ),
    )

    def text_chunks():
        yield current_timestamp_text() + "\n\n"
        if preamble_text:
            yield preamble_text.rstrip("\n") + "\n\n"
        yield (
            f"统计：文件夹总数: {scope.dir_count}，文件总数: {scope.file_count}\n"
            f"已授权链接入口：{len(scope.links) + scope.hardlink_count}；"
            f"文件硬链接：{scope.hardlink_count}；"
            f"文件软链接：{counts['file_symlink']}；"
            f"文件夹软链接：{counts['directory_symlink']}；"
            f"文件夹联接（Junction）：{counts['junction']}\n"
            "目录元信息先完整预扫描，再集中选择来源链接；扫描不读取文件正文。\n\n"
        )
        for number, (access, observed) in enumerate(records, 1):
            line = f"{number}. {access.nominal_path}"
            if show_actual_path and access.has_links:
                line += f"\n   实际路径：{access.actual_path}"
            if include_size:
                line += f" (大小: {observed.attributes.st_size} bytes)"
            if include_date:
                date = datetime.fromtimestamp(
                    observed.attributes.st_mtime_ns // 1_000_000_000,
                ).strftime("%Y-%m-%d %H:%M:%S")
                line += f" (修改时间: {date})"
            yield line + "\n"
        # 空目录也完整表达；不再因目录是链接而隐藏其内部结构。
        yield "\n【文件夹结构】\n"
        metadata = dict(scope.directory_metadata)
        if scope.root_access is not None and scope.root_access.has_links:
            access = scope.root_access
            line = f"名义扫描根：{access.nominal_path}"
            if show_actual_path:
                line += f"\n实际路径：{access.actual_path}"
            observed = metadata.get(access.nominal_path)
            if observed is not None:
                date = datetime.fromtimestamp(
                    observed.attributes.st_mtime_ns // 1_000_000_000,
                ).strftime("%Y-%m-%d %H:%M:%S")
                line += f"\n文件夹自身修改时间：{date}"
            yield line + "\n"
        for access in sorted(
            scope.directories[1:],
            key=lambda member: str(member.nominal_path).casefold(),
        ):
            line = str(access.nominal_path)
            if show_actual_path and access.has_links:
                line += f"\n   实际路径：{access.actual_path}"
            observed = metadata.get(access.nominal_path)
            if observed is not None:
                date = datetime.fromtimestamp(
                    observed.attributes.st_mtime_ns // 1_000_000_000,
                ).strftime("%Y-%m-%d %H:%M:%S")
                line += f" (文件夹自身修改时间: {date})"
            yield line + "\n"
        yield "\n【主动忽略／未授权断点】\n"
        from services.merge_service import format_merge_omissions
        yield format_merge_omissions(scope.ignored, ())
        if ending_text:
            yield "\n" + ending_text.rstrip("\n") + "\n"

    output = publish_stream_output(
        expected,
        (
            normalize_newlines(chunk, os.linesep).encode("utf-8")
            for chunk in text_chunks()
        ),
        recheck_context=lambda: recheck_scan_scope(scope),
    )
    result = ScanResult(
        output, scope.file_count, scope.dir_count,
        len(scope.links) + scope.hardlink_count,
        counts["file_symlink"], counts["directory_symlink"], counts["junction"],
        scope.hardlink_count,
    )
    if log_func:
        try:
            log_func(
                f"全景扫描完成：文件夹 {result.dir_count} 个，"
                f"文件 {result.file_count} 个，授权链接节点 {result.link_count} 个"
            )
        except (OSError, UnicodeError) as error:
            raise StreamOutputPostError(
                f"扫描成果已经发布：{result.output_file}；完成日志失败。\n{error}",
                result,
            ) from error
    return result