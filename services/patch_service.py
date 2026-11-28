# -*- coding: utf-8 -*-

from core.recovery_protection import read_recovery_evidence
from services.patch_executor import PatchExecutor
from services.patch_models import (
    PatchPreviewResult, PatchApplyResult,
    BackupRestorePreviewResult, BackupRestoreApplyResult,
)
from services.patch_parser import parse_patch_v3
from services.patch_restore import BackupRestoreExecutor


def preview_patch(
    output_rules, project_root, patch_text, allow_delete=False,
    allow_multi_replace_exact=False,
    backup_dir="99_归档/AI文件修改备份",
    normalize_text_newlines=True,
    authorize_patch_scope=None,
):
    if not patch_text.strip():
        raise ValueError("请先粘贴 Zipatch V3 修改包")
    patch = parse_patch_v3(patch_text)
    executor = PatchExecutor(
        output_rules, project_root, allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
        backup_dir=backup_dir,
        patch_text=patch_text,
        normalize_text_newlines=normalize_text_newlines,
        authorize_patch_scope=authorize_patch_scope,
    )
    report, checked_patch, errors, success, failed, global_errors, checks = executor.preview(patch)
    return PatchPreviewResult(
        preview_text=report, patch=checked_patch,
        has_errors=errors, success_count=success, failed_count=failed,
        execution=executor, check_results=checks,
        has_global_errors=global_errors,
    )


def invalidate_preview(preview):
    if not isinstance(preview, (PatchPreviewResult, BackupRestorePreviewResult)):
        raise ValueError("作废必须消费正式预演结果")
    return preview.execution.invalidate()


def apply_patch(preview):
    if not isinstance(preview, PatchPreviewResult):
        raise ValueError("执行必须消费正式修改包预演")
    executor = preview.execution
    report = executor.apply()
    return PatchApplyResult(
        log_text=report,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
        unit_results=tuple(executor.unit_results.values()),
    )


def preview_backup_restore(
    restore_source_dir, backup_dir="", *,
    backup_project_root="", authorize_restore_scope=None,
    select_restore_scope=None,
):
    executor = BackupRestoreExecutor(
        read_recovery_evidence(restore_source_dir),
        backup_dir=backup_dir,
        backup_project_root=backup_project_root,
        authorize_restore_scope=authorize_restore_scope,
        select_restore_scope=select_restore_scope,
    )
    return BackupRestorePreviewResult(
        preview_text=executor.preview_text,
        risk_summary=executor.risk_summary,
        execution=executor,
    )


def confirm_backup_restore(preview):
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("确认必须消费正式恢复预演")
    preview.execution.confirm_execution()


def apply_backup_restore(preview):
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("恢复必须消费正式恢复预演")
    executor = preview.execution
    report = executor.apply()
    return BackupRestoreApplyResult(
        log_text=report,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
        complete_original_state=executor.execution_result.complete_original_state,
        rebuilt_files=executor.execution_result.rebuilt_files,
    )
