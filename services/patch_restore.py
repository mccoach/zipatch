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
        backup_project_root="", authorize_restore_scope=None,
        select_restore_scope=None,
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
        self.plan = plan_restore(
            evidence, authorize_restore_scope=authorize_restore_scope,
            select_restore_scope=select_restore_scope,
        )
        self.selected_object_ids = tuple(
            action.original["id"] for action in self.plan.actions
        )
        self.unselected_object_ids = tuple(
            entry["id"] for entry in evidence.document.value["objects"]
            if entry["id"] not in set(self.selected_object_ids)
        )
        # 名义访问先定位并在需要时授权；实际工作只使用普通父链。
        # 完整当前结构、原定义及两种位置属于同一本次固定任务。
        # apply只比较这些前提，不重新询问、定位或扩大恢复范围。
        self.confirmed = False
        self.execution_result = None
        self.auxiliary_result = None
        self.auxiliary_execution = None
        self.rollback_status = "not_attempted"
        self.rollback_report = ""
        self.rollback_error = None
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
            selected_object_ids=self.selected_object_ids,
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
            "【固定所选备份恢复】",
            f"清单对象：{len(self.evidence.document.value['objects'])} 个；"
            f"所选：{len(self.selected_object_ids)} 个；"
            f"未选：{len(self.unselected_object_ids)} 个。",
            "未选对象不获取当前正文、不加入还原前保护或执行依赖。",
            "完整目录树为一个恢复对象，不任意拆开树内成员。",
            f"来源：{self.source}",
            "恢复目标由清单决定；还原保护目录只保存本次独立保护任务。",
            "仅恢复所列文件、链接和目录树，不回退整个项目。",
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
            if kind in ("file_symlink", "directory_symlink", "junction"):
                description = (
                    "移除当前固定入口或结构，按保存的实际原定义重建链接入口；"
                    "不访问链接目标"
                )
            elif kind == "absent":
                from core.link_paths import LinkPathObservation
                description = (
                    "只删除新增链接入口，不展开目标"
                    if isinstance(current, LinkPathObservation) else
                    "删除新增目录及全部固定当前成员"
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
            lines.append(
                f"- 清单名义位置：{action.access.nominal_path}\n"
                f"  实际恢复位置：{action.target}\n  {description}"
            )
            for definition in action.access.followed_links:
                lines.extend((
                    f"  访问依赖入口：{definition.entry_path}",
                    f"  访问依赖原定义：{definition.raw_target!r}",
                    f"  直接引用位置：{definition.direct_target}",
                    "  此依赖不追加为保护、删除或恢复对象。",
                ))
            if isinstance(current, TreeObservation):
                lines.extend(
                    f"  - {current.path / relative}"
                    for relative, _, _ in current.content_records if relative
                )
        from core.protection_projection import observation_state
        from core.recovery_document import content_state_from_value
        from core.link_paths import validate_link_specification
        from core.recovery_paths import contains

        lines.extend((
            "", "【全部链接入口及定义授权范围】",
            "下面的直接目标仅由原定义解释，不查询其存在性、不读取正文。",
            "目录软链接和junction是叶子入口，不展开或删除目标目录。",
            "指向中间链接时保留该直接引用，不改成最终目标。",
            "本次只在清单固定目标位置移除当前结构及恢复保存原定义。",
            "经过目录链接的父链已在第一次目标枚举或旧正文读取前单独授权。",
            "实际工作、还原前保护及失败回滚共同固定这些访问依赖。",
            "预演后改指向时停止；不改用新指向，不修改清单名义目标。",
        ))
        found = False
        for action in self.plan.actions:
            desired = content_state_from_value(
                action.original["original"], target=action.target,
            )
            current, _ = observation_state(action.current)
            for title, state in (("保存原状态", desired), ("当前现场", current)):
                for relative, specification in state.link_definitions:
                    found = True
                    entry = action.target / relative
                    direct = validate_link_specification(specification, entry)
                    outside = not contains(self.backup_project_root, direct)
                    lines.extend((
                        f"- {title}；入口：{entry}",
                        f"  类型：{specification.kind}",
                        f"  实际原定义：{specification.raw_target!r}",
                        f"  相对定义：{specification.relative}",
                        f"  直接引用位置：{direct}",
                        "  相对于保护根："
                        + ("根外引用" if outside else "根内引用")
                        + "；该引用位置不取得正文访问或恢复对象资格。",
                    ))
        if not found:
            lines.append("本次没有链接入口定义。")
        lines.extend((
            "",
            "确认所选范围恢复，同时授权上述所选固定入口移除及原定义重建。",
            "所有必要当前状态备份成功后才开始恢复。",
            "现存硬链接通过完整内容写回原对象，保留关联关系。",
            "前序恢复已使关联名称达到原状态时，后续名称直接跳过。",
            "重建文件或目录树不重建原硬链接拓扑，结果记录重建名称。",
            "确认后仍检查固定前提，不接受外部变化来扩大覆盖范围。",
            "操作期间不要用其他程序修改涉及位置。",
            "工作恢复失败停止后续，按本任务实际步骤及固定范围自动回滚；"
            "不生成二次备份、不自动续执行。",
            "回滚无法可靠对应现场或失败时保留两份备份并提示人工处理。",
            "不承诺完整元数据、整批原子事务或断电恢复。",
        ))
        return "\n".join(lines)

    def _report(self):
        result = self.execution_result
        lines = [
            (
                "整包原状态恢复完成，全部目标通过最终内容检查。"
                if result.complete_original_state else
                "所选对象原状态恢复完成，通过所选范围最终检查；"
                "本次不是整份备份全部恢复。"
            ),
            f"未选对象：{len(self.unselected_object_ids)} 个。",
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
        if self.rollback_report:
            lines.extend(("", "【失败处理结果】", self.rollback_report))
        lines.extend((
            "正式步骤保留，原备份保留；不自动重试或续执行。",
            str(error),
        ))
        return "\n".join(lines)

    def _rollback_failed_work(self):
        """失败恢复只消费本次保护对象及实际工作步骤，不再次备份。"""
        if self.execution.task_execution.work_completed:
            return
        results = self.execution.results
        attempted = any(
            receipt.mutation_attempted
            if isinstance(receipt, ContentPublicationResult)
            else receipt.attempted
            for receipt in results
        )
        if not self.execution.backup_completed:
            if attempted:
                raise RuntimeError("还原前全部备份屏障之前出现工作改变")
            self.rollback_status = "not_required"
            self.rollback_report = (
                self.execution.backup_cleanup_report
                or "工作恢复尚未发起；不需要回滚，原来源备份保留。"
            )
            return
        if not attempted:
            self.rollback_status = "not_required"
            self.rollback_report = (
                "未发起工作恢复改变；原来源及还原前保护备份保留。"
            )
            return
        from core.automatic_rollback_scope import validate_automatic_rollback_scope
        from core.recovery_protection import read_protection_evidence

        try:
            self.execution.recheck_access_dependencies()
            evidence = read_protection_evidence(self.protection)
            plan = plan_restore(evidence)
            self.execution.recheck_access_dependencies()
            validate_automatic_rollback_scope(
                self.protection.projection, plan.actions, results=results,
            )
            rollback = RecoveryExecution(
                evidence, plan.actions,
                recheck_context=self.execution.recheck_access_dependencies,
            )
            result = rollback.execute()
        except (OSError, ValueError) as error:
            self.rollback_status = "failed"
            self.rollback_error = error
            self.rollback_report = (
                "工作恢复失败，自动回滚未完整完成，不能认定现场已恢复。\n"
                "原来源备份和还原前保护备份均保留，请人工处理；"
                "不自动重试、再备份或续执行。\n"
                f"还原前保护：{self.backup_root}\n{error}"
            )
        else:
            self.rollback_status = "completed"
            self.rollback_report = (
                "工作恢复失败，已自动恢复本次还原开始前的全部固定原状态。\n"
                "这不表示原来源备份恢复任务成功；未自动续执行。\n"
                "未生成二次备份，两份备份均保留。\n"
                f"实际恢复对象：{len(result.completed_units)}；"
                f"跳过未变对象：{len(result.skipped_paths)}。"
            )
            if result.rebuilt_files:
                self.rollback_report += (
                    "\n以下普通文件名称独立重建，不重建原硬链接拓扑：\n"
                    + "\n".join(map(str, result.rebuilt_files))
                )

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
                self._rollback_failed_work()
                raise
            report = self._report()
            self._write_execution_log(report)
            self.role, self.current, self.phase = "最终核验", self.source, "恢复流程结束前检查"
            self.execution.recheck()
            self.current, self.phase = None, "completed"
            return report
        except (OSError, ValueError) as error:
            raise BackupRestoreExecutionError(self, error) from error
