# -*- coding: utf-8 -*-

"""固定任务的唯一生命周期、账本、条目结果及工作结束边界。"""

from dataclasses import dataclass
from types import MappingProxyType

from core.execution_state import ExecutionState
from core.fixed_unit_execution import FixedUnitExecution
from core.protection_projection import ProtectionUnit


@dataclass(frozen=True)
class FixedTaskWork:
    unit: ProtectionUnit
    perform: object
    accept_completed: object
    recheck: object

    def __post_init__(self):
        if not isinstance(self.unit, ProtectionUnit):
            raise TypeError("任务工作必须关联ProtectionUnit")
        for callback in (self.perform, self.accept_completed, self.recheck):
            if not callable(callback):
                raise TypeError("任务工作必须具有明确动作、接受和复核能力")


@dataclass(frozen=True)
class TaskEntryResult:
    status: str = "pending"
    results: tuple = ()
    stage: str = "尚未执行"
    work_completed: bool = False
    error: OSError | ValueError | None = None


class FixedTaskExecution:
    """共同任务拥有执行事实；业务只投影成员和展示结果。"""

    def __init__(self):
        self.state = ExecutionState()
        self._lifecycle = "prepared"
        self.work_completed = False
        self.current_entry = None
        self.entry_result_start = 0
        self.active_unit = None
        self.fact_error = None
        self._entry_results = {}
        self._phase = "prepared"

    @property
    def consumed(self):
        return self._lifecycle != "prepared"

    @property
    def lifecycle(self):
        return self._lifecycle

    @property
    def entry_results(self):
        return MappingProxyType(self._entry_results)

    def entry_result(self, key):
        return self._entry_results.get(key, TaskEntryResult())

    def invalidate(self):
        if self.consumed:
            return False
        self._lifecycle = "cancelled"
        return True

    def execute(
        self, entries, *, protection, initial_recheck, prepare_entry, stage,
        entry_key=id, entry_completed=None,
        final_check=None, auxiliary=None, after_auxiliary_check=None,
        task_target=None,
    ):
        if self.consumed:
            raise ValueError("固定任务已执行、失败或取消")
        for callback in (initial_recheck, prepare_entry, stage, entry_key):
            if not callable(callback):
                raise TypeError("固定任务缺少明确协调能力")
        for callback in (
            entry_completed, final_check, auxiliary, after_auxiliary_check,
        ):
            if callback is not None and not callable(callback):
                raise TypeError("可选任务能力必须可调用")

        def publish_stage(role, target, phase):
            self._phase = phase
            stage(role, target, phase)

        self._lifecycle = "attempted"
        completed = False
        try:
            publish_stage("执行前全量关口", task_target, "执行开始前固定前提复核")
            initial_recheck()
            if protection is not None:
                publish_stage("正式保护", protection.task_root, "全部必要原状态备份屏障")
                protection.protect_all()
                publish_stage("执行前全量关口", task_target, "全部备份后固定前提复核")
                initial_recheck()

            for entry in entries:
                key = entry_key(entry)
                if key in self._entry_results:
                    raise ValueError("任务条目结果标识重复")
                self.current_entry = entry
                self.entry_result_start = len(self.state.results)
                self._entry_results[key] = TaskEntryResult(
                    status="running", stage="条目准备",
                )
                entry_returned = False
                work = None
                try:
                    work = prepare_entry(entry)
                    if work is not None:
                        if not isinstance(work, FixedTaskWork):
                            raise TypeError("条目准备只能返回FixedTaskWork或固定跳过")
                        self.active_unit = FixedUnitExecution(
                            work.unit, protection=protection, state=self.state,
                            recheck=work.recheck, stage=publish_stage,
                        )
                        self.active_unit.execute(
                            work.perform, work.accept_completed,
                        )
                    if entry_completed is not None:
                        entry_completed(entry, work is not None)
                    self._entry_results[key] = TaskEntryResult(
                        status="completed" if work is not None else "skipped",
                        results=self.state.results[self.entry_result_start:],
                        stage="工作及最终核验完成" if work is not None else "固定跳过",
                        work_completed=(
                            self.active_unit is not None
                            and self.active_unit.work_completed
                        ),
                    )
                    entry_returned = True
                except (OSError, ValueError) as error:
                    self._entry_results[key] = TaskEntryResult(
                        status="failed",
                        results=self.state.results[self.entry_result_start:],
                        stage=self._phase,
                        work_completed=(
                            self.active_unit is not None
                            and self.active_unit.work_completed
                        ),
                        error=error,
                    )
                    raise
                finally:
                    if self.active_unit is not None:
                        self.fact_error = self.active_unit.fact_error
                    if (
                        not entry_returned
                        and self._entry_results[key].status == "running"
                    ):
                        # 不捕获程序缺陷；保留步骤和失败生命周期，
                        # 不把它转换成可处理的业务error。
                        self._entry_results[key] = TaskEntryResult(
                            status="failed",
                            results=self.state.results[self.entry_result_start:],
                            stage=self._phase,
                            work_completed=(
                                self.active_unit is not None
                                and self.active_unit.work_completed
                            ),
                        )
                self.active_unit = None
                self.current_entry = None

            if final_check is not None:
                publish_stage("整任务", task_target, "全部工作结束核验")
                final_check()
            self.work_completed = True
            auxiliary_result = auxiliary() if auxiliary is not None else None
            if after_auxiliary_check is not None:
                publish_stage("整任务", task_target, "辅助处理后固定范围核验")
                after_auxiliary_check()
            publish_stage("整任务", None, "completed")
            self._lifecycle = "completed"
            completed = True
            return auxiliary_result
        finally:
            if not completed:
                self._lifecycle = "failed"