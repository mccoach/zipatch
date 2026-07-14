# -*- coding: utf-8 -*-

from pathlib import Path

from services.patch_backup import resolve_backup_base_dir
from services.patch_executor import PatchExecutor
from services.patch_models import PatchPreviewResult, PatchApplyResult
from services.patch_parser import parse_patch_v2
from services.patch_restore import (
    apply_backup_restore_with_executor,
    preview_backup_restore_with_executor,
)


def preview_patch(
    project_root,
    patch_text,
    allow_delete=False,
    allow_multi_replace_exact=False,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
):
    root = Path(project_root)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"项目根目录不存在或不是目录：{project_root}")

    if not patch_text.strip():
        raise ValueError("请先粘贴 AI V2 修改包")

    patch = parse_patch_v2(patch_text)

    executor = PatchExecutor(
        root,
        allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        patch_text=patch_text,
    )

    preview_text, valid_patch, has_errors, success_count, failed_count, has_global_errors = executor.preview(patch)

    return PatchPreviewResult(
        preview_text=preview_text,
        patch=patch,
        valid_patch=valid_patch,
        has_errors=has_errors,
        success_count=success_count,
        failed_count=failed_count,
        has_global_errors=has_global_errors,
    )


def apply_patch(
    project_root,
    patch,
    allow_delete=False,
    allow_multi_replace_exact=False,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
    patch_text="",
    preview_text="",
):
    root = Path(project_root)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"项目根目录不存在或不是目录：{project_root}")

    if patch is None:
        raise ValueError("请先执行 Dry Run，并确保存在可执行内容")

    if not patch.get("operations"):
        raise ValueError("没有可执行的操作。请检查 Dry Run 结果。")

    executor = PatchExecutor(
        root,
        allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        patch_text=patch_text,
        preview_text=preview_text,
    )

    log_text = executor.apply(patch)

    return PatchApplyResult(
        log_text=log_text,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
    )


def preview_backup_restore(
    project_root,
    restore_source_dir,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
):
    return preview_backup_restore_with_executor(
        project_root=project_root,
        restore_source_dir=restore_source_dir,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
    )


def apply_backup_restore(
    project_root,
    restore_source_dir,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
    risk_confirmed=False,
    preview_text="",
):
    return apply_backup_restore_with_executor(
        project_root=project_root,
        restore_source_dir=restore_source_dir,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        risk_confirmed=risk_confirmed,
        preview_text=preview_text,
    )