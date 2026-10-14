# -*- coding: utf-8 -*-

from dataclasses import dataclass
from pathlib import Path

from core.constants import (
    FILE_SECTION_START_TEMPLATE,
    MESSAGE_FILE_EXCLUDED,
    MESSAGE_CANNOT_READ,
    MESSAGE_READ_ERROR,
)
from core.file_walk import iter_project_entries
from services.demand_rules import windows_path_key
from core.atomic_file import atomic_text_writer
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


def build_regular_merge_plan(source_folder, exclusions, output_file=None):
    """目录剪枝；排除文件保留段落，但不读取正文。"""
    plan = []
    for file_path, is_directory, excluded in iter_project_entries(
        source_folder, exclusions, output_file,
    ):
        if is_directory:
            continue
        plan.append(
            make_merge_plan_item(
                file_path,
                merge_content=not excluded,
                skip_message=MESSAGE_FILE_EXCLUDED if excluded else "",
            )
        )
    return sorted(plan, key=lambda item: item["path"].lower())


def build_demand_merge_plan(file_paths):
    """消费最终选中路径，不重新解释链接或改变选择顺序。"""
    return [
        {"path": str(path), "merge_content": True, "skip_message": ""}
        for path in file_paths
    ]


def write_merge_file(
    file_plan,
    output_file,
    preamble_text,
    ending_text,
    code_header_line,
    code_footer_line,
    log_func=None,
    strict_read=False,
):
    """
    统一写合并文件：
    按目录合并和按名单合并共用这一条写入链路。
    """
    output_file = str(Path(output_file).resolve())

    if strict_read:
        output_key = windows_path_key(output_file)
        if any(
            item["merge_content"] and windows_path_key(item["path"]) == output_key
            for item in file_plan
        ):
            raise ValueError("输出文件被实际选中，不能执行按名单合并。")

    file_counter = 0

    with atomic_text_writer(output_file) as f_out:
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
                        strict=strict_read,
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
    exclusions,
    preamble_text,
    ending_text,
    code_header_line,
    code_footer_line,
    log_func=None,
):
    if log_func:
        log_func("开始按目录合并")
        log_func(f"源文件夹：{source_folder}")
        log_func(f"输出文件：{output_file}")

    file_plan = build_regular_merge_plan(
        source_folder=source_folder,
        exclusions=exclusions,
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
        log_func(f"按目录合并完成：共 {total_files} 个文件")

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
        log_func("开始按名单合并")
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
        strict_read=True,
    )

    if log_func:
        log_func(f"按名单合并完成：共 {total_files} 个文件")

    return MergeResult(
        output_file=str(Path(output_file).resolve()),
        total_files=total_files,
    )



