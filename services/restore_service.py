# -*- coding: utf-8 -*-

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.constants import (
    MESSAGE_SKIP_BY_EXT,
    MESSAGE_CANNOT_READ,
    MESSAGE_READ_ERROR,
    SKIPPED_LOG_FILENAME,
)
from core.paths import ensure_parent_dir, get_available_renamed_path
from core.text_io import read_text_with_fallback_encodings


@dataclass
class RestoreResult:
    target_folder: str
    restored_count: int
    skipped_count: int
    log_path: Optional[str]


def parse_merged_blocks(full_text, start_marker, end_marker):
    """
    兼容原来的三种解析策略：
    1. 开始标记 + 结束标记；
    2. 仅开始标记；
    3. 仅结束标记。
    """
    if start_marker and start_marker in full_text and end_marker and end_marker in full_text:
        pattern = re.compile(
            r"---第\d+个文件---\s*(.*?)\s*"
            + re.escape(start_marker)
            + r"\s*(.*?)\s*"
            + re.escape(end_marker),
            re.DOTALL,
        )
        matches = pattern.findall(full_text)

        if matches:
            return matches, "开始和结束标记"

    if start_marker and start_marker in full_text:
        pattern = re.compile(
            r"---第\d+个文件---\s*(.*?)\s*"
            + re.escape(start_marker)
            + r"\s*(.*?)(?=---第\d+个文件---|$)",
            re.DOTALL,
        )
        matches = pattern.findall(full_text)

        if matches:
            return matches, "仅开始标记"

    if end_marker and end_marker in full_text:
        pattern = re.compile(
            r"---第\d+个文件---\s*(.*?)\s*(.*?)"
            + re.escape(end_marker),
            re.DOTALL,
        )
        matches = pattern.findall(full_text)

        if matches:
            return matches, "仅结束标记"

    return [], "无"


def resolve_restore_path(original_path_str, target_root_folder):
    """
    将合并文件中记录的原始路径映射到目标根目录下。

    规则：
    - 去除原始盘符或根路径前缀；
    - 禁止路径片段包含 ..；
    - 最终结果必须仍位于 target_root_folder 内部。
    """
    _, path_tail = os.path.splitdrive(original_path_str)
    path_tail = path_tail.replace("\\", os.path.sep).replace("/", os.path.sep)
    path_tail = path_tail.lstrip(os.path.sep)

    parts = [
        part
        for part in Path(path_tail).parts
        if part not in ("", os.path.sep)
    ]

    if any(part == ".." for part in parts):
        raise ValueError(f"还原路径不允许包含 '..'：{original_path_str}")

    target_root = Path(target_root_folder).resolve()
    target_path = (target_root / Path(*parts)).resolve()

    try:
        target_path.relative_to(target_root)
    except ValueError:
        raise ValueError(f"还原路径逃逸目标文件夹：{target_path}")

    return str(target_path)


def should_skip_restored_content(content):
    return (
        MESSAGE_SKIP_BY_EXT in content
        or MESSAGE_CANNOT_READ in content
        or MESSAGE_READ_ERROR in content
    )


def write_skipped_log(target_root_folder, source_txt_file, skipped_files_log):
    if not skipped_files_log:
        return None

    target_root_folder = str(Path(target_root_folder).resolve())

    if not os.path.exists(target_root_folder):
        os.makedirs(target_root_folder)

    log_path = os.path.join(target_root_folder, SKIPPED_LOG_FILENAME)

    with open(log_path, "w", encoding="utf-8") as f_log:
        f_log.write(f"在 {source_txt_file} 的还原过程中，以下文件因内容被忽略、已存在或还原失败而被跳过：\n")
        f_log.write("-" * 60 + "\n")

        for path in skipped_files_log:
            f_log.write(path + "\n")

    return log_path


def split_and_restore(
    source_txt_file,
    target_root_folder,
    code_header_line,
    code_footer_line,
    existing_file_policy,
    log_func=None,
):
    source_txt_file = str(Path(source_txt_file).resolve())
    target_root_folder = str(Path(target_root_folder).resolve())

    if not Path(source_txt_file).is_file():
        raise ValueError(f"源 txt 文件不存在：{source_txt_file}")

    if not target_root_folder:
        raise ValueError("目标文件夹不能为空。")

    if log_func:
        log_func("开始代码拆分还原")
        log_func(f"源 txt 文件：{source_txt_file}")
        log_func(f"目标文件夹：{target_root_folder}")

    full_text, used_encoding = read_text_with_fallback_encodings(source_txt_file)

    if log_func:
        log_func(f"源文件读取成功，编码：{used_encoding}")

    matches, parsing_strategy = parse_merged_blocks(
        full_text,
        code_header_line,
        code_footer_line,
    )

    if not matches:
        raise ValueError("未找到有效代码块，请检查“代码开始标记”和“代码结束标记”。")

    if log_func:
        log_func(f"解析成功，策略：{parsing_strategy}，文件块数量：{len(matches)}")

    restored_count = 0
    skipped_files_log = []

    for i, (original_path_str, content) in enumerate(matches, start=1):
        original_path_str = original_path_str.strip()
        content = content.rstrip()

        if not original_path_str:
            skipped_files_log.append(f"[第{i}个文件] 未找到有效路径")
            continue

        if log_func and (i <= 20 or i % 50 == 0):
            log_func(f"正在还原第 {i} 个文件：{original_path_str}")

        if should_skip_restored_content(content):
            skipped_files_log.append(original_path_str)
            continue

        restored_path = resolve_restore_path(
            original_path_str,
            target_root_folder,
        )

        if os.path.exists(restored_path):
            if existing_file_policy == "skip":
                skipped_files_log.append(f"{original_path_str} (目标已存在，已跳过)")
                continue

            if existing_file_policy == "rename":
                restored_path = get_available_renamed_path(restored_path)

        try:
            ensure_parent_dir(restored_path)

            with open(restored_path, "w", encoding="utf-8") as f_out:
                f_out.write(content)

            restored_count += 1

        except Exception as e:
            skipped_files_log.append(f"{original_path_str} (还原失败: {e})")

    log_path = write_skipped_log(
        target_root_folder,
        source_txt_file,
        skipped_files_log,
    )

    if log_func:
        log_func(f"代码拆分还原完成：成功 {restored_count} 个，跳过 {len(skipped_files_log)} 个")

        if log_path:
            log_func(f"跳过日志：{log_path}")

    return RestoreResult(
        target_folder=target_root_folder,
        restored_count=restored_count,
        skipped_count=len(skipped_files_log),
        log_path=log_path,
    )
