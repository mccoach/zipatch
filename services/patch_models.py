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
    has_global_errors: bool = False


@dataclass
class PatchApplyResult:
    log_text: str
    backup_root: str


@dataclass
class PatchOpCheckResult:
    index: int
    op_type: str
    path: str
    ok: bool
    message: str
    locator: str
    old_first_line: str = ""


@dataclass
class BackupRestorePreviewResult:
    preview_text: str
    manifest: dict
    has_mismatch: bool
    risk_summary: dict


@dataclass
class BackupRestoreApplyResult:
    log_text: str
    backup_root: str