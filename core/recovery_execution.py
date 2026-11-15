# -*- coding: utf-8 -*-

"""主动恢复和自动回滚共用的固定整包原状态恢复核心。"""

import sys
from dataclasses import dataclass
from pathlib import Path

from core.content_publication import (
    ContentPublicationError, ContentPublicationOptions, PreparedContentArtifact,
    dispose_content_artifact, prepare_copy_content_artifact, publish_content,
)
from core.directory_file_replacement import (
    PreparedDirectoryFileReplacement,
    execute_directory_file_replacement, prepare_directory_file_replacement,
)
from core.file_observation import (
    FileObservation, TreeObservation, ObjectIdentity,
    observe_file, recheck_file, recheck_tree,
)
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult,
    PreparedTreeCopy, PreparedTreeRemoval, _recheck_tree_copy_target,
    execute_filesystem_change, execute_tree_copy, execute_tree_removal,
    prepare_directory_creation, prepare_file_removal,
    prepare_tree_copy, prepare_tree_removal,
)
from core.protection_projection import (
    ContentState, FixedEffect, FixedUnit, build_protection_projection,
    observation_state,
)
from core.recovery_paths import checked_chain, identity, overlaps
from core.recovery_protection import (
    RecoveryEvidence, RecoveryProtectionSession, recheck_recovery_evidence,
)
from core.task_observation import (
    observed_files, project_task_observation, shared_file_states,
)


def matches_original_content(observed, original):
    """仅内容、存在性及目录结构决定恢复；修改时间不单独触发恢复。"""
    if isinstance(observed, TreeObservation):
        kind, records = "directory", observed.content_records
    else:
        kind = observed.kind
        records = (("", "file", observed.digest),) if kind == "file" else ()
    return kind == original["kind"] and records == original["records"]


def _active_results(active):
    if active is None or isinstance(active, PreparedContentArtifact):
        return ()
    if isinstance(active, PreparedTreeCopy):
        return tuple(receipt.change for receipt in active.results)
    if isinstance(active, PreparedTreeRemoval):
        return active.results
    if isinstance(active, PreparedDirectoryFileReplacement):
        if (
            not active._removal_completed
            and active.active_change is active.removal
        ):
            return active.removal.results
        return active.results
    raise TypeError("恢复活动对象必须是正式共享核心")


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
        protection_root=None, protection_task_id=None,
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
        self._original_files = {}
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
            for _, file in observed_files(action.current):
                self._original_files[identity(file.path)] = file
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
        self._results = ()
        self.completed_units = ()
        self.rebuilt_files = ()
        self.skipped_paths = ()
        self.fact_error = None
        self.current = None
        self.phase = "prepared"
        self._lifecycle = "prepared"
        self._active = None
        self._scene = None
        self._active_key = None
        self._finished = {}

        self.protection = (
            RecoveryProtectionSession(
                self.projection, protection_root,
                protection_task_id, "restore",
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
        return (*self._results, *_active_results(self._active))

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
        return self._lifecycle != "prepared"

    @property
    def active_change(self):
        return self._active if self._active is not None else self._scene

    def invalidate(self):
        if self.consumed:
            return False
        self._lifecycle = "cancelled"
        return True

    def _stage(self, target, phase):
        self.current, self.phase = target, phase

    def _result(self, *, completed=False, error=None):
        return RecoveryExecutionResult(
            self.results, self.completed_units, completed, completed,
            self.current, self.phase, self.protection,
            self.rebuilt_files, self.skipped_paths, self.fact_error, error,
        )

    def _removed_links(self):
        counts = {}
        removed_names = set()
        for receipt in self.results:
            removes_name = (
                receipt.succeeded and receipt.kind == "remove_file"
                if isinstance(receipt, FilesystemChangeResult) else
                receipt.succeeded and receipt.mechanism == "atomic_replace"
            )
            if not removes_name:
                continue
            key = identity(receipt.target)
            if key in removed_names:
                continue
            original = self._original_files.get(key)
            if original is not None:
                removed_names.add(key)
                identifier = original.object_identity
                counts[identifier] = counts.get(identifier, 0) + 1
        return counts

    def _created(self):
        created = {}
        for receipt in self.results:
            if (
                isinstance(receipt, FilesystemChangeResult)
                and receipt.succeeded and receipt.kind == "create_directory"
            ):
                if receipt.created_directory is None:
                    raise RuntimeError("正常目录创建缺少正式身份结果")
                created[identity(receipt.target)] = receipt.created_directory
        return created

    def _project(self, observed, *, baseline=None):
        return project_task_observation(
            observed, self._removed_links(), shared_file_states(self._finished),
            baseline=baseline,
        )

    def _check_parents(self, observed, created):
        _, chain = checked_chain(
            observed.path.parent, include_observation=True,
        )
        initial = dict(observed.chain[:-1])
        for path, kind in chain:
            expected = initial[path]
            if expected == kind:
                continue
            if (
                expected == "absent" and kind == "directory"
                and identity(path) in created
            ):
                continue
            raise ValueError(f"恢复必要父目录偏离固定前提：{path}")

    def recheck(self):
        """比较固定来源和任务正常成果，不从后来现场取得新覆盖授权。"""
        recheck_recovery_evidence(self.evidence)
        created = self._created()
        for directory in created.values():
            target, chain = checked_chain(
                directory.path, include_observation=True,
            )
            if chain[-1][1] != "directory":
                raise ValueError("恢复创建目录的存在性变化")
            attributes = target.lstat()
            if ObjectIdentity(attributes.st_dev, attributes.st_ino) != directory.object_identity:
                raise ValueError("恢复创建目录的身份变化")
        for action in self.actions:
            key = identity(action.target)
            self._check_parents(action.current, created)
            if key == self._active_key:
                continue
            finished = self._finished.get(key)
            if finished is None:
                _recheck(self._project(action.current))
            else:
                actual, baseline = finished
                _recheck(self._project(actual, baseline=baseline))

    def _step(self, step):
        try:
            receipt = execute_filesystem_change(step)
        except FilesystemChangeError as error:
            self._results = (*self._results, error.result)
            self._stage(error.result.target, error.result.phase)
            raise
        self._results = (*self._results, receipt)
        return receipt

    def _composite(self, prepared, executor, **keywords):
        self._active = prepared
        completed = False
        try:
            result = executor(prepared, **keywords)
            completed = True
            return result
        finally:
            self._results = (*self._results, *_active_results(prepared))
            self._active = None
            if not completed:
                # 结果已经归档；现场独立保留，不再次贡献步骤结果。
                self._scene = prepared
                self._stage(prepared.current, prepared.phase)

    def _create_parents(self, unit, before):
        for target in unit.parent_directories:
            self._stage(target, "恢复必要父目录创建")
            self.recheck()
            if identity(target) in self._created():
                continue
            number = next(
                number for number, member in enumerate(before.chain)
                if member[0] == target
            )
            expected = FileObservation(
                target, before.chain[:number + 1], "absent",
                None, None, None, None, None, None,
            )
            self._step(prepare_directory_creation(expected))

    def _publish_file(self, before, source, times):
        self._stage(before.path, "原文件完整成果准备")
        artifact = prepare_copy_content_artifact(
            source, before, preserve_target_mode=False,
            options=ContentPublicationOptions(
                durable_publication=True, mtime_ns=times[0][1],
            ),
        )
        self._active = artifact
        completed = False
        try:
            self.recheck()
            self._stage(before.path, "原文件完整内容写回")
            try:
                receipt = publish_content(artifact)
            except ContentPublicationError as error:
                self._results = (*self._results, error.result)
                self._stage(error.result.target, error.result.phase)
                raise
            self._results = (*self._results, receipt)
            actual = observe_file(
                before.path, include_digest=True, include_mtime=True,
            )
            expected_identity = (
                before.object_identity if artifact.mechanism == "in_place"
                else ObjectIdentity(*artifact.identity)
            )
            expected_links = (
                before.link_count if artifact.mechanism == "in_place" else 1
            )
            if (
                actual.kind != "file"
                or actual.object_identity != expected_identity
                or actual.link_count != expected_links
                or actual.digest != artifact.digest
                or actual.mtime_ns != times[0][1]
            ):
                raise OSError("恢复文件身份、完整内容或适用时间不符")
            if before.kind == "absent":
                self.rebuilt_files = (*self.rebuilt_files, before.path)
            completed = True
            return actual
        finally:
            if not completed:
                self._scene = artifact
            self._active = None
            if artifact._lifecycle not in ("completed", "disposed"):
                if not (
                    artifact.mechanism == "in_place"
                    and artifact._lifecycle == "attempted"
                ):
                    try:
                        dispose_content_artifact(artifact)
                    except (OSError, ValueError) as cleanup_error:
                        print(
                            f"[恢复暂存清理失败] {artifact.path}：{cleanup_error}",
                            file=sys.stderr,
                        )

    def _restore(self, action, before, unit):
        entry = action.original
        original = entry["original"]
        source = self.evidence.materials.get(entry["id"])
        times = entry["file_times"]
        self._create_parents(unit, before)
        self._active_key = identity(action.target)
        try:
            if original["kind"] == "absent":
                if isinstance(before, TreeObservation):
                    self._composite(
                        prepare_tree_removal(before), execute_tree_removal,
                        before_step=self.recheck,
                    )
                else:
                    self.recheck()
                    self._step(prepare_file_removal(before))
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
                    self._composite(
                        prepared, execute_directory_file_replacement,
                        recheck_context=self.recheck,
                    )
                    actual = observe_file(
                        action.target, include_digest=True, include_mtime=True,
                    )
                    if (
                        actual.kind != "file" or actual.link_count != 1
                        or actual.object_identity
                        != ObjectIdentity(*prepared.artifact.identity)
                    ):
                        raise OSError("目录转文件恢复身份不符")
                    self.rebuilt_files = (*self.rebuilt_files, action.target)
                    return actual
                return self._publish_file(before, source, times)
            if isinstance(before, TreeObservation):
                self._composite(
                    prepare_tree_removal(before), execute_tree_removal,
                    before_step=self.recheck,
                )
            elif before.kind == "file":
                self.recheck()
                self._step(prepare_file_removal(before))
            copy = prepare_tree_copy(
                source, _absent(before),
                durable_publication=True, file_times=times,
            )
            self._composite(
                copy, execute_tree_copy, recheck_context=self.recheck,
            )
            actual = _recheck_tree_copy_target(copy)
            self.rebuilt_files = (
                *self.rebuilt_files, *(file.path for _, file in actual.files),
            )
            return actual
        finally:
            self._active_key = None

    def _check_final_originals(self):
        self.recheck()
        for action in self.actions:
            actual, baseline = self._finished[identity(action.target)]
            final = self._project(actual, baseline=baseline)
            if not matches_original_content(final, action.original["original"]):
                raise OSError(f"全部原状态最终检查不符：{action.target}")

    def execute(self):
        if self.consumed:
            raise ValueError("恢复任务已经执行或取消")
        self._lifecycle = "attempted"
        active_unit = None
        unit_start = 0
        try:
            self._stage(self.evidence.task_root, "恢复开始前全部固定前提检查")
            self.recheck()
            if self.protection is not None:
                self._stage(self.protection.task_root, "全部必要还原前保护屏障")
                self.protection.protect_all()
                self.recheck()
            for action in self.actions:
                self._stage(action.target, "恢复对象开始前固定前提检查")
                self.recheck()
                before = self._project(action.current)
                if matches_original_content(before, action.original["original"]):
                    self.skipped_paths = (*self.skipped_paths, action.target)
                    self._finished[identity(action.target)] = (
                        before, dict(self._removed_links()),
                    )
                    continue
                unit = self._units.get(action.original["id"])
                if unit is None:
                    raise ValueError("无需恢复对象偏离原状态，禁止扩大恢复范围")
                active_unit = unit
                unit_start = len(self._results)
                if self.protection is not None:
                    self._stage(self.protection.task_root, "工作改变前started屏障")
                    self.protection.begin(unit.unit_id)
                self._stage(action.target, "恢复完整原对象")
                actual = self._restore(action, before, unit)
                if not matches_original_content(actual, action.original["original"]):
                    raise OSError(f"原对象恢复内容不符：{action.target}")
                self._finished[identity(action.target)] = (
                    actual, dict(self._removed_links()),
                )
                self.recheck()
                self.completed_units = (*self.completed_units, action.original["id"])
                if self.protection is not None:
                    self._stage(self.protection.task_root, "工作完成事实发布")
                    self.protection.complete(
                        unit.unit_id, self._results[unit_start:],
                    )
                active_unit = None
            self._stage(self.evidence.task_root, "整包原状态最终检查")
            self._check_final_originals()
        except (OSError, ValueError) as error:
            if (
                active_unit is not None and self.protection is not None
                and not self.protection.failed
                and self.protection.document is not None
                and any(
                    member["id"] == active_unit.unit_id
                    and member["fact"]["state"] == "started"
                    for member in self.protection.document.value["units"]
                )
            ):
                try:
                    self.protection.uncertain(
                        active_unit.unit_id, self._results[unit_start:],
                    )
                except (OSError, ValueError) as fact_error:
                    self.fact_error = fact_error
            raise RecoveryExecutionError(
                self._result(error=error), self,
            ) from error
        self._lifecycle = "completed"
        self._stage(None, "completed")
        return self._result(completed=True)
