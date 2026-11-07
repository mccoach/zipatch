# -*- coding: utf-8 -*-

from dataclasses import dataclass


@dataclass
class PatchPreviewResult:
    preview_text: str
    patch: dict

    has_errors: bool
    success_count: int
    failed_count: int
    execution: object
    check_results: tuple
    has_global_errors: bool = False


@dataclass
class PatchApplyResult:
    log_text: str
    backup_root: str
    unit_results: tuple


@dataclass
class PatchOpCheckResult:
    op_id: str
    op_type: str
    path: str
    ok: bool
    message: str
    locator: str


@dataclass
class BackupRestorePreviewResult:
    preview_text: str
    risk_summary: dict
    execution: object


@dataclass
class BackupRestoreApplyResult:
    log_text: str
    backup_root: str
    complete_original_state: bool