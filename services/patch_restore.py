# -*- coding: utf-8 -*-

"""一次预演、一次确认、一次集中恢复；只读取新格式。"""

from core.atomic_file import atomic_text_writer
from core.recovery_paths import checked_chain, entity_type
from services.patch_models import (
    BackupRestorePreviewResult, BackupRestoreApplyResult,
)
from services.recovery import (
    RecoveryProtection, check_isolation, check_material,
    load_recovery, perform_restore, plan_restore,
    recheck_action, recheck_plan, restore_summary,
)


class BackupRestoreExecutor:
    def __init__(
        self, root, restore_source_dir, backup_enabled=True,
        backup_dir="99_归档/AI文件修改备份",
    ):
        self.root, self.source, self.source_manifest, entries = load_recovery(
            restore_source_dir, root,
        )
        self.plan = plan_restore(self.root, entries)
        check_isolation(self.plan, source_root=self.source)
        self.protection = (
            RecoveryProtection(
                self.plan, "restore", backup_dir, source_root=self.source,
            )
            if backup_enabled and self.plan.objects else None
        )
        self.backup_root = self.protection.task_root if self.protection else None
        self.risk_summary = restore_summary(self.plan)
        if not backup_enabled:
            self.risk_summary["prebackup_absent_dirs"] = []
        self.completed_actions = set()
        self.consumed = False
        self.logs = []
        self.preview_text = self.format_preview()

    def format_preview(self):
        lines = [
            "【新格式备份还原预演】",
            f"项目根目录：{self.root}",
            f"备份来源：{self.source}",
            f"已发布对象：{len(self.plan.actions)}",
            f"还原前备份目录：{self.backup_root or '未启用或无需改变'}",
            "",
            "恢复会覆盖或删除保护范围内的后续修改。",
            "已发布但尚未执行的对象也可能被冗余还原。",
            "没有可靠执行后状态，不能确认没有后续修改。",
            "确认后执行本计划，范围变化时停止，不重新规划。",
            "规划、备份和执行期间请避免外部并发修改。",
        ]
        for key, title in (
            ("restore_paths", "恢复原状态"),
            ("overwrite_paths", "覆盖或移除当前对象"),
            ("delete_paths", "删除当前对象"),
            ("restore_dirs", "整体恢复原目录树"),
            ("create_parent_paths", "创建必要缺失父目录"),
            ("prebackup_absent_dirs", "还原前备份再次恢复的目录删除范围"),
        ):
            if self.risk_summary[key]:
                lines.extend(("", title + "："))
                lines.extend("- " + value for value in self.risk_summary[key])
        if not self.plan.actions:
            lines.append("有效空清单，无需恢复，不推测未引用材料。")
        return "\n".join(lines)

    def check_source_manifest(self):
        path = checked_chain(self.source / "recovery.json")
        if entity_type(path) != "file":
            raise ValueError("预演后源恢复清单不存在或类型变化")
        if path.read_bytes() != self.source_manifest:
            raise ValueError("预演后源恢复清单变化，必须重新预演及确认")

    def apply(self, risk_confirmed=False):
        if self.consumed:
            raise ValueError("本次恢复计划已经执行或失败，必须重新预演")
        if self.risk_summary["has_risk"] and not risk_confirmed:
            raise ValueError("恢复会覆盖或删除后续内容，必须当次确认")
        self.consumed = True
        self.check_source_manifest()
        recheck_plan(self.plan)
        check_isolation(
            self.plan, self.backup_root, source_root=self.source,
        )
        # 确认是新的时间边界；全部材料重新验证通过后才开始任何恢复。
        for action in self.plan.actions:
            check_material(self.source, action.payload)
        if not self.plan.actions:
            return "有效空清单，无需恢复。"
        stage, current_index, action_started = "执行期复核", 0, False
        try:
            for current_index, action in enumerate(self.plan.actions):
                action_started = False
                stage = "固定动作状态复核"
                recheck_action(self.plan, current_index, self.completed_actions)
                if not action.execute:
                    self.completed_actions.add(current_index)
                    self.logs.append(f"[无需改变] {action.label}")
                    continue
                stage = "还原前备份及清单发布"
                if self.protection:
                    self.protection.protect(current_index)
                # 保护可能耗时；再次确认正在读取的源和固定目标。
                stage = "保护后源材料及目标复核"
                self.check_source_manifest()
                check_material(self.source, action.payload)
                recheck_action(self.plan, current_index, self.completed_actions)
                stage = "原状态恢复"
                action_started = True
                perform_restore(self.root, self.source, action)
                self.completed_actions.add(current_index)
                stage = "完成日志"
                self.logs.append(f"[已恢复] {action.label}")
        except (OSError, ValueError) as error:
            action = self.plan.actions[current_index]
            completed = [
                self.plan.actions[index].label
                for index in sorted(self.completed_actions)
            ]
            message = (
                f"恢复中断：对象={action.label}，阶段={stage}。\n"
                f"已经完成：{', '.join(completed) or '无'}。\n"
                f"当前业务动作可能部分改变目标：{'是' if action_started else '否'}。\n"
                f"源备份：{self.source}\n"
                f"还原前备份：{self.backup_root or '未启用'}\n"
                f"没有自动整批回滚。\n{error}"
            )
            if isinstance(error, OSError):
                raise OSError(message) from error
            raise ValueError(message) from error
        try:
            if self.protection and self.protection.started:
                with atomic_text_writer(
                    self.backup_root / "还原执行日志.txt", newline="\n",
                ) as stream:
                    stream.write(self.preview_text + "\n\n")
                    stream.write("\n".join(self.logs) + "\n")
        except (OSError, ValueError) as error:
            raise OSError(
                "恢复动作已经完成，但辅助日志保存失败；"
                "源备份和已发布还原前备份独立有效，没有自动回滚。\n"
                f"源备份：{self.source}\n"
                f"还原前备份：{self.backup_root or '未启用'}\n{error}"
            ) from error
        return "\n".join(self.logs)


def preview_backup_restore_with_executor(
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


def apply_backup_restore_with_executor(preview, risk_confirmed=False):
    if not isinstance(preview, BackupRestorePreviewResult):
        raise ValueError("恢复必须消费本次预演结果")
    executor = preview.execution
    report = executor.apply(risk_confirmed=risk_confirmed)
    return BackupRestoreApplyResult(
        log_text=report,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
    )