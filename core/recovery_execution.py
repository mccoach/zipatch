# -*- coding: utf-8 -*-

"""主动恢复和自动回滚共用的固定整包原状态恢复核心。"""

from dataclasses import dataclass
from pathlib import Path

from core.content_publication import (
    ContentPublicationOptions, ContentPublicationResult,
)
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
from core.recovery_document import content_state_from_value
from core.internal_materials import validate_material_state
from core.link_paths import (
    LinkPathObservation, link_definition_digest, recheck_link_path,
    specification_from_link,
)
from core.filesystem_changes import (
    prepare_directory_creation, prepare_link_creation, prepare_link_removal,
)
from core.recovery_protection import (
    RecoveryEvidence, RecoveryProtectionSession, recheck_recovery_evidence,
)
from core.task_observation import TaskObservationState
from core.fixed_task_execution import FixedTaskExecution, FixedTaskWork


def matches_original_content(observed, original):
    """仅内容、存在性及目录结构决定恢复；修改时间不单独触发恢复。"""
    if isinstance(observed, TreeObservation):
        kind, records = "directory", observed.content_records
        definitions = tuple(
            (relative, specification_from_link(member.entry_link))
            for relative, member in observed.links
        )
    elif isinstance(observed, LinkPathObservation):
        if observed.follow_final_link or observed.entry_link is None:
            raise ValueError("恢复内容比较要求不跟随的结构入口")
        kind = observed.kind
        records = (("", kind, link_definition_digest(observed.entry_link)),)
        definitions = (("", specification_from_link(observed.entry_link)),)
    else:
        kind = observed.kind
        records = (("", "file", observed.digest),) if kind == "file" else ()
        definitions = ()
    state = content_state_from_value(original, target=observed.path)
    return (
        kind == state.kind and records == state.records
        and definitions == state.link_definitions
    )



def _absent(observed):
    return FileObservation(
        observed.path,
        (*observed.chain[:-1], (observed.path, "absent")),
        "absent", None, None, None, None, None, None,
    )


def _recheck(observed):
    if isinstance(observed, LinkPathObservation):
        return recheck_link_path(observed)
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
        recheck_context=None, selected_object_ids=None,
    ):
        if recheck_context is not None and not callable(recheck_context):
            raise TypeError("恢复所属固定依赖复核必须是可调用对象")
        self.recheck_context = recheck_context
        if not isinstance(evidence, RecoveryEvidence):
            raise TypeError("恢复执行必须消费正式恢复依据")
        if (protection_root is None) != (protection_task_id is None):
            raise ValueError("保护位置和标识必须同时提供")
        self.evidence = evidence
        self.actions = tuple(actions)
        entries = evidence.document.value["objects"]
        if selected_object_ids is None:
            selected_object_ids = tuple(entry["id"] for entry in entries)
        if (
            type(selected_object_ids) is not tuple
            or any(type(value) is not str for value in selected_object_ids)
            or len(set(selected_object_ids)) != len(selected_object_ids)
        ):
            raise ValueError("恢复选择必须是唯一清单对象编号元组")
        selected = set(selected_object_ids)
        selected_entries = tuple(entry for entry in entries if entry["id"] in selected)
        if (
            tuple(entry["id"] for entry in selected_entries) != selected_object_ids
            or tuple(action.original for action in self.actions) != selected_entries
        ):
            raise ValueError("恢复动作必须准确对应原清单顺序下的明确所选对象")
        self.complete_selection = len(selected_entries) == len(entries)
        self.observation_state = TaskObservationState(
            action.current for action in self.actions
        )
        units = []
        shared_desired = {}
        for action in self.actions:
            access = action.access
            if (
                not isinstance(access, LinkPathObservation)
                or access.follow_final_link
                or identity(access.nominal_path)
                != identity(action.original["target"])
                or identity(action.target) != identity(access.actual_path)
                or overlaps(action.target, evidence.task_root)
            ):
                raise ValueError("恢复实际位置不对应固定名义访问或与源备份重叠")
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
                        content_state_from_value(
                            original, target=action.target,
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
            from core.recovery_paths import contains

            for action in self.actions:
                access = action.access
                if any(
                    overlaps(internal, definition.entry_path)
                    for definition in access.followed_links
                ) or any(
                    contains(internal, directory.path)
                    for directory in access.directories
                    if not contains(access.actual_path, directory.path)
                ):
                    raise ValueError("还原前保护创建范围会改变固定名义访问依赖")

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
            self.results, self.completed_units, completed,
            completed and self.complete_selection,
            self.current, self.phase, self.protection,
            self.rebuilt_files, self.skipped_paths, self.fact_error, error,
        )

    def _project(self, observed, *, baseline=None):
        return self.observation_state.project(
            observed, self.results, baseline=baseline,
        )

    def recheck_access_dependencies(self):
        """只比较原名义访问关系，不访问新指向或授予新工作范围。"""
        from core.link_paths import recheck_path_dependencies

        seen = set()
        for action in self.actions:
            access = action.access
            key = (
                identity(access.nominal_path),
                access.follow_final_link,
            )
            if key in seen:
                continue
            seen.add(key)
            recheck_path_dependencies(access)

    def recheck(self):
        """固定来源和联合业务范围比较，不从现场取得新授权。"""
        if self.recheck_context is not None:
            self.recheck_context()
        self.recheck_access_dependencies()
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
            desired = content_state_from_value(
                original, target=action.target,
            )
            if desired.link_definitions:
                # 材料先完整对应原结构，再允许任何现场移除。
                validate_material_state(
                    source, desired, target=action.target,
                )
                missing = _absent(before)
                self._remove_current_structure(before)
                actual = _rebuild_structure(
                    source, missing, desired, times,
                    state=self.task_execution.state,
                    recheck_context=self.recheck, stage=self._stage,
                )
                if isinstance(actual, TreeObservation):
                    self.rebuilt_files = (
                        *self.rebuilt_files,
                        *(member.path for _, member in actual.files),
                    )
                return actual
            if isinstance(before, LinkPathObservation):
                missing = _absent(before)
                self._remove_current_structure(before)
                before = missing
                if desired.kind == "absent":
                    recheck_file(before)
                    return before
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

    def _remove_current_structure(self, before):
        state = self.task_execution.state
        self.recheck()
        if isinstance(before, TreeObservation):
            state.execute_active(
                prepare_tree_removal(before), execute_tree_removal,
                stage=self._stage, before_step=self.recheck,
            )
        elif isinstance(before, LinkPathObservation):
            state.execute_step(
                prepare_link_removal(before), stage=self._stage,
            )
        elif before.kind == "file":
            state.execute_step(
                prepare_file_removal(before), stage=self._stage,
            )
        elif before.kind != "absent":
            raise ValueError("恢复移除遇到未知固定结构类型")

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


def _rebuild_structure(
    source, expected, desired, file_times, *,
    state, recheck_context, stage,
):
    """恢复任务内部的固定结构重建，不取得新业务授权。

    普通材料不伪装成活动源链接。链接定义直接消费已校验状态；
    文件正文消费首次材料bytes。现场范围只来自固定清单，
    允许存在的对象身份只来自本次正式正常结果及自有暂存。
    """
    from core.atomic_file import sync_directory
    from core.execution_state import ExecutionState
    from core.file_observation import observe_tree

    if not isinstance(state, ExecutionState):
        raise TypeError("结构恢复必须使用所属正式工作账本")
    if not callable(recheck_context) or not callable(stage):
        raise TypeError("结构恢复必须具有固定范围复核及阶段能力")
    if not isinstance(expected, FileObservation) or expected.kind != "absent":
        raise ValueError("结构重建只消费固定不存在入口")
    validate_material_state(source, desired, target=expected.path)
    if not desired.link_definitions:
        raise ValueError("链接结构重建必须具有完整定义")
    if overlaps(source.path, expected.path):
        raise ValueError("恢复材料与固定目标重叠")
    times = tuple(file_times)
    if (
        tuple(relative for relative, _ in times)
        != tuple(relative for relative, kind, _ in desired.records if kind == "file")
        or any(type(value) is not int for _, value in times)
    ):
        raise ValueError("恢复正式时间没有准确覆盖原普通文件")
    ordinary = (
        dict(source.files) if isinstance(source, TreeObservation)
        else {"": source}
    )
    for relative, kind, digest in desired.records:
        if kind == "file":
            member = ordinary[relative]
            if (
                not isinstance(member.source_bytes, bytes)
                or member.digest != digest
                or type(member.mtime_ns) is not int
            ):
                raise ValueError("恢复普通材料缺少固定完整正文及正式来源时间")
    definitions = dict(desired.link_definitions)
    start = len(state.results)
    content_execution = None

    def check():
        recheck_context()
        if isinstance(source, TreeObservation):
            recheck_tree(source, check_mtime=True)
        else:
            recheck_file(source, check_mtime=True)
        directories, files, links = {}, {}, {}
        for receipt in state.results[start:]:
            if not receipt.succeeded:
                raise ValueError("结构重建只接受本次真实正常前缀")
            if isinstance(receipt, ContentPublicationResult):
                actual = receipt.observation
                if not isinstance(actual, FileObservation):
                    raise RuntimeError("正常文件步骤缺少可靠成果")
                files[actual.path.relative_to(expected.path).as_posix()] = actual
            elif receipt.kind == "create_directory":
                actual = receipt.created_directory
                relative = receipt.target.relative_to(expected.path).as_posix()
                directories["" if relative == "." else relative] = actual
            elif receipt.kind == "create_link":
                actual = receipt.created_link
                if not isinstance(actual, LinkPathObservation):
                    raise RuntimeError("正常链接步骤缺少可靠成果")
                relative = receipt.target.relative_to(expected.path).as_posix()
                links["" if relative == "." else relative] = actual
            else:
                raise ValueError("结构重建遇到不属于本次固定生成的步骤")

        if desired.kind != "directory":
            if not links:
                recheck_file(expected)
                return None
            if set(links) != {""} or directories or files:
                raise ValueError("单入口恢复成果范围非法")
            return recheck_link_path(links[""])

        if not directories:
            recheck_file(expected)
            return None
        artifact = (
            None if content_execution is None else content_execution.artifact
        )
        staged = None
        if artifact is not None and artifact._lifecycle == "prepared":
            relative = artifact.path.relative_to(expected.path).as_posix()
            if relative in files or relative in directories or relative in links:
                raise ValueError("恢复暂存位置与固定正常成果冲突")
            staged = (relative, artifact)
        actual = observe_tree(
            expected.path, include_mtime=True, include_links=True,
        )
        required_files = set(files)
        if staged is not None:
            required_files.add(staged[0])
        if (
            set(dict(actual.directories)) != set(directories)
            or set(dict(actual.files)) != required_files
            or set(dict(actual.links)) != set(links)
        ):
            raise ValueError("结构恢复现场偏离实际成功前缀")
        for relative, member in actual.directories:
            if member.object_identity != directories[relative].object_identity:
                raise ValueError("结构恢复目录身份变化")
        for relative, member in actual.files:
            if staged is not None and relative == staged[0]:
                artifact = staged[1]
                if (
                    (member.object_identity.device, member.object_identity.inode)
                    != artifact.identity
                    or member.digest != artifact.digest
                    or member.link_count != 1
                ):
                    raise ValueError("结构恢复完整自有暂存变化")
            else:
                fixed = files[relative]
                if (
                    member.object_identity != fixed.object_identity
                    or member.digest != fixed.digest
                    or member.link_count != fixed.link_count
                    or member.mtime_ns != fixed.mtime_ns
                ):
                    raise ValueError("结构恢复普通文件成果变化")
        for relative, member in actual.links:
            if member != links[relative]:
                raise ValueError("结构恢复链接定义或入口身份变化")
        return actual

    check()
    if desired.kind == "directory":
        directories = [
            relative for relative, kind, _ in desired.records
            if kind == "directory"
        ]
        for relative in sorted(
            directories,
            key=lambda position: (
                len(Path(position).parts), position.encode("utf-8"),
            ),
        ):
            stage(expected.path / relative, "固定恢复普通目录创建")
            check()
            fixed = expected if not relative else observe_file(
                expected.path / relative,
            )
            if fixed.kind != "absent":
                raise ValueError("固定恢复目录位置已经出现")
            state.execute_step(
                prepare_directory_creation(fixed), stage=stage,
            )
            check()

    time_map = dict(times)
    for relative, kind, digest in desired.records:
        if kind == "directory":
            continue
        target = expected.path / relative
        stage(target, "固定恢复入口或正文准备")
        check()
        fixed = expected if not relative else observe_file(target)
        if fixed.kind != "absent":
            raise ValueError("固定恢复成员位置已经出现")
        if relative in definitions:
            step = prepare_link_creation(fixed, definitions[relative])
            check()
            state.execute_step(step, stage=stage)
        else:
            content_execution = FixedContentExecution(
                fixed, source=ordinary[relative],
                preserve_target_mode=False, desired_digest=digest,
                options=ContentPublicationOptions(
                    durable_publication=True, mtime_ns=time_map[relative],
                ),
            )
            state.execute_active(
                content_execution,
                lambda prepared: prepared.execute(
                    recheck_context=check,
                    record_step=state.record_step, stage=stage,
                ),
                stage=stage,
            )
            content_execution = None
        check()

    stage(expected.path, "固定原结构完整恢复核验")
    actual = check()
    effect = FixedEffect(expected, desired)
    from core.fixed_unit_execution import verify_fixed_effects
    verify_fixed_effects((effect,), (actual,))
    if isinstance(actual, TreeObservation) and actual.file_times != times:
        raise OSError("结构恢复普通文件正式时间不符")
    sync_directory(expected.path.parent)
    return actual
