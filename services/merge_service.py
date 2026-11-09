# -*- coding: utf-8 -*-

"""两个合并入口共用严格读取、双占位、容器构建及唯一发布。"""

import errno
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event

from core.constants import (
    FILE_SECTION_START_TEMPLATE, MESSAGE_FILE_EXCLUDED, MESSAGE_FILE_FAILED,
    MERGE_MODES,
)
from core.file_observation import FileObservation, observe_file
from core.file_walk import iter_project_entries
from core.merged_format import (
    MTIME_FORMAT, MTIME_PREFIX, MTIME_UNAVAILABLE, STRUCTURAL_NEWLINE,
    validate_body_end_marker, validate_markers, validate_placeholders,
)
from core.rule_lists import validate_file_rules
from core.recovery_paths import (
    UnsafePathError, checked_chain, contains, entity_type_from_attributes,
    validate_component,
)
from core.text_io import TextDecodingError, decode_text_bytes
from core.text_newlines import normalize_newlines
from core.time_utils import current_timestamp_text
from services.demand_rules import (
    build_demand_selection, demand_physical_lines,
    inspect_demand_rules, windows_path_key,
)
from services.stream_output import (
    StreamOutputPostError, StreamOutputResult, publish_stream_output,
)


@dataclass(frozen=True)
class MergeResult:
    output_result: StreamOutputResult
    total_files: int
    excluded_count: int = 0
    read_failure_count: int = 0
    mtime_failures: tuple = ()

    @property
    def output_file(self):
        return str(self.output_result.target)


def read_text_content_for_merge(file_path):
    """合并拥有读取协调；有限失败仍由生成流程决定被动占位。"""
    observed = observe_file(file_path, include_bytes=True)
    if observed.kind != "file":
        raise FileNotFoundError(f"合并来源文件已经不存在：{observed.path}")
    content, _ = decode_text_bytes(
        observed.source_bytes,
        source_description="合并来源文件", path=observed.path,
    )
    return content


def build_regular_merge_plan(source_folder, exclusions, output_file=None):
    """单次目录剪枝；排除文件保留块，但不读取正文。"""
    plan = []
    for file_path, is_directory, excluded in iter_project_entries(
        source_folder, exclusions, output_file,
    ):
        if not is_directory:
            plan.append({
                "path": str(file_path),
                "merge_content": not excluded,
            })
    return sorted(plan, key=lambda entry: entry["path"].lower())


def build_demand_merge_plan(file_paths):
    """消费最终清单，不重新选取、排序或改写原路径表达。"""
    return [{"path": str(path), "merge_content": True} for path in file_paths]


def read_source_mtime(file_path):
    """正文决定之后独立观察时间；保持缺失及资源故障政策。"""
    try:
        path = Path(file_path)
        checked_chain(path.parent)
        if path.name and path.name not in (".", ".."):
            validate_component(path.name)
        attributes = os.stat(file_path, follow_symlinks=False)
        entity_type_from_attributes(path, attributes)
    except UnsafePathError:
        raise
    except OSError as error:
        if error.errno in (errno.ENOMEM, errno.EMFILE, errno.ENFILE, errno.ENOSPC):
            raise
        return None, str(error)

    try:
        observed = datetime.fromtimestamp(
            attributes.st_mtime_ns // 1_000_000_000,
        )
    except (OverflowError, OSError, ValueError) as error:
        return None, f"修改时间无法表示为本地时间：{error}"
    return observed.strftime(MTIME_FORMAT), ""


def _extra_text_chunks(text, *, before=False, ending=False):
    """普通附加文本保留布局政策，不套用精确源码正文契约。"""
    if not text:
        return
    complete = normalize_newlines(text, "\n").rstrip("\n")
    if before:
        yield STRUCTURAL_NEWLINE
    yield normalize_newlines(complete, STRUCTURAL_NEWLINE)
    yield STRUCTURAL_NEWLINE if ending else STRUCTURAL_NEWLINE * 2


def write_merge_file(
    file_plan, expected_output, preamble_text, ending_text,
    code_header_line, code_footer_line, log_func=None, *,
    normalize_text_newlines=False, record_file_mtime=True,
    ignore_text=MESSAGE_FILE_EXCLUDED, failure_text=MESSAGE_FILE_FAILED,
    output_rules=None, match_root=None,
):
    """消费已形成来源清单及固定输出，完整生成后才发布。"""
    if not isinstance(expected_output, FileObservation):
        raise TypeError("合并容器必须消费固定输出FileObservation")
    validate_markers(code_header_line, code_footer_line)
    validate_placeholders(ignore_text, failure_text)
    for name, value in (
        ("正文标准化", normalize_text_newlines),
        ("记录文件修改时间", record_file_mtime),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name}开关必须是布尔值")
    if normalize_text_newlines:
        validate_file_rules(output_rules)

    file_plan = tuple(file_plan)
    output_key = windows_path_key(expected_output.path)
    if any(windows_path_key(entry["path"]) == output_key for entry in file_plan):
        raise ValueError("输出文件被实际选中，不能执行按名单合并。")
    root = checked_chain(match_root) if match_root is not None else None
    total = excluded_count = read_failure_count = 0
    mtime_failures = []

    def text_chunks():
        nonlocal total, excluded_count, read_failure_count
        yield current_timestamp_text() + STRUCTURAL_NEWLINE * 2
        yield from _extra_text_chunks(preamble_text)

        for total, entry in enumerate(file_plan, 1):
            source_path = entry["path"]
            # 展示位置不是读取授权。原链安全由正式读取入口检查；
            # 不在有限正文失败处理之前另查路径而改变占位政策。
            display_path = os.path.abspath(source_path)
            if any(ord(character) < 32 for character in display_path):
                raise ValueError(f"合并来源路径包含控制字符：{display_path!r}")
            reasons = []
            if not entry["merge_content"]:
                # 不读取排除正文，但实际使用的原路径仍须安全。
                checked_chain(source_path)
                body = ignore_text
                excluded_count += 1
                reasons.append("主动忽略：文件命中排除名单，未读取真实正文")
            else:
                try:
                    body = read_text_content_for_merge(source_path)
                except (
                    FileNotFoundError, PermissionError,
                    IsADirectoryError, NotADirectoryError, TextDecodingError,
                ) as error:
                    body = failure_text
                    read_failure_count += 1
                    reasons.append(
                        f"被动失败：正文读取或解码失败，使用被动标识：{error}"
                    )

            if normalize_text_newlines:
                # 目录范围内保持源目录根语义；补充的外部文件使用路径尾部匹配。
                # 仅列表没有目录根，全部文件继续使用列表的尾部匹配语义。
                within_root = root is not None and contains(root, display_path)
                parts = (
                    Path(display_path).relative_to(root).parts
                    if within_root else Path(display_path).parts[1:]
                )
                matched = output_rules.matches(
                    parts, ignore_root=not within_root,
                )
                body = normalize_newlines(body, "\r\n" if matched else "\n")

            validate_body_end_marker(body, code_footer_line, display_path)
            modified_at = None
            if record_file_mtime:
                modified_at, failure = read_source_mtime(source_path)
                if failure:
                    mtime_failures.append((display_path, failure))
                    reasons.append(f"修改时间获取失败：{failure}")

            if log_func:
                if total <= 20 or total % 50 == 0:
                    log_func(f"正在合并第 {total} 个文件：{display_path}")
                for reason in reasons:
                    log_func(f"合并来源：{display_path}；{reason}")

            yield FILE_SECTION_START_TEMPLATE.format(index=total) + STRUCTURAL_NEWLINE
            yield display_path + STRUCTURAL_NEWLINE
            if record_file_mtime:
                yield (
                    MTIME_PREFIX
                    + (modified_at if modified_at is not None else MTIME_UNAVAILABLE)
                    + STRUCTURAL_NEWLINE
                )
            yield code_header_line + STRUCTURAL_NEWLINE
            yield body
            # 真实尾换行不抵扣独立协议分隔。
            yield STRUCTURAL_NEWLINE + code_footer_line + STRUCTURAL_NEWLINE

        yield from _extra_text_chunks(ending_text, before=True, ending=True)

    output = publish_stream_output(
        expected_output, (chunk.encode("utf-8") for chunk in text_chunks()),
    )
    return MergeResult(
        output, total, excluded_count, read_failure_count,
        tuple(mtime_failures),
    )


def _log_completed(result, mode, log_func):
    if log_func is None:
        return
    try:
        log_func(f"{mode}完成：共 {result.total_files} 个文件")
        log_func(
            f"合并成果已发布；主动忽略 {result.excluded_count} 个，"
            f"被动失败 {result.read_failure_count} 个，"
            f"修改时间未取得 {len(result.mtime_failures)} 个。"
        )
    except (OSError, UnicodeError) as error:
        raise StreamOutputPostError(
            f"{mode}成果已经提交：{result.output_file}；"
            "完成日志失败，不代表成果未发布，没有自动撤销。\n"
            f"主动忽略 {result.excluded_count} 个；"
            f"被动失败 {result.read_failure_count} 个；"
            f"修改时间未取得 {len(result.mtime_failures)} 个。\n"
            + "".join(
                f"- {path}：{reason}\n"
                for path, reason in result.mtime_failures
            )
            + str(error),
            result,
        ) from error


def merge_files(
    mode, source_folder, list_text, output_file, exclusions,
    preamble_text, ending_text, code_header_line, code_footer_line,
    log_func=None, *, normalize_text_newlines=False, record_file_mtime=True,
    ignore_text=MESSAGE_FILE_EXCLUDED, failure_text=MESSAGE_FILE_FAILED,
    output_rules=None,
):
    """唯一合并入口：按模式形成一次有序清单，然后生成和发布一次。

    目录排除只作用于目录选择。列表是明确包含：
    与目录已有路径重复时保留目录顺序，但提供真实正文；
    其余列表文件追加在目录文件之后。去重不读取文件正文。
    编辑窗口的检查结果不作为执行资格，本次重新检查所需列表一次。
    """
    if type(mode) is not str or mode not in MERGE_MODES:
        raise ValueError("合并模式必须是 directory、list 或 combined")
    if mode in ("directory", "combined") and not source_folder:
        # 不允许空路径经checked_chain隐式变为当前工作目录。
        raise ValueError("源文件夹不能为空；本次没有选择目录或发布成果")

    selected_files = ()
    if mode in ("list", "combined"):
        jobs = tuple(enumerate(demand_physical_lines(list_text), 1))
        matches = inspect_demand_rules(jobs, source_folder, Event())
        ordered_matches = tuple(matches[number] for number, _ in jobs)
        selection = build_demand_selection(ordered_matches)
        if not selection.complete or selection.has_errors:
            reasons = [
                f"合并列表第 {number} 行：{match.issue}"
                for number, match in zip(
                    (number for number, _ in jobs), ordered_matches,
                )
                if match.issue
            ]
            raise ValueError(
                "合并列表检查未通过，本次未发布成果。\n"
                + "\n".join(reasons)
            )
        selected_files = selection.files
        if mode == "list" and not selected_files:
            raise ValueError("仅列表模式至少需要一条实际选中文件的启用规则")

    expected = observe_file(output_file, include_digest=True)
    output_key = windows_path_key(expected.path)
    if any(windows_path_key(path) == output_key for path in selected_files):
        raise ValueError("合并列表选中了本次输出文件，不能合并")

    if log_func:
        log_func(f"开始合并：{mode}")
        if mode in ("directory", "combined"):
            log_func(f"源文件夹：{source_folder}")
        log_func(f"输出文件：{expected.path}")

    plan = (
        build_regular_merge_plan(source_folder, exclusions, expected.path)
        if mode in ("directory", "combined") else []
    )
    positions = {
        windows_path_key(entry["path"]): index
        for index, entry in enumerate(plan)
    }
    for entry in build_demand_merge_plan(selected_files):
        key = windows_path_key(entry["path"])
        previous = positions.get(key)
        if previous is not None:
            # 明确包含优先于目录文件排除，不创建重复块或重复正文读取。
            plan[previous]["merge_content"] = True
        else:
            positions[key] = len(plan)
            plan.append(entry)

    result = write_merge_file(
        plan, expected, preamble_text, ending_text,
        code_header_line, code_footer_line, log_func,
        normalize_text_newlines=normalize_text_newlines,
        record_file_mtime=record_file_mtime,
        ignore_text=ignore_text, failure_text=failure_text,
        output_rules=output_rules,
        match_root=source_folder if mode in ("directory", "combined") else None,
    )
    _log_completed(result, "合并", log_func)
    return result
