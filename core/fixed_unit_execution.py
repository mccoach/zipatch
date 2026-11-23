# -*- coding: utf-8 -*-

"""固定工作单元的唯一顺序、父目录创建和工作完成事实。"""

from core.execution_state import ExecutionState
from core.file_observation import (
    FileObservation, TreeObservation, recheck_file, recheck_tree,
)
from core.directory_creation import fixed_directory_observations
from core.protection_projection import ProtectionUnit
from core.recovery_paths import identity


def verify_fixed_effects(effects, actuals):
    """消费正式对象约束，在单元结束边界核验完整内容状态。"""
    effects = tuple(effects)
    if type(actuals) is not tuple or len(actuals) != len(effects):
        raise RuntimeError("正式动作必须交付对应全部effects的对象约束元组")
    from core.link_paths import (
        LinkPathObservation, link_definition_digest, recheck_link_path,
        specification_from_link,
    )

    for effect, actual in zip(effects, actuals):
        if not isinstance(actual, (
            FileObservation, TreeObservation, LinkPathObservation,
        )):
            raise TypeError("正式成果必须是文件、链接入口或完整目录观察")
        if actual.path != effect.before.path:
            raise RuntimeError("正式成果位置不对应固定effect")
        definitions = ()
        if isinstance(actual, TreeObservation):
            kind, records = "directory", actual.content_records
            definitions = tuple(
                (relative, specification_from_link(member.entry_link))
                for relative, member in actual.links
            )
            recheck_tree(actual)
        elif isinstance(actual, LinkPathObservation):
            if actual.follow_final_link or actual.entry_link is None:
                raise ValueError("正式链接成果必须是不跟随的结构入口")
            kind = actual.kind
            records = (("", kind, link_definition_digest(actual.entry_link)),)
            definitions = (("", specification_from_link(actual.entry_link)),)
            recheck_link_path(actual)
        else:
            kind = actual.kind
            records = (
                (("", "file", actual.digest),) if kind == "file" else ()
            )
            recheck_file(actual)
        if (
            kind != effect.after.kind or records != effect.after.records
            or definitions != effect.after.link_definitions
        ):
            raise OSError(f"正式成果内容、存在性或目录结构不符：{actual.path}")


class FixedUnitExecution:
    """工作完成与事实发布完成分开；报告不反向登记执行事实。"""

    def __init__(self, unit, *, protection, state, recheck, stage):
        if not isinstance(unit, ProtectionUnit):
            raise TypeError("工作单元必须消费ProtectionUnit")
        if not isinstance(state, ExecutionState):
            raise TypeError("工作单元必须消费共同任务ExecutionState")
        if not callable(recheck) or not callable(stage):
            raise TypeError("工作单元缺少明确协调能力")
        self.unit = unit
        self.protection = protection
        self.state = state
        self.recheck = recheck
        self.stage = stage
        self.fact_error = None
        self.work_completed = False
        self.completed = False
        self._started = False
        self._consumed = False

    def _create_parents(self):
        fixed = {
            identity(expected.path): expected
            for effect in self.unit.effects
            for expected in fixed_directory_observations(effect.before)
        }
        if any(
            identity(target) not in fixed
            for target in self.unit.parent_directories
        ):
            raise ValueError("必要父目录没有对应固定不存在观察")
        self.state.directories.ensure_all(
            tuple(fixed[identity(target)] for target in self.unit.parent_directories),
            record_step=self.state.record_step,
            stage=lambda target, phase:
            self.stage("工作目标", target, phase),
            before_step=self.recheck,
        )

    def execute(self, perform, accept_completed):
        if self._consumed:
            raise ValueError("固定工作单元已消费，禁止再次执行")
        if not callable(perform) or not callable(accept_completed):
            raise TypeError("固定动作和接受能力必须可调用")
        self._consumed = True
        start = len(self.state.results)
        target = self.unit.effects[0].before.path
        try:
            self.stage("工作目标", target, "固定工作单元开始前复核")
            self.recheck()
            if self.protection is not None:
                self.stage(
                    "正式保护", self.protection.task_root,
                    "工作改变前started屏障",
                )
                self.protection.begin(self.unit.unit_id)
            self._started = True
            self._create_parents()
            self.stage("工作目标", target, "必要父目录完成后固定前提复核")
            self.recheck()
            self.stage("工作目标", target, "固定工作动作执行")
            actuals = perform()
            self.stage("工作目标", target, "全部具体effect成果结束核验")
            verify_fixed_effects(self.unit.effects, actuals)
            accept_completed(actuals)
            self.work_completed = True
            if self.protection is not None:
                self.stage(
                    "正式保护", self.protection.task_root,
                    "工作完成事实发布",
                )
                self.protection.complete(
                    self.unit.unit_id, self.state.results[start:],
                )
            self.completed = True
            return actuals
        except (OSError, ValueError):
            if (
                self._started and self.protection is not None
                and not self.protection.failed
            ):
                try:
                    self.protection.uncertain(
                        self.unit.unit_id, self.state.results[start:],
                    )
                except (OSError, ValueError) as error:
                    self.fact_error = error
            raise