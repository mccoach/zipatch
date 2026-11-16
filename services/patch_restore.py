# -*- coding: utf-8 -*-

"""整包恢复服务：固定预检、一次确认和共享恢复核心。"""

import uuid

from core.content_publication import ContentPublicationResult
from core.fixed_content_execution import FixedContentExecution
from core.filesystem_changes import FilesystemChangeResult
from core.file_observation import observe_file, TreeObservation
from core.recovery_execution import RecoveryExecution, RecoveryExecutionError
from core.recovery_protection import RecoveryEvidence
from core.recovery_paths import checked_root
from core.time_utils import now_stamp
from services.patch_backup import resolve_backup_base_dir
from services.recovery import plan_restore


class BackupRestoreExecutionError(OSError):
    def __init__(self, executor, error):
        self.executor = executor
        self.error = error
        self.role = executor.role
        self.current = executor.current
        self.phase = executor.phase
        self.result = executor.execution_result
        self.auxiliary_result = executor.auxiliary_result
        super().__init__(executor.failure_message(error))


def _step_description(receipt):
    if isinstance(receipt, ContentPublicationResult):
        return (
            f"- {receipt.target}；机制={receipt.mechanism}；"
            f"阶段={receipt.phase}；改变发起={receipt.mutation_attempted}；"
            f"正文完成={receipt.content_completed}；正常完成={receipt.succeeded}；"
            f"应急={receipt.emergency_path}",
        )
    if isinstance(receipt, FilesystemChangeResult):
        return (
            f"- {receipt.target}；动作={receipt.kind}；"
            f"阶段={receipt.phase}；改变发起={receipt.attempted}；"
            f"正常完成={receipt.succeeded}",
        )
    raise TypeError("恢复报告必须消费正式工作结果")


class BackupRestoreExecutor:
    def __init__(
        self, evidence, backup_dir="", *,
        backup_project_root="",
    ):
        if not isinstance(evidence, RecoveryEvidence):
            raise TypeError("恢复必须消费正式依据")
        if evidence.manifest is None:
            raise ValueError("人类主动恢复必须使用严格读取的磁盘清单")
        if (
            not isinstance(backup_project_root, str)
            or not backup_project_root.strip()
        ):
            raise ValueError("人类主动恢复强制保护，请填写独立保护项目根目录")
        from pathlib import Path
        selected_root = Path(backup_project_root)
        if not selected_root.is_absolute():
            raise ValueError("保护项目根目录必须是明确的绝对路径")
        self.backup_project_root = checked_root(selected_root)
        backup_base = resolve_backup_base_dir(
            self.backup_project_root, backup_dir,
        )
        self.evidence = evidence
        self.source = evidence.task_root
        self.plan = plan_restore(evidence)
        self.confirmed = False
        self.execution_result = None
        self.auxiliary_result = None
        self.auxiliary_execution = None
        self.backup_root = None
        self.role = "恢复准备"
        self.current = self.source
        self.phase = "prepared"
        task_id = None
        if any(action.execute for action in self.plan.actions):
            task_id = uuid.uuid4().hex
            self.backup_root = (
                backup_base / f"{now_stamp()}_备份还原前_{task_id}"
            )
        self.execution = RecoveryExecution(
            evidence, self.plan.actions,
            protection_root=self.backup_root,
            protection_task_id=task_id,
            material_base=self.backup_project_root,
        )
        self.risk_summary = {
            "restore_count": sum(action.execute for action in self.plan.actions),
            "no_operation_count": sum(not action.execute for action in self.plan.actions),

            "backup_root": str(self.backup_root) if self.backup_root else "",
        }
        self.preview_text = self.format_preview()

    @property
    def consumed(self):
        return self.execution.consumed

    @property
    def protection(self):
        return self.execution.protection

    def confirm_execution(self):
        if self.consumed or self.confirmed:
            raise ValueError("恢复任务已确认、执行或取消")
        self.confirmed = True

    def invalidate(self):
        return self.execution.invalidate()

    def format_preview(self):
        summary = self.risk_summary
        lines = [
            "【整包备份恢复】",
            f"来源：{self.source}",
            f"保护根目录：{self.backup_project_root}",
            "保护根用于定位备份保存目录，并作为共用材料布局的明确基准。",
            "同卷对象按共同基准保留相对位置；跨卷对象由共用布局按卷分组。",
            "该基准不限制、解释或改变原清单中的恢复目标。",
            "恢复清单记录的完整原状态，不重放原修改包。",
            "按内容、存在性和目录结构比较；仅时间不同不触发恢复。",
            f"预检需恢复：{summary['restore_count']} 个对象；"
            f"无需修改：{summary['no_operation_count']} 个对象。",
            (
                f"还原前保护目录：{self.backup_root}"
                if self.backup_root else
                "全部对象已符合原状态，本次无需生成空保护任务。"
            ),
            "", "【恢复范围与影响】",
        ]
        for action in self.plan.actions:
            if not action.execute:
                continue
            kind = action.original["original"]["kind"]
            current = action.current
            if kind == "absent":
                description = (
                    "删除新增目录及全部当前内容"
                    if isinstance(current, TreeObservation) else "删除新增文件"
                )
            elif kind == "directory":
                description = "恢复原目录树，移除当前额外内容"
            elif isinstance(current, TreeObservation):
                description = "删除当前目录及全部内容，重建原文件"
            elif current.kind == "absent":
                description = "重建原文件"
            else:
                description = "恢复原文件完整内容"
            lines.append(f"- {action.target}\n  {description}")
            if isinstance(current, TreeObservation):
                lines.extend(
                    f"  - {current.path / relative}"
                    for relative, _, _ in current.content_records if relative
                )
        lines.extend((
            "",
            "现存硬链接通过完整内容写回原对象，保留关联关系。",
            "前序恢复已使关联名称达到原状态时，后续名称直接跳过。",
            "重建文件或目录树不重建原硬链接拓扑，结果记录重建名称。",
            "确认后仍检查固定前提，不接受外部变化来扩大覆盖范围。",
            "操作期间不要用其他程序修改涉及位置。",
            "失败停止并提示；不自动重试，不删除原备份。",
        ))
        return "\n".join(lines)

    def _report(self):
        result = self.execution_result
        lines = [
            "整包原状态恢复完成，全部目标通过最终内容检查。",
            f"实际恢复对象：{len(result.completed_units)}；"
            f"跳过未变对象：{len(result.skipped_paths)}。",
            f"原备份保留：{self.source}",
            f"还原前保护：{self.backup_root or '未生成'}",
        ]
        lines.extend(f"[无需修改] {path}" for path in result.skipped_paths)
        if result.rebuilt_files:
            lines.extend((
                "",
                "【独立文件重建说明】",
                "以下名称已独立重建；内容恢复，不重建原硬链接关系：",
                *(str(path) for path in result.rebuilt_files),
            ))
        return "\n".join(lines)

    def _write_execution_log(self, report):
        if self.protection is None or not self.protection.directory_results:
            return
        target = self.backup_root / "还原执行日志.txt"
        self.role, self.current, self.phase = "辅助输出", target, "恢复日志准备"
        expected = observe_file(target, include_digest=True)
        if expected.kind != "absent":
            raise ValueError("恢复日志目标不再不存在")
        execution = FixedContentExecution(
            expected, (self.preview_text + "\n\n" + report + "\n").encode("utf-8"),
        )
        self.auxiliary_execution = execution

        def stage(position, phase):
            self.current, self.phase = position, phase

        execution.execute(
            recheck_context=self.execution.recheck,
            record_step=lambda receipt: setattr(self, "auxiliary_result", receipt),
            stage=stage,
        )
        self.auxiliary_execution = None

    def failure_message(self, error):
        lines = [
            f"恢复流程未完整结束：{self.current}；阶段={self.phase}。",
            f"原备份：{self.source}",
            f"还原前保护：{self.backup_root or '未生成'}",
        ]
        if self.execution.backup_cleanup_report:
            lines.append(self.execution.backup_cleanup_report)
        if self.execution_result is not None:
            lines.append(
                f"恢复核心已通过全部最终检查：{self.execution_result.succeeded}"
            )
            for receipt in self.execution_result.results:
                lines.extend(_step_description(receipt))
            if self.execution_result.rebuilt_files:
                lines.append("以下文件已独立重建，不保证原硬链接关系：")
                lines.extend(map(str, self.execution_result.rebuilt_files))
        if self.auxiliary_result is not None:
            lines.append("【独立恢复日志结果】")
            lines.extend(_step_description(self.auxiliary_result))
        lines.extend((
            "此前恢复步骤保留，原备份保留；不自动重试。",
            str(error),
        ))
        return "\n".join(lines)

    def apply(self):
        if self.consumed or not self.confirmed:
            raise ValueError("恢复任务已消费，或尚未取得本次确认")
        self.role, self.current, self.phase = "工作恢复", self.source, "整包原状态恢复"
        try:
            try:
                self.execution_result = self.execution.execute()
            except RecoveryExecutionError as error:
                self.execution_result = error.result
                self.current, self.phase = error.result.current, error.result.phase
                if self.protection is not None and self.protection.failed:
                    self.role = "正式保护"
                raise
            report = self._report()
            self._write_execution_log(report)
            self.role, self.current, self.phase = "最终核验", self.source, "恢复流程结束前检查"
            self.execution.recheck()
            self.current, self.phase = None, "completed"
            return report
        except (OSError, ValueError) as error:
            raise BackupRestoreExecutionError(self, error) from error
