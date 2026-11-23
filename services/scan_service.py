# -*- coding: utf-8 -*-

"""路径扫描的固定来源、链接叶子、统计和唯一流式成果发布。"""

import os
from dataclasses import dataclass
from datetime import datetime

from core.file_observation import observe_file
from core.time_utils import current_timestamp_text
from services.scan_scope import (
    prepare_scan_scope, recheck_scan_scope, scan_display_path,
)
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
    *,
    authorize_scan_scope=None, select_scan_scope=None,
    select_scan_enumeration=None,
):
    """元信息范围准备及授权 -> 固定内容生成 -> 来源复核 -> 唯一发布。

    普通文件正文和链接引用目标正文均不读取。
    普通目录继续不列为文件行；三类链接入口明确列出结构元信息。
    """
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
    scope = prepare_scan_scope(
        source_folder, exclusions, expected,
        authorize=authorize_scan_scope, select_scope=select_scan_scope,
        select_enumeration=select_scan_enumeration,
    )
    if scope is None:
        # 明确跳过唯一来源范围，不伪造“扫描过空目录”的观察。
        text = (
            current_timestamp_text()
            + "\n\n本次未选择任何实际目录枚举范围；扫描入口为0。"
            "\n未进入链接目标目录，未读取来源正文。\n"
        )
        output = publish_stream_output(
            expected, (text.replace("\n", os.linesep).encode("utf-8"),),
        )
        return ScanResult(output, 0, 0)
    from core.recovery_paths import identity
    from services.scan_metadata import (
        prepare_target_metadata, recheck_target_metadata,
    )

    metadata = (
        prepare_target_metadata(scope, expected, authorize=authorize_scan_scope)
        if scope.links and (include_size or include_date) else ()
    )
    target_metadata = {
        identity(member.entry.actual_path): member for member in metadata
    }
    kind_counts = {
        kind: sum(access.kind == kind for access in scope.links)
        for kind in ("file_symlink", "directory_symlink", "junction")
    }

    def recheck_sources():
        recheck_scan_scope(scope)
        recheck_target_metadata(metadata)

    records = sorted(
        (
            *((file.path, file) for file in scope.files),
            *((access.actual_path, access) for access in scope.links),
        ),
        key=lambda member: (str(member[0]).casefold(), str(member[0])),
    )

    def text_chunks():
        yield current_timestamp_text() + "\n\n"
        if preamble_text:
            yield preamble_text.rstrip("\n") + "\n\n"
        yield (
            f"统计：文件夹总数: {scope.dir_count}，"
            f"文件总数: {scope.file_count}\n"
            f"所选普通文件：{len(scope.files)}；"
            f"文件软链接：{kind_counts['file_symlink']}；"
            f"目录软链接：{kind_counts['directory_symlink']}；"
            f"Junction：{kind_counts['junction']}\n"
            "目录统计保留已枚举的普通目录上下文，文件统计只包含所选入口。\n"
        )
        if scope.links:
            yield (
                "链接说明：保存实际原定义；引用目标未展开，正文未读取。"
                "目录大小不递归累计；目标日期为目标自身修改时间。\n"
            )
        yield "\n"
        if scope.root_access.has_links:
            yield f"名义扫描根：{scope.root_access.nominal_path}\n"
            yield f"实际扫描根：{scope.root_access.actual_path}\n\n"
        for index, (path, observed) in enumerate(records, 1):
            nominal = scan_display_path(scope, path)
            line = f"{index}. {nominal}"
            definition = getattr(observed, "entry_link", None)
            if definition is not None:
                line += f" (类型: {observed.kind})"
                if nominal != path:
                    line += f"\n   实际入口：{path}"
                line += f"\n   实际原定义：{definition.raw_target!r}"
                line += f"\n   直接引用：{definition.direct_target}"
                member = target_metadata.get(identity(path))
                if member is not None:
                    line += (
                        f"\n   目标：{member.access.actual_path}"
                        f"；类型={member.access.kind}"
                    )
                    if member.access.kind == "absent":
                        line += "；大小／日期不可用：目标不存在"
                    else:
                        if include_size:
                            line += (
                                f"；大小={member.file.attributes.st_size} bytes"
                                if member.file is not None else
                                "；大小不累计：目标为目录"
                            )
                        if include_date:
                            modified = (
                                member.file.mtime_ns if member.file is not None
                                else member.directory_mtime_ns
                            )
                            date = datetime.fromtimestamp(
                                modified // 1_000_000_000,
                            ).strftime("%Y-%m-%d %H:%M:%S")
                            line += f"；日期={date}"
            else:
                if nominal != path:
                    line += f"\n   实际文件：{path}"
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

    output = publish_stream_output(
        expected,
        (
            chunk.replace("\n", os.linesep).encode("utf-8")
            for chunk in text_chunks()
        ),
        recheck_context=recheck_sources,
    )
    result = ScanResult(
        output, scope.file_count, scope.dir_count, len(scope.links),
        kind_counts["file_symlink"], kind_counts["directory_symlink"],
        kind_counts["junction"],
    )
    if log_func:
        try:
            log_func(
                f"全景扫描完成：目录 {result.dir_count} 个，"
                f"文件 {result.file_count} 个，链接入口 {result.link_count} 个"
            )
        except (OSError, UnicodeError) as error:
            raise StreamOutputPostError(
                f"全景扫描成果已经提交：{result.output_file}；"
                "完成日志失败，不代表成果未发布，没有自动撤销。\n"
                f"{error}",
                result,
            ) from error
    return result
