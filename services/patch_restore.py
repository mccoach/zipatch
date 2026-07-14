# -*- coding: utf-8 -*-

from pathlib import Path

from core.path_validation import safe_join, normalize_rel_path
from services.patch_backup import (
    copy_path_for_backup,
    current_state_matches_expected,
    describe_state_mismatch,
    load_manifest,
    make_backup_root,
    remove_empty_parent_dirs_until_root,
    remove_path,
    restore_path_from_backup,
    save_json,
)
from services.patch_models import BackupRestorePreviewResult, BackupRestoreApplyResult


def empty_restore_risk_summary():
    return {
        "delete_paths": [],
        "overwrite_paths": [],
        "restore_dirs": [],
        "mismatch_paths": [],
        "has_risk": False,
    }


def add_unique_risk_item(items, text):
    if text and text not in items:
        items.append(text)


def build_restore_risk_summary_from_plan(restore_operations):
    summary = empty_restore_risk_summary()

    for plan in restore_operations:
        if plan.get("mismatch"):
            add_unique_risk_item(
                summary["mismatch_paths"],
                f"{plan.get('path', '')}：{plan.get('issue', '')}",
            )

        for entry in plan.get("entries", []):
            rel_path = entry.get("path", "")
            before_state = entry.get("before_state", {})
            after_state = entry.get("after_state", {})

            before_exists = bool(before_state.get("exists", False))
            after_exists = bool(after_state.get("exists", False))

            if after_exists and not before_exists:
                add_unique_risk_item(summary["delete_paths"], rel_path)

            if before_exists and after_exists:
                add_unique_risk_item(summary["overwrite_paths"], rel_path)

            if before_exists and before_state.get("type") == "dir":
                add_unique_risk_item(summary["restore_dirs"], rel_path)

    summary["has_risk"] = any(
        summary[key]
        for key in (
            "delete_paths",
            "overwrite_paths",
            "restore_dirs",
            "mismatch_paths",
        )
    )

    return summary


class BackupRestoreExecutor:
    def __init__(
        self,
        root: Path,
        restore_source_dir,
        backup_enabled=True,
        backup_dir="99_归档/AI文件修改备份",
        preview_text="",
    ):
        self.root = Path(root).resolve()
        self.restore_source_dir = Path(restore_source_dir).resolve()
        self.backup_enabled = backup_enabled
        self.backup_root = (
            make_backup_root(self.root, backup_dir, "备份还原前")
            if backup_enabled
            else None
        )
        self.preview_text = preview_text
        self.logs = []
        self.backed_up = {}
        self.restore_operations = []
        self.manifest = None
        self.risk_summary = empty_restore_risk_summary()

    def log(self, msg):
        self.logs.append(msg)

    def backup_current_path(self, target: Path):
        """
        还原前备份当前路径状态。

        支持文件和目录。
        """
        if not self.backup_enabled:
            return None

        if not target.exists():
            return None

        target = target.resolve()

        if target in self.backed_up:
            return self.backed_up[target]

        rel = target.relative_to(self.root)
        backup_path = self.backup_root / "files" / rel
        copy_path_for_backup(target, backup_path)
        self.backed_up[target] = backup_path

        self.log(f"[还原前备份] {rel} -> {backup_path}")

        return backup_path

    def load_and_validate_manifest(self):
        self.manifest = load_manifest(self.restore_source_dir)

        if self.manifest.get("version") not in ("2.3", "2.4"):
            raise ValueError(
                f"当前仅支持 V2.3/V2.4 备份 manifest，还原来源版本为："
                f"{self.manifest.get('version')}"
            )

        if self.manifest.get("mode") != "forward":
            raise ValueError("当前仅支持由修改包执行产生的 forward manifest")

        operations = self.manifest.get("operations")

        if not isinstance(operations, list) or not operations:
            raise ValueError("manifest 中没有可还原的 operations")

        return operations

    def normalize_manifest_path(self, rel_path):
        return str(normalize_rel_path(rel_path)).replace("\\", "/")

    def backup_file_from_manifest(self, state):
        backup_file = state.get("backup_file", "")

        if not backup_file:
            return None

        candidate = (self.restore_source_dir / backup_file).resolve()

        try:
            candidate.relative_to(self.restore_source_dir)
        except ValueError:
            raise ValueError(f"manifest 备份文件路径逃逸备份目录：{backup_file}")

        if not candidate.exists():
            raise ValueError(f"manifest 记录的备份文件不存在：{backup_file}")

        return candidate

    def collect_restore_entry(self, op_item, state_key):
        """
        state_key:
        - path
        - new_path
        """
        before_state_all = op_item.get("before_state", {})
        after_state_all = op_item.get("after_state", {})

        before_state = before_state_all.get(state_key, {})
        after_state = after_state_all.get(state_key, {})

        if state_key == "path":
            rel_path = op_item.get("path", "")
        else:
            rel_path = op_item.get("new_path", "")

        rel_path = self.normalize_manifest_path(rel_path)
        target = safe_join(self.root, rel_path)

        backup_source = self.backup_file_from_manifest(before_state)

        return {
            "path": rel_path,
            "target": target,
            "before_state": before_state,
            "after_state": after_state,
            "backup_source": backup_source,
            "state_key": state_key,
        }

    def build_restore_plan(self):
        operations = self.load_and_validate_manifest()
        self.restore_operations = []

        for op_item in reversed(operations):
            entries = []

            entries.append(self.collect_restore_entry(op_item, "path"))

            if "new_path" in op_item:
                entries.append(self.collect_restore_entry(op_item, "new_path"))

            mismatch_messages = []

            for entry in entries:
                if not current_state_matches_expected(
                    entry["target"],
                    entry["after_state"],
                ):
                    mismatch_messages.append(
                        f'{entry["path"]}：'
                        f'{describe_state_mismatch(entry["target"], entry["after_state"])}'
                    )

            self.restore_operations.append({
                "index": op_item.get("index"),
                "op": op_item.get("op", ""),
                "path": op_item.get("path", ""),
                "new_path": op_item.get("new_path", ""),
                "entries": entries,
                "mismatch": bool(mismatch_messages),
                "issue": "；".join(mismatch_messages),
            })

        self.risk_summary = build_restore_risk_summary_from_plan(self.restore_operations)

    def preview(self):
        self.build_restore_plan()

        lines = []
        lines.append("【备份还原 Dry Run 预演结果】")
        lines.append(f"项目根目录：{self.root}")
        lines.append(f"备份来源：{self.restore_source_dir}")

        if self.backup_enabled:
            lines.append(f"还原前备份目录：{self.backup_root}")
        else:
            lines.append("还原前备份：未启用")

        lines.append(f"可还原操作数量：{len(self.restore_operations)}")
        lines.append("")

        mismatch_count = len([item for item in self.restore_operations if item.get("mismatch")])
        lines.append(f"状态漂移：{mismatch_count} 项")

        if mismatch_count:
            lines.append("")
            lines.append("【状态漂移清单】")

            for item in self.restore_operations:
                if item.get("mismatch"):
                    lines.append(
                        f"- 原操作 {item.get('index')} {item.get('op')}：{item.get('issue')}"
                    )

        lines.append("")
        lines.append("【还原风险摘要】")

        if self.risk_summary.get("has_risk"):
            for key, title in (
                ("mismatch_paths", "状态漂移项"),
                ("delete_paths", "将删除当前存在路径"),
                ("overwrite_paths", "将覆盖当前路径"),
                ("restore_dirs", "涉及目录树还原"),
            ):
                values = self.risk_summary.get(key, [])
                if not values:
                    continue

                lines.append("")
                lines.append(f"{title}：{len(values)}")

                for item in values:
                    lines.append(f"- {item}")
        else:
            lines.append("未检测到高风险项。")

        lines.append("")
        lines.append("【还原说明】")
        lines.append("- 还原按原修改操作的反序执行。")
        lines.append("- 若 before_state 显示原路径不存在，则还原时会删除当前对应路径。")
        lines.append("- 若 before_state 记录了 backup_file，则从备份目录复制回原路径。")
        lines.append("- 若未启用还原前备份，工具无法备份当前状态。")
        lines.append("- 状态漂移不再依赖长期强制开关；是否继续由执行前弹窗二次确认决定。")

        return "\n".join(lines), self.manifest, bool(mismatch_count), self.risk_summary

    def restore_one_entry(self, entry):
        target = entry["target"]
        before_state = entry["before_state"]
        before_exists = bool(before_state.get("exists", False))
        backup_source = entry.get("backup_source")

        self.backup_current_path(target)

        if not before_exists:
            if target.exists():
                remove_path(target)
                remove_empty_parent_dirs_until_root(target, self.root)
                self.log(f"[还原] 删除新增路径：{entry['path']}")
            else:
                self.log(f"[跳过] 还原目标原本不存在，当前也不存在：{entry['path']}")
            return

        if backup_source is None:
            raise ValueError(f"缺少还原所需备份文件：{entry['path']}")

        restore_path_from_backup(backup_source, target)
        self.log(f"[还原] 恢复路径：{entry['path']}")

    def write_restore_manifest(self, status="completed", error_message=""):
        if not self.backup_enabled:
            return

        manifest = {
            "version": "2.4",
            "mode": "restore",
            "status": status,
            "project_root": str(self.root),
            "restore_source_dir": str(self.restore_source_dir),
            "backup_root": str(self.backup_root),
            "operations": [
                {
                    "index": item.get("index"),
                    "op": item.get("op"),
                    "path": item.get("path"),
                    "new_path": item.get("new_path"),
                    "mismatch": item.get("mismatch"),
                    "issue": item.get("issue"),
                    "entries": [
                        {
                            "path": entry.get("path"),
                            "state_key": entry.get("state_key"),
                            "before_state": entry.get("before_state"),
                            "after_state": entry.get("after_state"),
                        }
                        for entry in item.get("entries", [])
                    ],
                }
                for item in self.restore_operations
            ],
        }

        if error_message:
            manifest["error_message"] = error_message

        save_json(self.backup_root / "manifest.json", manifest)

    def write_restore_log_file(self):
        if not self.backup_enabled:
            self.log("[日志] 未启用自动备份，未写入还原前备份目录日志文件。")
            return

        log_path = self.backup_root / "还原执行日志.txt"
        self.log(f"[日志] {log_path}")

        file_log_lines = []

        if self.preview_text:
            file_log_lines.extend([
                "【备份还原 Dry Run 预演记录】",
                self.preview_text,
                "",
                "=" * 60,
                "【还原执行记录】",
                "=" * 60,
                "",
            ])

        file_log_lines.extend(self.logs)
        log_path.write_text("\n".join(file_log_lines), encoding="utf-8")

    def apply(self, risk_confirmed=False):
        self.build_restore_plan()

        if self.risk_summary.get("has_risk") and not risk_confirmed:
            raise ValueError("检测到高风险还原项，但本次执行未确认。")

        self.logs = []

        if self.backup_enabled:
            self.backup_root.mkdir(parents=True, exist_ok=True)

        self.log("【开始执行备份还原】")
        self.log(f"项目根目录：{self.root}")
        self.log(f"备份来源：{self.restore_source_dir}")

        if self.backup_enabled:
            self.log(f"还原前备份目录：{self.backup_root}")
        else:
            self.log("还原前备份：未启用")

        self.log("")

        try:
            for item in self.restore_operations:
                self.log(f"---- 还原原操作 {item.get('index')}: {item.get('op')} ----")

                for entry in item.get("entries", []):
                    self.restore_one_entry(entry)

            self.write_restore_manifest()

        except Exception as e:
            if self.backup_enabled:
                self.write_restore_manifest(
                    status="partial_failed",
                    error_message=str(e),
                )
                self.log("")
                self.log("[警告] 还原中途失败，已写入 partial_failed 还原 manifest。")

            raise

        self.log("")
        self.write_restore_log_file()

        return "\n".join(self.logs)


def preview_backup_restore_with_executor(
    project_root,
    restore_source_dir,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
):
    root = Path(project_root)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"项目根目录不存在或不是目录：{project_root}")

    source = Path(restore_source_dir)

    if not source.exists() or not source.is_dir():
        raise ValueError(f"备份来源不存在或不是目录：{restore_source_dir}")

    executor = BackupRestoreExecutor(
        root,
        source,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
    )

    preview_text, manifest, has_mismatch, risk_summary = executor.preview()

    return BackupRestorePreviewResult(
        preview_text=preview_text,
        manifest=manifest,
        has_mismatch=has_mismatch,
        risk_summary=risk_summary,
    )


def apply_backup_restore_with_executor(
    project_root,
    restore_source_dir,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
    risk_confirmed=False,
    preview_text="",
):
    root = Path(project_root)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"项目根目录不存在或不是目录：{project_root}")

    source = Path(restore_source_dir)

    if not source.exists() or not source.is_dir():
        raise ValueError(f"备份来源不存在或不是目录：{restore_source_dir}")

    executor = BackupRestoreExecutor(
        root,
        source,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        preview_text=preview_text,
    )

    log_text = executor.apply(risk_confirmed=risk_confirmed)

    return BackupRestoreApplyResult(
        log_text=log_text,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
    )