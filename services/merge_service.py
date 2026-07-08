# -*- coding: utf-8 -*-

import os
from dataclasses import dataclass
from pathlib import Path

from core.constants import (
    FILE_SECTION_START_TEMPLATE,
    MESSAGE_SKIP_BY_EXT,
    MESSAGE_SKIP_BY_NAME,
    MESSAGE_CANNOT_READ,
    MESSAGE_READ_ERROR,
)
from core.file_walk import iter_all_files_with_skip_reason
from core.paths import ensure_parent_dir
from core.text_io import read_text_content_for_merge
from core.time_utils import current_timestamp_text


@dataclass
class MergeResult:
    output_file: str
    total_files: int


def make_merge_plan_item(file_path, merge_content=True, skip_message=""):
    return {
        "path": str(Path(file_path).resolve()),
        "merge_content": merge_content,
        "skip_message": skip_message,
    }


def build_regular_merge_plan(
    source_folder,
    exclude_folders,
    exclude_files,
    exclude_extensions,
    output_file=None,
):
    """
    常规合并计划：
    - 未排除文件：合并内容；
    - 命中文件名/扩展名排除：仍写文件段落，但内容写“内容略”。
    """
    source_folder = str(Path(source_folder).resolve())

    if not Path(source_folder).is_dir():
        raise ValueError(f"源文件夹不存在或不是文件夹：{source_folder}")

    plan = []

    for file_path, merge_content, skip_message in iter_all_files_with_skip_reason(
        source_folder=source_folder,
        exclude_folders=exclude_folders,
        exclude_files=exclude_files,
        exclude_extensions=exclude_extensions,
        output_file=output_file,
        skip_by_name_message=MESSAGE_SKIP_BY_NAME + "，内容略",
        skip_by_ext_message=MESSAGE_SKIP_BY_EXT + "，内容略",
    ):
        plan.append(
            make_merge_plan_item(
                file_path=file_path,
                merge_content=merge_content,
                skip_message=skip_message,
            )
        )

    return sorted(plan, key=lambda item: item["path"].lower())


def normalize_demand_file_path(raw_path):
    value = (raw_path or "").strip()

    if not value:
        return ""

    while len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()

    value = os.path.expandvars(os.path.expanduser(value))
    value = value.replace("\\", os.path.sep).replace("/", os.path.sep)
    value = os.path.normpath(value)

    return value


def validate_and_normalize_demand_file_list(file_list_text):
    """
    按需合并名单校验：
    - 自动去除引号；
    - 自动标准化路径；
    - 必须是绝对路径；
    - 必须存在；
    - 必须是文件；
    - 自动去重；
    - 返回错误行号用于 UI 高亮。
    """
    normalized_lines = []
    valid_paths = []
    invalid_line_numbers = []
    issue_counts = {}
    seen = set()
    duplicate_count = 0

    for raw_line in (file_list_text or "").splitlines():
        if not raw_line.strip():
            continue

        normalized_path = normalize_demand_file_path(raw_line)
        normalized_lines.append(normalized_path)
        line_number = len(normalized_lines)

        issue = ""

        if not os.path.isabs(normalized_path):
            issue = "非绝对路径"
        elif not os.path.exists(normalized_path):
            issue = "路径不存在"
        elif not os.path.isfile(normalized_path):
            issue = "不是文件"
        else:
            path_key = os.path.normcase(os.path.abspath(normalized_path))

            if path_key in seen:
                duplicate_count += 1
                continue

            seen.add(path_key)

            abs_path = os.path.abspath(normalized_path)
            valid_paths.append(abs_path)
            normalized_lines[-1] = abs_path
            continue

        invalid_line_numbers.append(line_number)
        issue_counts[issue] = issue_counts.get(issue, 0) + 1

    if duplicate_count:
        issue_counts["重复路径已自动去重"] = duplicate_count

    return {
        "normalized_text": "\n".join(normalized_lines),
        "valid_paths": valid_paths,
        "invalid_line_numbers": invalid_line_numbers,
        "issue_counts": issue_counts,
        "duplicate_count": duplicate_count,
    }


def build_demand_merge_plan(file_paths):
    return [
        make_merge_plan_item(file_path, merge_content=True)
        for file_path in file_paths
    ]


def write_merge_file(
    file_plan,
    output_file,
    preamble_text,
    ending_text,
    code_header_line,
    code_footer_line,
    log_func=None,
):
    """
    统一写合并文件：
    常规合并和按需合并共用这一条写入链路。
    """
    output_file = str(Path(output_file).resolve())

    ensure_parent_dir(output_file)

    file_counter = 0

    with open(output_file, "w", encoding="utf-8", errors="ignore") as f_out:
        f_out.write(f"{current_timestamp_text()}\n\n")

        if preamble_text:
            f_out.write(preamble_text.rstrip("\n") + "\n\n")

        for item in file_plan:
            file_counter += 1
            file_path = item["path"]

            if log_func and (file_counter <= 20 or file_counter % 50 == 0):
                log_func(f"正在合并第 {file_counter} 个文件：{file_path}")

            f_out.write(FILE_SECTION_START_TEMPLATE.format(index=file_counter) + "\n")
            f_out.write(f"{file_path}\n")
            f_out.write(code_header_line + "\n")

            if item["merge_content"]:
                f_out.write(
                    read_text_content_for_merge(
                        file_path,
                        read_error_message=MESSAGE_READ_ERROR,
                        cannot_read_message=MESSAGE_CANNOT_READ,
                    )
                )
            else:
                f_out.write(item["skip_message"])

            f_out.write("\n" + code_footer_line + "\n")

        if ending_text:
            f_out.write("\n" + ending_text.rstrip("\n") + "\n")

    return file_counter


def merge_project_files(
    source_folder,
    output_file,
    exclude_folders,
    exclude_files,
    exclude_extensions,
    preamble_text,
    ending_text,
    code_header_line,
    code_footer_line,
    log_func=None,
):
    if log_func:
        log_func("开始常规文件代码合并")
        log_func(f"源文件夹：{source_folder}")
        log_func(f"输出文件：{output_file}")

    file_plan = build_regular_merge_plan(
        source_folder=source_folder,
        exclude_folders=exclude_folders,
        exclude_files=exclude_files,
        exclude_extensions=exclude_extensions,
        output_file=output_file,
    )

    total_files = write_merge_file(
        file_plan=file_plan,
        output_file=output_file,
        preamble_text=preamble_text,
        ending_text=ending_text,
        code_header_line=code_header_line,
        code_footer_line=code_footer_line,
        log_func=log_func,
    )

    if log_func:
        log_func(f"常规文件代码合并完成：共 {total_files} 个文件")

    return MergeResult(
        output_file=str(Path(output_file).resolve()),
        total_files=total_files,
    )


def merge_demand_files(
    file_paths,
    output_file,
    preamble_text,
    ending_text,
    code_header_line,
    code_footer_line,
    log_func=None,
):
    if log_func:
        log_func("开始按需文件代码合并")
        log_func(f"输出文件：{output_file}")

    file_plan = build_demand_merge_plan(file_paths)

    total_files = write_merge_file(
        file_plan=file_plan,
        output_file=output_file,
        preamble_text=preamble_text,
        ending_text=ending_text,
        code_header_line=code_header_line,
        code_footer_line=code_footer_line,
        log_func=log_func,
    )

    if log_func:
        log_func(f"按需文件代码合并完成：共 {total_files} 个文件")

    return MergeResult(
        output_file=str(Path(output_file).resolve()),
        total_files=total_files,
    )


def summarize_demand_file_list_issues(issue_counts):
    if not issue_counts:
        return ""

    return "；".join(
        f"{name} {count} 行"
        for name, count in issue_counts.items()
    )
