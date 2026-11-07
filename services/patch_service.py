# -*- coding: utf-8 -*-

from services.patch_executor import PatchExecutor
from services.patch_models import (
    PatchPreviewResult, PatchApplyResult,
    BackupRestorePreviewResult, BackupRestoreApplyResult,
)
from services.patch_parser import parse_patch_v3
from services.patch_restore import BackupRestoreExecutor


def preview_patch(
    output_rules, project_root, patch_text, allow_delete=False,
    allow_multi_replace_exact=False, backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
    normalize_text_newlines=True,
):
    if not patch_text.strip():
        raise ValueError("请先粘贴 Zipatch V3 修改包")
    patch = parse_patch_v3(patch_text)
    executor = PatchExecutor(
        output_rules, project_root, allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
        backup_enabled=backup_enabled, backup_dir=backup_dir,
        patch_text=patch_text,
        normalize_text_newlines=normalize_text_newlines,
    )
    (
        report, checked_patch, errors, success, failed,
        global_errors, check_results,
    ) = executor.preview(patch)
    return PatchPreviewResult(
        preview_text=report, patch=checked_patch,
        has_errors=errors, success_count=success, failed_count=failed,
        execution=executor, check_results=check_results,
        has_global_errors=global_errors,
    )


def invalidate_preview(preview):
    """正式取消入口：检查预演类别，由该任务执行器永久撤销资格。"""
    if not isinstance(preview, (PatchPreviewResult, BackupRestorePreviewResult)):
        raise ValueError("作废必须消费本次修改或恢复预演结果")
    return preview.execution.invalidate()


def apply_patch(preview):
    if not isinstance(preview, PatchPreviewResult):
        raise ValueError("执行必须消费本次 Dry Run 预演结果")
    executor = preview.execution
    report = executor.apply()
    return PatchApplyResult(
        log_text=report,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
        unit_results=tuple(executor.unit_results.values()),
    )


def preview_backup_restore(
    project_root, restore_source_dir, backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
):
    executor = BackupRestoreExecutor(
        project_root, restore_source_dir, backup_enabled, backup_dir,
    )
    return BackupRestorePreviewResult(
        preview_text=executor.preview_text,
        risk_summary=executor.risk_summary,
        execution=executor,
    )


def choose_backup_restore(
    preview, identifiers, decision, *,
    scope="selected", replace_decisions=False,
):
    """正式对象决定入口，不执行恢复或发布保护材料。"""
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("对象决定必须消费本次恢复预演结果")
    summary = preview.execution.choose(
        identifiers, decision,
        scope=scope, replace_decisions=replace_decisions,
    )
    preview.risk_summary = summary
    preview.preview_text = preview.execution.preview_text
    return summary


def prepare_backup_restore(preview):
    """明确决定收口为唯一最终恢复及保护方案。"""
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("恢复方案必须消费本次预演结果")
    summary = preview.execution.prepare_execution()
    preview.risk_summary = summary
    preview.preview_text = preview.execution.preview_text
    return summary


def return_backup_restore_to_selection(preview):
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("返回检查必须消费本次恢复预演结果")
    summary = preview.execution.return_to_selection()
    preview.risk_summary = summary
    preview.preview_text = preview.execution.preview_text
    return summary


def confirm_backup_restore(preview, *, loss_acknowledged=False):
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("最终确认必须消费本次恢复预演结果")
    preview.execution.confirm_execution(loss_acknowledged=loss_acknowledged)


def apply_backup_restore(preview):
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("恢复必须消费本次预演结果")
    executor = preview.execution
    report = executor.apply()
    return BackupRestoreApplyResult(
        log_text=report,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
        complete_original_state=executor.execution_result.complete_original_state,
    )


def choose_backup_restore_subitems(
    preview, identifier, relatives, decision, *,
    selection="selected", replace_decisions=False,
    replace_object_decision=False,
):
    """本次正式子项决定入口，不执行删除或发布保护材料。"""
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("子项决定必须消费本次正式恢复预演结果")
    summary = preview.execution.choose_subitems(
        identifier, relatives, decision,
        selection=selection, replace_decisions=replace_decisions,
        replace_object_decision=replace_object_decision,
    )
    preview.risk_summary = summary
    preview.preview_text = preview.execution.preview_text
    return summary