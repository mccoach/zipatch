# -*- coding: utf-8 -*-

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.atomic_file import atomic_text_writer
from core.rule_lists import validate_file_rules
from core.text_newlines import normalize_newlines
from core.constants import (
    MESSAGE_FILE_EXCLUDED,
    MESSAGE_CANNOT_READ,
    MESSAGE_READ_ERROR,
    SKIPPED_LOG_FILENAME,
)
from core.paths import get_available_renamed_path
from core.text_io import read_text_with_fallback_encodings


@dataclass
class RestoreResult:
    target_folder: str
    restored_count: int
    skipped_count: int
    log_path: Optional[str]
    failed_count: int


def parse_merged_blocks(full_text, start_marker, end_marker):
    """
    解析标准合并文件块。

    唯一支持格式：
    ---第N个文件---
    文件路径
    开始标记
    文件内容
    结束标记
    """
    if not start_marker or not end_marker:
        return [], "标准开始和结束标记"

    pattern = re.compile(
        r"---第\d+个文件---\s*(.*?)\s*"
        + re.escape(start_marker)
        + r"\s*(.*?)\s*"
        + re.escape(end_marker),
        re.DOTALL,
    )

    return pattern.findall(full_text), "标准开始和结束标记"


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
        MESSAGE_FILE_EXCLUDED in content
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

    with atomic_text_writer(log_path) as f_log:
        f_log.write(f"在 {source_txt_file} 的还原过程中，以下文件因内容被忽略、已存在或还原失败而被跳过：\n")
        f_log.write("-" * 60 + "\n")

        for path in skipped_files_log:
            f_log.write(path + "\n")

    return log_path


def split_and_restore(
    output_rules,
    source_txt_file,
    target_root_folder,
    code_header_line,
    code_footer_line,
    existing_file_policy,
    log_func=None,
):
    validate_file_rules(output_rules)
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
    failed_count = 0
    skipped_files_log = []

    processing_stage = "文件处理"
    try:
        for i, (original_path_str, content) in enumerate(matches, start=1):
            original_path_str = original_path_str.strip()
            content = content.rstrip()

            if not original_path_str:
                skipped_files_log.append(f"[第{i}个文件] 未找到有效路径")
                continue

            processing_stage = "还原进度记录"
            if log_func and (i <= 20 or i % 50 == 0):
                log_func(f"正在还原第 {i} 个文件：{original_path_str}")

            if should_skip_restored_content(content):
                skipped_files_log.append(original_path_str)
                continue

            processing_stage = "目标路径解析及已有目标处理"
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

            relative_parts = Path(restored_path).relative_to(Path(target_root_folder)).parts
            newline = "\r\n" if output_rules.matches(relative_parts) else "\n"
            complete = normalize_newlines(content, newline)
            processing_stage = "目标文件提交"
            try:
                with atomic_text_writer(restored_path, newline="") as f_out:
                    f_out.write(complete)
            except (OSError, UnicodeEncodeError) as error:
                # 延续逐文件 I/O、编码失败继续后续文件的既有业务规则。
                failed_count += 1
                failure = f"{original_path_str} (还原失败: {error})"
                skipped_files_log.append(failure)
                processing_stage = "文件提交失败记录"
                if log_func:
                    log_func(f"文件未提交：{failure}")
            else:
                restored_count += 1
    except (OSError, ValueError) as error:
        # 路径或目标拒绝仍然中断，不转换成正常跳过。
        # 只补充本批已经发生的提交事实，不执行自动回滚。
        message = (
            f"拆分还原在第 {i} 个文件的{processing_stage}阶段中断："
            f"{original_path_str}。\n"
            f"已有 {restored_count} 个还原文件提交，"
            f"{failed_count} 个文件还原失败；"
            f"已收集 {len(skipped_files_log)} 条跳过或失败信息。\n"
            "已提交文件没有自动撤销，跳过日志尚未提交。\n"
            f"{error}"
        )
        if isinstance(error, OSError):
            raise OSError(message) from error
        if isinstance(error, UnicodeError):
            raise UnicodeError(message) from error
        raise ValueError(message) from error

    try:
        log_path = write_skipped_log(
            target_root_folder,
            source_txt_file,
            skipped_files_log,
        )
    except (OSError, ValueError) as error:
        # ValueError 包括日志编码错误及原子能力的非普通目标拒绝。
        # 日志未提交不等于此前还原文件未提交。
        raise OSError(
            f"跳过日志未提交；已有 {restored_count} 个还原文件提交，"
            f"{failed_count} 个文件还原失败。已提交文件没有自动撤销。\n"
            f"{error}"
        ) from error

    if log_func:
        try:
            # 最终完成消息放在其他运行记录之后，
            # 避免跳过日志路径记录失败时已经宣称整个流程完成。
            if log_path:
                log_func(f"跳过日志：{log_path}")
            if failed_count:
                log_func(
                    f"代码拆分还原结束，存在失败：成功 {restored_count} 个，"
                    f"跳过 {len(skipped_files_log)} 个，其中还原失败 {failed_count} 个"
                )
            else:
                log_func(f"代码拆分还原完成：成功 {restored_count} 个，跳过 {len(skipped_files_log)} 个")
        except (OSError, UnicodeError) as error:
            raise OSError(
                f"还原文件提交阶段已结束，已提交 {restored_count} 个文件；"
                f"其中还原失败 {failed_count} 个。"
                f"跳过日志：{log_path or '本次没有需要生成的跳过日志'}。\n"
                "但最终运行记录失败，已提交文件没有自动撤销。\n"
                f"{error}"
            ) from error

    return RestoreResult(
        target_folder=target_root_folder,
        restored_count=restored_count,
        skipped_count=len(skipped_files_log),
        log_path=log_path,
        failed_count=failed_count,
    )
