# -*- coding: utf-8 -*-

"""严格version 4正式恢复：决定、确认及唯一整任务核心消费。"""

import sys
import uuid
from pathlib import Path
from types import MappingProxyType

from core.content_publication import (
    ContentPublicationError, ContentPublicationResult,
    dispose_content_artifact, prepare_content_artifact, publish_content,
)
from core.file_observation import observe_file
from core.filesystem_changes import FilesystemChangeResult
from core.recovery_execution import (
    RecoveryExecutionError, prepare_recovery_execution,
)
from core.recovery_paths import checked_root
from core.recovery_protection import read_recovery_evidence
from core.time_utils import now_stamp
from services.patch_backup import resolve_backup_base_dir
from services.recovery import plan_restore, restore_summary
from services.recovery_comparison import (
    RESTORE_DIFFERENCE_LABELS, RESTORE_STATUS_LABELS,
    format_execution_fact, format_restore_comparisons,
)
from services.recovery_selection import (
    RESTORE_DECISION_LABELS, change_restore_decisions,
    initial_restore_decisions, restore_content_loss_paths,
    restore_plan_complete_original_state, selected_restore_plan,
)
from services.recovery_subitems import (
    SUBITEM_DECISION_LABELS, change_subitem_decisions,
    format_subitem_scope, initial_subitem_decisions,
)


_PROTECTION_PHASES = frozenset((
    "全部必要还原前保护屏障",
    "工作改变前started屏障",
    "工作完成事实发布",
))


class BackupRestoreExecutionError(OSError):
    """正式工作、保护及辅助事实分别持有，不解析异常原因猜对象。"""

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
    """只消费共享恢复实际使用的两类正式工作结果。"""
    if isinstance(receipt, ContentPublicationResult):
        lines = [
            f"- {receipt.target}；机制={receipt.mechanism}；阶段={receipt.phase}",
            f"  改变发起={receipt.mutation_attempted}；"
            f"名称替换={receipt.replacement}；"
            f"正文确认={receipt.content_completed}；"
            f"必要确认={receipt.confirmation_completed}；"
            f"应急={receipt.emergency_state}；位置={receipt.emergency_path}",
        ]
        return tuple(lines)
    if isinstance(receipt, FilesystemChangeResult):
        return (
            f"- {receipt.target}；动作={receipt.kind}；"
            f"改变发起={receipt.attempted}；"
            f"正常完成={receipt.succeeded}；阶段={receipt.phase}",
        )
    raise TypeError("正式恢复报告只接受共享恢复工作步骤")


class BackupRestoreExecutor:
    def __init__(
        self, root, restore_source_dir, backup_enabled=True,
        backup_dir="99_归档/AI文件修改备份",
    ):
        if type(backup_enabled) is not bool:
            raise TypeError("还原前保护开关必须是布尔值")
        self.evidence = read_recovery_evidence(restore_source_dir)
        self.source = self.evidence.task_root
        # 绝对来源恢复不需要借用拆分目标目录或猜测项目根。
        # 未提供业务定位根时，源任务自身作为必须保护的定位范围；
        # 清单绝对目标、非根限制及来源隔离仍由正式核心检查。
        self.root = checked_root(root) if root is not None else self.source
        self.source_document = self.evidence.document.value
        self.plan = plan_restore(self.root, self.evidence)
        self.selection_plan = self.plan
        self.backup_enabled = backup_enabled
        self.backup_dir = backup_dir
        self.decisions = initial_restore_decisions(self.plan)
        self.subitem_decisions = MappingProxyType({})
        self.finalized = False
        self.confirmed = False
        self.consumed = False
        self.execution = None
        self.execution_result = None
        self.auxiliary_result = None
        self.backup_root = None
        self.logs = []
        self.role = "恢复准备"
        self.current = self.source
        self.phase = "prepared"
        self.risk_summary = self.selection_summary()
        self.preview_text = self.format_preview()

    @property
    def protection(self):
        return None if self.execution is None else self.execution.protection

    def _check_editable(self):
        if self.consumed:
            raise ValueError("恢复任务已执行、失败或取消作废，必须重新预演")
        if self.finalized:
            raise ValueError("最终恢复方案已固定，不能改变对象决定")

    def _refresh(self):
        self.risk_summary = self.selection_summary()
        self.preview_text = self.format_preview()
        return self.risk_summary

    def selection_summary(self):
        summary = restore_summary(self.plan, self.source_document)
        summary.update(
            decision_records=tuple(
                (
                    action.payload["id"], action.label,
                    self.decisions[action.payload["id"]],
                )
                for action in self.plan.actions
            ),
            selected_restore_count=sum(
                value == "restore" for value in self.decisions.values()
            ),
            selected_keep_count=sum(
                value == "keep" for value in self.decisions.values()
            ),
            subitem_scopes=tuple(
                action.subitem_scope for action in self.plan.actions
                if action.subitem_scope is not None
            ),
            subitem_decision_records=tuple(
                (identifier, tuple(values.items()))
                for identifier, values in self.subitem_decisions.items()
            ),
            selected_subitem_count=len(self.subitem_decisions),
            subitem_undecided_count=sum(
                value == "undecided"
                for values in self.subitem_decisions.values()
                for value in values.values()
            ),
            cleanup_plans=tuple(
                action.cleanup_plan for action in self.plan.actions
                if action.cleanup_plan is not None
            ),
            finalized=self.finalized,
            backup_enabled=self.backup_enabled,
            backup_directory_setting=self.backup_dir,
            backup_root=str(self.backup_root) if self.backup_root else "",
            content_loss_paths=(
                restore_content_loss_paths(self.plan)
                if self.finalized else ()
            ),
            complete_original_state=(
                self.finalized
                and restore_plan_complete_original_state(self.plan)
            ),
        )
        summary["undecided_count"] = (
            sum(
                value == "undecided" for value in self.decisions.values()
            )
            + summary["subitem_undecided_count"]
        )
        summary["retained_subitem_paths"] = tuple(
            str(cleanup.scope.target / relative)
            for cleanup in summary["cleanup_plans"]
            for relative, _, _ in cleanup.retained_records
        )

        # 唯一保护投影归共享执行核心；
        # 摘要只读取它，不从业务对象再推导一份保护范围。
        objects = (
            () if self.execution is None
            else self.execution.projection.objects
        )
        summary["protection_objects"] = tuple(
            (str(member.target), member.original_type)
            for member in objects
        )
        summary["prebackup_absent_dirs"] = (
            [
                str(member.target)
                + "（再次恢复时删除其整个当前内容）"
                for member in objects
                if member.original_type == "absent"
                and any(
                    member.target == parent
                    for unit in self.execution.projection.units
                    for parent in unit.parent_directories
                )
            ]
            if self.backup_enabled and self.execution is not None else []
        )
        return summary

    def choose(
        self, identifiers, decision, *,
        scope="selected", replace_decisions=False,
    ):
        self._check_editable()
        candidate = change_restore_decisions(
            self.selection_plan, self.decisions, identifiers, decision,
            scope=scope, replace_decisions=replace_decisions,
        )
        subitems = MappingProxyType({
            identifier: values
            for identifier, values in self.subitem_decisions.items()
            if candidate[identifier] == "subitems"
        })
        self.decisions = candidate
        self.subitem_decisions = subitems
        return self._refresh()

    def choose_subitems(
        self, identifier, relatives, decision, *,
        selection="selected", replace_decisions=False,
        replace_object_decision=False,
    ):
        self._check_editable()
        if type(identifier) is not str:
            raise TypeError("子项对象编号必须是字符串")
        if type(replace_object_decision) is not bool:
            raise TypeError("替换整体决定许可必须是布尔值")
        action = next((
            action for action in self.selection_plan.actions
            if action.payload["id"] == identifier
        ), None)
        if action is None:
            raise ValueError("子项对象不属于本次正式恢复任务")
        if action.subitem_scope is None:
            raise ValueError(
                "此对象必须整体恢复或保留，不支持子项清理"
            )
        if (
            self.decisions[identifier] in ("restore", "keep")
            and not replace_object_decision
        ):
            raise ValueError("子项方案将替换整体决定，必须明确确认")
        previous = self.subitem_decisions.get(identifier)
        if previous is None:
            previous = initial_subitem_decisions(action.subitem_scope)
        candidate = change_subitem_decisions(
            action.subitem_scope, previous, relatives, decision,
            selection=selection, replace_decisions=replace_decisions,
        )
        objects = dict(self.decisions)
        objects[identifier] = "subitems"
        subitems = dict(self.subitem_decisions)
        subitems[identifier] = candidate
        self.decisions = MappingProxyType(objects)
        self.subitem_decisions = MappingProxyType(subitems)
        return self._refresh()

    def prepare_execution(self):
        self._check_editable()
        # 未决定属于可修正拒绝，不消费任务。
        candidate = selected_restore_plan(
            self.selection_plan, self.decisions,
            subitem_decisions=self.subitem_decisions,
        )
        completed = False
        try:
            task_root = task_id = None
            if self.backup_enabled and candidate.units:
                task_id = uuid.uuid4().hex
                task_root = (
                    resolve_backup_base_dir(self.root, self.backup_dir)
                    / f"{now_stamp()}_备份还原前_{task_id}"
                )
            execution = prepare_recovery_execution(
                self.evidence, candidate.units,
                kept=candidate.kept, unchanged=candidate.unchanged,
                forbidden_roots=(self.root,),
                protection_root=task_root,
                protection_task_id=task_id,
            )
            self.plan = candidate
            self.execution = execution
            self.backup_root = task_root
            self.finalized = True
            summary = self._refresh()
            completed = True
            return summary
        finally:
            if not completed:
                self.consumed = True
                if self.execution is not None:
                    self.execution.invalidate()

    def return_to_selection(self):
        if self.consumed:
            raise ValueError("恢复任务已执行、失败或取消作废，必须重新预演")
        if self.confirmed:
            raise ValueError("最终方案已经确认，不能返回修改决定")
        if not self.finalized:
            raise ValueError("当前没有已经固定的最终方案")
        self.execution.invalidate()
        self.execution = None
        self.plan = self.selection_plan
        self.backup_root = None
        self.finalized = False
        return self._refresh()

    def confirm_execution(self, *, loss_acknowledged=False):
        if self.consumed:
            raise ValueError("恢复任务已执行、失败或取消作废，必须重新预演")
        if not self.finalized:
            raise ValueError("必须先完成对象决定并固定最终恢复方案")
        if type(loss_acknowledged) is not bool:
            raise TypeError("总风险确认必须是布尔值")
        if self.confirmed:
            raise ValueError("最终恢复方案已经确认，不能重复确认")
        if (
            not self.backup_enabled
            and restore_content_loss_paths(self.plan)
            and not loss_acknowledged
        ):
            raise ValueError(
                "未启用还原前保护且将损失当前内容，必须明确确认总风险"
            )
        self.confirmed = True

    def invalidate(self):
        if self.consumed:
            return False
        self.consumed = True
        if self.execution is not None:
            self.execution.invalidate()
        return True

    def format_preview(self):
        summary = self.risk_summary
        lines = [
            "【严格version 4备份还原预演】",
            "材料保留原文件名和目录结构；按清单material位置读取，"
            "恢复目标仍使用清单target绝对位置。",
            f"业务定位根／相对备份基准：{self.root}",
            "恢复目标直接使用清单绝对位置，不映射到上述目录。",
            f"备份来源：{self.source}",
            f"已发布原状态对象：{len(self.plan.actions)}",
            "恢复依据是正式原状态及编号材料；不重放原OP。",
            "预期不等于实际完成，事实不足不剥夺原状态恢复依据。",
            (
                f"最终方案：实际恢复 {summary['restore_count']}；"
                f"用户保留 {summary['keep_count']}；"
                f"无需操作 {summary['no_operation_count']}；"
                f"其中仅正式时间待恢复 {summary['time_pending_count']}。"
                if self.finalized else
                f"对象决定：未决定 {summary['undecided_count']}；"
                f"已选恢复 {summary['selected_restore_count']}；"
                f"已选保留 {summary['selected_keep_count']}；"
                f"无需操作 {summary['no_operation_count']}。"
            ),
            "正式服务及GUI支持原不存在目录的固定子项清理／保留；"
            "整体与子项方案互斥，切换必须明确接受重设。",
            "原先存在的对象继续整体恢复或保留；"
            "不允许整体替换同时保留其后代。",
            "保留对象持续参与后续必要状态复核。",
            "无需操作对象仍检查正式文件时间前提。",
            "实际待恢复对象仅时间变化不作为内容偏离。",
            "文件修改时间使用清单正式mtime_ns，不使用材料后来时间。",
            "普通文件完整暂存后名称替换；硬链接保持原对象写回。",
            "目录转文件先完整准备成果再移除目录。",
            "目录树、移除和整批恢复不是原子事务。",
            "任一失败停止后续；已有步骤不自动撤销。",
            "规划、保护及执行期间请避免外部并发修改。",
            f"还原前保护：{'开启' if self.backup_enabled else '关闭'}",
            f"保护目录设置：{self.backup_dir}",
            f"最终计划还原前备份：{self.backup_root or '尚未规划或无需保护'}",
            (
                "全部必要保护及保护后复核先于首个恢复动作；"
                "还原前材料可经同一入口再次恢复。"
                if self.backup_enabled else
                "没有还原前保护，不能保证找回本次覆盖或删除的当前内容。"
            ),
            "", "【对象决定】",
        ]
        lines.extend(
            f"- {identifier}；{target}；"
            f"{RESTORE_DECISION_LABELS[decision]}"
            for identifier, target, decision in summary["decision_records"]
        )
        lines.extend(("", "【正式原状态及文件时间】"))
        for target, kind, times in summary["original_states"]:
            lines.append(
                f"- {target}；原类型={kind}；时间记录={len(times)}"
            )
            lines.extend(
                f"  - {Path(target) / relative}；mtime_ns={modified}"
                for relative, modified in times
            )
        lines.extend(("", "【本次固定差异分类】"))
        lines.extend(
            f"- {label}：{summary['status_counts'][status]} 个对象"
            for status, label in RESTORE_STATUS_LABELS.items()
        )
        lines.append("差异按对象统计，可重叠，不相加为动作数量：")
        lines.extend(
            f"- {label}：{summary['difference_counts'][category]} 个对象"
            for category, label in RESTORE_DIFFERENCE_LABELS.items()
        )
        lines.append(
            f"- 原任务执行事实不足："
            f"{summary['fact_insufficient_count']} 个对象。"
        )
        lines.extend(format_restore_comparisons(summary["comparisons"]))
        lines.extend(("", "【原任务操作后预期】"))
        for target, kind, integrity in summary["expected_states"]:
            lines.append(
                f"- {target}；预期={kind}；"
                f"内容指纹={integrity['digest'] if integrity else '无'}"
            )
        lines.extend(("", "【原任务正式执行事实】"))
        for identifier, members, fact in summary["execution_facts"]:
            lines.append(f"共同成员：{', '.join(members)}")
            lines.extend(format_execution_fact(
                identifier, fact["state"], fact["changes"],
            ))
        lines.extend((
            "", "【最终实际范围】" if self.finalized else
            "【候选恢复影响：尚不是执行授权】",
        ))
        for key, title in (
            ("restore_paths", "恢复原状态"),
            ("overwrite_paths", "覆盖或移除当前对象"),
            ("delete_paths", "删除当前对象"),
            ("restore_dirs", "整体恢复原目录树"),
            ("create_parent_paths", "创建必要缺失父目录"),
            ("prebackup_absent_dirs", "还原前材料再次恢复的整体删除范围"),
        ):
            if summary[key]:
                lines.extend(("", title + "："))
                lines.extend("- " + path for path in summary[key])
        for target, members in summary["current_tree_impacts"]:
            lines.extend((
                "", f"当前目录整体移除范围：{target}",
                "以下包含全部当前子项、隐藏名称及空目录；"
                "这是整体恢复的完整影响，不是子项删除授权。",
            ))
            if not members:
                lines.append("- 当前目录为空")
            lines.extend(
                f"- [{'文件' if kind == 'file' else '目录'}] {path}"
                for path, kind in members
            )
        if summary["subitem_scopes"]:
            lines.extend((
                "", "【固定子项分类】",
                "正式服务及GUI支持子项决定；整体删除须另行明确选择。",
                "固定分类不是删除授权，未决定子项不得进入最终方案。",
            ))
            lines.extend(
                format_subitem_scope(scope)
                for scope in summary["subitem_scopes"]
            )
        if summary["subitem_decision_records"]:
            lines.extend(("", "【正式子项决定】"))
            for identifier, decisions in summary["subitem_decision_records"]:
                action = next(
                    action for action in self.plan.actions
                    if action.payload["id"] == identifier
                )
                lines.extend(
                    f"- {identifier}；{action.current.path / relative}；"
                    f"{SUBITEM_DECISION_LABELS[decision]}"
                    for relative, decision in decisions
                )
        for cleanup in summary["cleanup_plans"]:
            lines.extend((
                "", f"【固定选择性清理范围】{cleanup.scope.target}",
                "文件逐项删除，目录仅在为空时移除，不递归清理兜底。",
            ))
            lines.extend(
                f"- {'删除文件' if step.removal == 'file' else '移除空目录'}："
                f"{step.item.path}"
                for step in cleanup.steps
            )
            if not cleanup.steps:
                lines.append("无工作删除，全部当前范围保留。")
            lines.append("保留结果（包含必要祖先目录）：")
            lines.extend(
                f"- {cleanup.scope.target / relative}"
                for relative, _, _ in cleanup.retained_records
            )
            if not cleanup.retained_records:
                lines.append("无，全部步骤完成后满足原不存在状态。")
        if self.finalized:
            lines.extend(("", "【本次必要还原前保护范围】"))
            if self.backup_enabled:
                lines.extend(
                    f"- {target}；当前类型={kind}"
                    for target, kind in summary["protection_objects"]
                )
                if not summary["protection_objects"]:
                    lines.append("无实际改变对象，不生成保护材料。")
                lines.append(
                    "选择性清理只保护实际删除文件或完整被清理子树；"
                    "不保存包含保留内容的父树或无关兄弟。"
                )
            else:
                lines.append("未启用；不会保存本次删除或覆盖的当前内容。")
            lines.extend(("", "【当前内容损失范围】"))
            lines.extend("- " + path for path in summary["content_loss_paths"])
            if not summary["content_loss_paths"]:
                lines.append("无固定内容差异造成的损失范围")
            lines.append(
                "全部按本方案成功后可满足全部原状态。"
                if summary["complete_original_state"] else
                "本方案包含用户保留；按计划完成不表示全部原状态已经恢复。"
            )
        if not self.plan.actions:
            lines.append("有效空清单，无需恢复，不推测未引用材料。")
        return "\n".join(lines)

    def _completed_report(self):
        result = self.execution_result
        completed = set(result.completed_units)
        unit_by_member = {
            member: unit.unit_id
            for unit in self.execution.projection.units
            for member in unit.members
        }
        self.logs.append(
            f"本次固定方案：实际待恢复 "
            f"{self.risk_summary['restore_count']} 个；"
            f"用户保留 {self.risk_summary['keep_count']} 个；"
            f"无需操作 {self.risk_summary['no_operation_count']} 个。"
        )
        if not self.plan.units:
            self.logs.append(
                "本次没有工作恢复动作；不创建工作目录或还原前备份。"
            )
        for action in self.plan.actions:
            identifier = action.payload["id"]
            if not action.execute:
                kept = action.restore_decision == "keep" or (
                    action.cleanup_plan is not None
                    and bool(action.cleanup_plan.retained_records)
                )
                self.logs.append(
                    f"[用户保留] {action.label}；未执行工作恢复"
                    if kept else f"[无需改变] {action.label}"
                )
                continue
            if unit_by_member[identifier] not in completed:
                raise RuntimeError("正常恢复结果缺少本对象的工作完成单元")
            self.logs.append(
                f"[按子项方案完成] {action.label}"
                if action.cleanup_plan is not None else
                f"[已恢复] {action.label}"
            )
            if action.cleanup_plan is not None:
                self.logs.extend(
                    f"[已删除] {step.item.path}"
                    for step in action.cleanup_plan.steps
                )
                self.logs.extend(
                    f"[保留子项或必要祖先] "
                    f"{action.cleanup_plan.scope.target / relative}"
                    for relative, _, _ in action.cleanup_plan.retained_records
                )
        self.logs.append(
            "本次方案完成，全部对象的原状态要求已经满足。"
            if result.complete_original_state else
            "按本次方案完成，但包含用户保留，未完整恢复全部操作前状态。"
        )
        self.logs.append(
            f"备份目录：{self.backup_root or '未启用或无需改变'}"
        )

    def _write_execution_log(self):
        if self.protection is None or not self.protection.directory_results:
            return
        target = self.backup_root / "还原执行日志.txt"
        self.role, self.current, self.phase = (
            "辅助输出", target, "辅助目标固定观察",
        )
        expected = observe_file(target, include_digest=True)
        if expected.kind != "absent":
            raise ValueError("本任务辅助日志目标不再不存在")
        content = (
            self.preview_text + "\n\n" + "\n".join(self.logs) + "\n"
        ).encode("utf-8")
        artifact = None
        try:
            self.phase = "完整辅助日志准备"
            artifact = prepare_content_artifact(expected, content)
            self.execution.recheck()
            self.phase = "辅助日志正式发布"
            try:
                self.auxiliary_result = publish_content(artifact)
            except ContentPublicationError as error:
                self.auxiliary_result = error.result
                self.current, self.phase = (
                    error.result.target, error.result.phase,
                )
                raise
        finally:
            if artifact is not None and artifact._lifecycle not in (
                "completed", "disposed",
            ):
                # 固定absent日志只形成普通独立暂存。
                # 清理诊断不能覆盖主要准备、发布或程序错误。
                try:
                    dispose_content_artifact(artifact)
                except (OSError, ValueError) as cleanup_error:
                    print(
                        f"[正式恢复辅助暂存清理失败] "
                        f"{artifact.path}：{cleanup_error}",
                        file=sys.stderr,
                    )

    def failure_message(self, error):
        result = self.execution_result
        lines = [
            f"正式恢复未完整结束：角色={self.role}；"
            f"位置={self.current}；阶段={self.phase}。",
            f"源备份：{self.source}",
            f"还原前备份：{self.backup_root or '未启用或无需改变'}",
        ]
        completed = set()
        unit_by_member = {}
        if self.execution is not None:
            unit_by_member = {
                member: unit.unit_id
                for unit in self.execution.projection.units
                for member in unit.members
            }
        if result is not None:
            completed = set(result.completed_units)
            lines.extend((
                f"工作正常完成单元：{len(result.completed_units)}；"
                f"整任务完成={result.completed}；"
                f"完整原状态={result.complete_original_state}",
                "【真实工作步骤】",
            ))
            for receipt in result.results:
                lines.extend(_step_description(receipt))
            if not result.results:
                lines.append(
                    "无正式工作步骤记录；不能据此证明磁盘没有其他改变。"
                )
            if result.fact_error is not None:
                lines.append(
                    f"不确定事实补充也未保存：{result.fact_error}"
                )
        lines.extend(("", "【对象工作完成与固定决定】"))
        for action in self.plan.actions:
            number = unit_by_member.get(action.payload["id"])
            lines.append(
                f"- {action.label}；决定={action.restore_decision}；"
                f"实际待恢复={action.execute}；"
                f"工作正常完成={number in completed if number else False}"
            )
            if action.cleanup_plan is not None:
                lines.extend(
                    f"  保留子项或必要祖先："
                    f"{action.cleanup_plan.scope.target / relative}"
                    for relative, _, _ in action.cleanup_plan.retained_records
                )
        if self.auxiliary_result is not None:
            lines.extend(("", "【独立辅助日志发布事实】"))
            lines.extend(_step_description(self.auxiliary_result))
        lines.extend((
            "工作目标、正式保护和辅助输出分别判断。",
            "没有名称替换不表示没有原地改变；unknown不认证完整正文。",
            "此前真实步骤不自动撤销；旧任务永久失效。",
            "不自动重试、回滚或删除正式材料。",
            str(error),
        ))
        return "\n".join(lines)

    def apply(self):
        if self.consumed:
            raise ValueError(
                "本次恢复计划已经执行或失败，或已取消作废，必须重新预演"
            )
        if not self.finalized:
            raise ValueError("必须先完成对象决定并固定最终恢复方案")
        if self.plan.units and not self.confirmed:
            raise ValueError("实际恢复必须取得本次最终汇总确认")
        self.consumed = True
        try:
            self.role, self.current, self.phase = (
                "工作恢复", self.source, "共享整任务恢复",
            )
            try:
                self.execution_result = self.execution.execute()
            except RecoveryExecutionError as error:
                self.execution_result = error.result
                self.current, self.phase = (
                    error.result.current, error.result.phase,
                )
                if (
                    error.result.phase in _PROTECTION_PHASES
                    or error.result.protection is not None
                    and error.result.protection.failed
                ):
                    self.role = "正式保护"
                raise
            self.role, self.phase = "运行记录", "正常工作结果报告"
            self._completed_report()
            self._write_execution_log()
            self.role, self.current, self.phase = (
                "整任务", self.source, "辅助输出后最终联合复核",
            )
            self.execution.recheck()
            self.phase = "completed"
            self.current = None
            return "\n".join(self.logs)
        except (OSError, ValueError) as error:
            raise BackupRestoreExecutionError(self, error) from error
