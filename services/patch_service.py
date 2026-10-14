# -*- coding: utf-8 -*-

from services.patch_executor import PatchExecutor
from services.patch_models import PatchPreviewResult, PatchApplyResult
from services.patch_parser import parse_patch_v3
from services.patch_restore import (
    apply_backup_restore_with_executor,
    preview_backup_restore_with_executor,
)


def preview_patch(
    output_rules, project_root, patch_text, allow_delete=False,
    allow_multi_replace_exact=False, backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
):
    if not patch_text.strip():
        raise ValueError("请先粘贴 Zipatch V3 修改包")
    patch = parse_patch_v3(patch_text)
    executor = PatchExecutor(
        output_rules, project_root, allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
        backup_enabled=backup_enabled, backup_dir=backup_dir,
        patch_text=patch_text,
    )
    report, valid_patch, errors, success, failed, global_errors = executor.preview(patch)
    return PatchPreviewResult(
        preview_text=report, patch=patch, valid_patch=valid_patch,
        has_errors=errors, success_count=success, failed_count=failed,
        execution=executor, has_global_errors=global_errors,
    )


def apply_patch(preview):
    if not isinstance(preview, PatchPreviewResult):
        raise ValueError("执行必须消费本次 Dry Run 预演结果")
    executor = preview.execution
    report = executor.apply()
    return PatchApplyResult(
        log_text=report,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
    )


def preview_backup_restore(
    project_root, restore_source_dir, backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
):
    return preview_backup_restore_with_executor(
        project_root, restore_source_dir, backup_enabled, backup_dir,
    )


def apply_backup_restore(preview, risk_confirmed=False):
    return apply_backup_restore_with_executor(preview, risk_confirmed)