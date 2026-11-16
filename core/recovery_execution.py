# -*- coding: utf-8 -*-

"""主动恢复和自动回滚共用的固定整包原状态恢复核心。"""

from dataclasses import dataclass
from pathlib import Path

from core.content_publication import ContentPublicationOptions
from core.fixed_content_execution import FixedContentExecution
from core.directory_file_replacement import (
    execute_directory_file_replacement, prepare_directory_file_replacement,
)
from core.file_observation import (
    FileObservation, TreeObservation,
    observe_file, recheck_file, recheck_tree,
)
from core.filesystem_changes import (
    execute_tree_copy, execute_tree_removal, prepare_file_removal,
    prepare_tree_copy, prepare_tree_removal,
)
from core.protection_projection import (
    ContentState, FixedEffect, FixedUnit, build_protection_projection,
    observation_state,
)
from core.recovery_paths import identity, overlaps
from core.recovery_protection import (
    RecoveryEvidence, RecoveryProtectionSession, recheck_recovery_evidence,
)
from core.task_observation import TaskObservationState
from core.fixed_task_execution import FixedTaskExecution, FixedTaskWork


def matches_original_content(observed, original):
    """仅内容、存在性及目录结构决定恢复；修改时间不单独触发恢复。"""
    if isinstance(observed, TreeObservation):
        kind, records = "directory", observed.content_records
    else:
        kind = observed.kind
        records = (("", "file", observed.digest),) if kind == "file" else ()
    return kind == original["kind"] and records == original["records"]



def _absent(observed):
    return FileObservation(
        observed.path,
        (*observed.chain[:-1], (observed.path, "absent")),
        "absent", None, None, None, None, None, None,
    )


def _recheck(observed):
    if isinstance(observed, TreeObservation):
        return recheck_tree(observed)
    return recheck_file(observed)


@dataclass(frozen=True)
class RecoveryExecutionResult:
    results: tuple
    completed_units: tuple
    completed: bool
    complete_original_state: bool
    current: Path | None
    phase: str
    protection: object
    rebuilt_files: tuple = ()
    skipped_paths: tuple = ()
    fact_error: OSError | ValueError | None = None
    error: OSError | ValueError | None = None

    @property
    def succeeded(self):
        return self.error is None and self.completed and self.current is None


class RecoveryExecutionError(OSError):
    def __init__(self, result, execution):
        self.result = result
        self.execution = execution
        super().__init__(
            f"原状态恢复未完整完成：{result.current}\n"
            f"阶段：{result.phase}\n"
            f"已恢复对象：{len(result.completed_units)}；"
            f"跳过未变对象：{len(result.skipped_paths)}。\n"
            "此前恢复步骤保留，不自动重试，原备份保留。\n"
            f"{result.error}"
            + (
                f"\n恢复事实保存也失败：{result.fact_error}"
                if result.fact_error is not None else ""
            )
        )


class RecoveryExecution:
    """固定初始观察，正常步骤投影及完整结束检查。

    不重新观察并授权失败现场，不重放原OP。
    硬链接正常联动只由已完成成果投影到剩余固定观察。
    """

    def __init__(
        self, evidence, actions, *,
        protection_root=None, protection_task_id=None, material_base=None,
    ):
        if not isinstance(evidence, RecoveryEvidence):
            raise TypeError("恢复执行必须消费正式恢复依据")
        if (protection_root is None) != (protection_task_id is None):
            raise ValueError("保护位置和标识必须同时提供")
        self.evidence = evidence
        self.actions = tuple(actions)
        entries = evidence.document.value["objects"]
        if tuple(action.original for action in self.actions) != entries:
            raise ValueError("整包恢复必须准确覆盖原清单全部对象")
        self.observation_state = TaskObservationState(
            action.current for action in self.actions
        )
        units = []
        shared_desired = {}
        for action in self.actions:
            if (
                identity(action.target) != identity(action.original["target"])
                or overlaps(action.target, evidence.task_root)
            ):
                raise ValueError("恢复目标位置不对应原清单或与源备份重叠")
            state, _ = observation_state(action.current)
            original = action.original["original"]
            expected_execute = not matches_original_content(action.current, original)
            if action.execute != expected_execute:
                raise ValueError("恢复动作与固定内容比较结果矛盾")

            if (
                isinstance(action.current, FileObservation)
                and action.current.kind == "file"
                and original["kind"] == "file"
            ):
                identifier = action.current.object_identity
                digest = original["records"][0][2]
                previous = shared_desired.setdefault(identifier, digest)
                if previous != digest:
                    raise ValueError(
                        "当前关联硬链接名称要求恢复为不同内容，"
                        "保持现有硬链接关系时无法同时满足"
                    )
            if action.execute:
                units.append(FixedUnit(
                    (action.original["id"],),
                    (FixedEffect(
                        action.current,
                        ContentState(
                            original["kind"], original["records"],
                            original["integrity"],
                        ),
                    ),),
                ))
        self.projection = build_protection_projection(
            tuple(units), forbidden_roots=(),
        )
        self._units = {
            unit.members[0]: unit for unit in self.projection.units
        }


        self.rebuilt_files = ()
        self.skipped_paths = ()
        self.fact_error = None
        self.current = None
        self.phase = "prepared"
        self.task_execution = FixedTaskExecution()
        self._active_key = None

        self.protection = (
            RecoveryProtectionSession(
                self.projection, protection_root,
                protection_task_id, "restore", material_base=material_base,
            )
            if protection_root is not None and self.projection.units else None
        )
        if self.protection is not None:
            missing = tuple(
                path for path, kind in self.protection._task_expected.chain
                if kind == "absent"
            )
            internal = missing[0] if missing else protection_root
            if overlaps(internal, evidence.task_root) or any(
                overlaps(internal, action.target) for action in self.actions
            ):
                raise ValueError("还原前保护与来源或恢复对象重叠")

    @property
    def results(self):
        return self.task_execution.state.results

    @property
    def completed_units(self):
        return tuple(
            action.original["id"] for action in self.actions
            if self.task_execution.entry_result(
                action.original["id"],
            ).work_completed
        )

    @property
    def backup_completed(self):
        return self.protection is not None and self.protection.backup_completed

    @property
    def backup_cleanup_report(self):
        return (
            self.protection.backup_cleanup_report
            if self.protection is not None else ""
        )

    @property
    def consumed(self):
        return self.task_execution.consumed

    @property
    def active_change(self):
        return self.task_execution.state.active_change

    def invalidate(self):
        return self.task_execution.invalidate()

    def _stage(self, target, phase):
        self.current, self.phase = target, phase

    def _result(self, *, completed=False, error=None):
        return RecoveryExecutionResult(
            self.results, self.completed_units, completed, completed,
            self.current, self.phase, self.protection,
            self.rebuilt_files, self.skipped_paths, self.fact_error, error,
        )

    def _project(self, observed, *, baseline=None):
        return self.observation_state.project(
            observed, self.results, baseline=baseline,
        )

    def recheck(self):
        """固定来源和联合业务范围比较，不从现场取得新授权。"""
        recheck_recovery_evidence(self.evidence)
        state = self.task_execution.state
        state.recheck_created_directories()
        for action in self.actions:
            key = identity(action.target)
            state.recheck_parents(action.current)
            if key == self._active_key:
                continue
            _recheck(self.observation_state.expected(
                action.current, self.results,
            ))

    def _publish_file(self, before, source, times):
        execution = FixedContentExecution(
            before, source=source, preserve_target_mode=False,
            desired_digest=source.digest,
            options=ContentPublicationOptions(
                durable_publication=True, mtime_ns=times[0][1],
            ),
        )
        state = self.task_execution.state
        actual = state.execute_active(
            execution,
            lambda prepared: prepared.execute(
                recheck_context=self.recheck,
                record_step=state.record_step, stage=self._stage,
            ),
            stage=self._stage,
        )
        if before.kind == "absent":
            self.rebuilt_files = (*self.rebuilt_files, before.path)
        return actual

    def _restore(self, action, before):
        entry = action.original
        original = entry["original"]
        source = self.evidence.materials.get(entry["id"])
        times = entry["file_times"]
        self._active_key = identity(action.target)
        try:
            if original["kind"] == "absent":
                if isinstance(before, TreeObservation):
                    self.task_execution.state.execute_active(
                        prepare_tree_removal(before), execute_tree_removal,
                        stage=self._stage, before_step=self.recheck,
                    )
                else:
                    self.recheck()
                    self.task_execution.state.execute_step(
                        prepare_file_removal(before), stage=self._stage,
                    )
                return observe_file(action.target)
            if original["kind"] == "file":
                if isinstance(before, TreeObservation):
                    effect = FixedEffect(
                        before, ContentState(
                            original["kind"], original["records"],
                            original["integrity"],
                        ),
                    )
                    prepared = prepare_directory_file_replacement(
                        effect, source, mtime_ns=times[0][1],
                        durable_publication=True,
                    )
                    self.task_execution.state.execute_active(
                        prepared, execute_directory_file_replacement,
                        stage=self._stage, recheck_context=self.recheck,
                    )
                    actual = prepared.final_observation
                    if not isinstance(actual, FileObservation):
                        raise RuntimeError("正常目录转文件缺少最终成果观察")
                    self.rebuilt_files = (*self.rebuilt_files, action.target)
                    return actual
                return self._publish_file(before, source, times)
            if isinstance(before, TreeObservation):
                self.task_execution.state.execute_active(
                    prepare_tree_removal(before), execute_tree_removal,
                    stage=self._stage, before_step=self.recheck,
                )
            elif before.kind == "file":
                self.recheck()
                self.task_execution.state.execute_step(
                        prepare_file_removal(before), stage=self._stage,
                    )
            copy = prepare_tree_copy(
                source, _absent(before),
                durable_publication=True, file_times=times,
            )
            self.task_execution.state.execute_active(
                copy, execute_tree_copy,
                stage=self._stage, recheck_context=self.recheck,
            )
            actual = copy.final_observation
            if not isinstance(actual, TreeObservation):
                raise RuntimeError("正常恢复树复制缺少最终成果观察")
            self.rebuilt_files = (
                *self.rebuilt_files, *(file.path for _, file in actual.files),
            )
            return actual
        finally:
            self._active_key = None

    def _check_final_originals(self):
        self.recheck()
        for action in self.actions:
            actual, baseline = self.observation_state.finished[identity(action.target)]
            final = self._project(actual, baseline=baseline)
            if not matches_original_content(final, action.original["original"]):
                raise OSError(f"全部原状态最终检查不符：{action.target}")

    def execute(self):
        if self.consumed:
            raise ValueError("恢复任务已经执行或取消")

        def prepare_entry(action):
            self._stage(action.target, "恢复对象开始前固定前提检查")
            self.recheck()
            before = self._project(action.current)
            if matches_original_content(before, action.original["original"]):
                self.skipped_paths = (*self.skipped_paths, action.target)
                self.observation_state.accept((before,), self.results)
                return None
            unit = self._units.get(action.original["id"])
            if unit is None:
                raise ValueError("无需恢复对象偏离原状态，禁止扩大恢复范围")

            def accept_completed(actuals):
                self.observation_state.accept(actuals, self.results)
                self.recheck()


            return FixedTaskWork(
                unit,
                lambda: (self._restore(action, before),),
                accept_completed, self.recheck,
            )


        try:
            self.task_execution.execute(
                self.actions, protection=self.protection,
                initial_recheck=self.recheck, prepare_entry=prepare_entry,
                entry_key=lambda action: action.original["id"],
                stage=lambda role, target, phase: self._stage(target, phase),
                final_check=self._check_final_originals,
                task_target=self.evidence.task_root,
            )
        except (OSError, ValueError) as error:
            self.fact_error = self.task_execution.fact_error
            raise RecoveryExecutionError(
                self._result(error=error), self,
            ) from error
        return self._result(completed=True)
