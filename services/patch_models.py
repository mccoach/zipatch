# -*- coding: utf-8 -*-

from dataclasses import dataclass


@dataclass
class PatchPreviewResult:
    preview_text: str
    patch: dict
    valid_patch: dict
    has_errors: bool
    success_count: int
    failed_count: int
    execution: object
    has_global_errors: bool = False


@dataclass
class PatchApplyResult:
    log_text: str
    backup_root: str


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