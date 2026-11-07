# -*- coding: utf-8 -*-

"""严格原状态的固定整任务恢复协调。

输入解释、选择和用户确认归正式消费者。
本核心消费最终effects及完整保留观察，直接组织已有变更和
正式保护。不重放OP，不读取旧格式，不重新构建工作正文。
"""

import sys
from dataclasses import dataclass, replace
from pathlib import Path

from core.content_publication import (
    ContentPublicationError, ContentPublicationOptions,
    PreparedContentArtifact, _recheck_copy_source,
    dispose_content_artifact, prepare_copy_content_artifact, publish_content,
)
from core.directory_file_replacement import (
    PreparedDirectoryFileReplacement,
    execute_directory_file_replacement, prepare_directory_file_replacement,
)
from core.file_observation import (
    DirectoryObservation, FileObservation, ObjectIdentity, TreeObservation,
    observe_file, recheck_file, recheck_tree,
)
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult, PreparedTreeCopy,
    PreparedTreeRemoval, _recheck_tree_copy_target,
    execute_filesystem_change, execute_tree_copy, execute_tree_removal,
    prepare_directory_creation, prepare_file_removal,
    prepare_tree_copy, prepare_tree_removal,
)
from core.protection_projection import (
    ContentState, build_protection_projection, observation_state,
    validate_absolute_path,
)
from core.recovery_content import content_integrity
from core.recovery_paths import checked_chain, contains, identity, overlaps
from core.recovery_protection import (
    RecoveryEvidence, RecoveryProtectionSession, recheck_recovery_evidence,
)


@dataclass(frozen=True)
class RecoveryExecutionResult:
    results: tuple
    completed_units: tuple
    completed: bool
    complete_original_state: bool
    current: Path | None
    phase: str
    protection: RecoveryProtectionSession | None
    fact_error: OSError | ValueError | None = None
    error: OSError | ValueError | None = None

    @property
    def succeeded(self):
        return self.error is None and self.completed and self.current is None


class RecoveryExecutionError(OSError):
    """主要工作失败和附属事实失败分别持有。"""

    def __init__(self, result, execution):
        self.result = result
        self.execution = execution
        super().__init__(
            f"固定恢复任务未完整结束：{result.current}\n"
            f"阶段：{result.phase}\n"
            f"工作正常完成单元：{len(result.completed_units)}；"
            f"真实步骤：{len(result.results)}。\n"
            "此前步骤不自动撤销；完成事实保存失败不证明工作没有发生。"
            "任务资格已消费，停止后续，不自动重试、回滚或删除正式依据。\n"
            f"{result.error}"
            + (
                f"\n不确定事实补充也未保存：{result.fact_error}"
                if result.fact_error is not None else ""
            )
        )


def _entry_state(entry):
    state = entry["original"]
    return ContentState(state["kind"], state["records"], state["integrity"])


def _files(observed):
    if isinstance(observed, TreeObservation):
        return observed.files
    return (("", observed),) if observed.kind == "file" else ()


def _same_file_facts(left, right):
    return (
        left.path == right.path and left.kind == right.kind
        and left.object_identity == right.object_identity
        and left.link_count == right.link_count
        and left.source_bytes == right.source_bytes
        and left.digest == right.digest
        and left.mtime_ns == right.mtime_ns
    )


def _active_results(active):
    if active is None or isinstance(active, PreparedContentArtifact):
        return ()
    if isinstance(active, PreparedTreeCopy):
        return tuple(member.change for member in active.results)
    if isinstance(active, PreparedTreeRemoval):
        return active.results
    if isinstance(active, PreparedDirectoryFileReplacement):
        if (
            not active._removal_completed
            and active.active_change is active.removal
        ):
            return active.removal.results
        return active.results
    raise TypeError("活动恢复能力不是正式子核心")


def _absent(observed):
    return FileObservation(
        observed.path,
        (*observed.chain[:-1], (observed.path, "absent")),
        "absent", None, None, None, None, None, None,
    )


def _adjust_links(observed, counts, previous=None):
    previous = {} if previous is None else previous

    def adjusted(member):
        if member.kind != "file":
            return member
        identifier = member.object_identity
        delta = counts.get(identifier, 0) - previous.get(identifier, 0)
        links = member.link_count - delta
        if links < 1:
            raise ValueError("正常名称移除形成非法剩余链接数量")
        return replace(member, link_count=links)

    if isinstance(observed, FileObservation):
        return adjusted(observed)
    return replace(
        observed,
        files=tuple(
            (relative, adjusted(member)) for relative, member in observed.files
        ),
    )


def _recheck(observed, check_time):
    if isinstance(observed, TreeObservation):
        return recheck_tree(observed, check_mtime=check_time)
    return recheck_file(
        observed, check_mtime=check_time and observed.kind == "file",
    )


class RecoveryExecution:
    """唯一投影、真实步骤前缀和一次资格；不拥有GUI确认。

    kept允许原不存在目录的完整当前观察及其中固定删除effects。
    unchanged必须已经满足严格原内容及全部正式文件时间。
    """

    def __init__(
        self, evidence, units, *, kept=(), unchanged=(), forbidden_roots=(),
        protection_root=None, protection_task_id=None,
        durable_publication=True,
    ):
        if not isinstance(evidence, RecoveryEvidence):
            raise TypeError("恢复必须消费RecoveryEvidence")
        if type(durable_publication) is not bool:
            raise TypeError("恢复确认开关必须是布尔值")
        if (protection_root is None) != (protection_task_id is None):
            raise ValueError("保护任务位置和标识必须同时明确")

        self.evidence = evidence
        self.projection = build_protection_projection(
            units, forbidden_roots=tuple(forbidden_roots),
        )
        self.kept = tuple(kept)
        self.unchanged = tuple(unchanged)
        self.durable_publication = durable_publication
        self._bindings = {}
        self._original_files = {}
        self._results = ()
        self._completed_units = ()
        self._finished = {}
        self._active = None
        self._scene = None
        self._active_effect = None
        self._fact_error = None
        self._lifecycle = "prepared"
        self.current = None
        self.phase = "prepared"
        self._validate_scope()

        if protection_root is not None:
            validate_absolute_path(protection_root)
            if overlaps(protection_root, evidence.task_root):
                raise ValueError("还原前保护与源恢复依据重叠")
            for observed in (*self.kept, *self.unchanged):
                if overlaps(protection_root, observed.path):
                    raise ValueError("还原前保护与保留或无需操作范围重叠")

        self.protection = (
            RecoveryProtectionSession(
                self.projection, protection_root, protection_task_id,
                "restore", durable_publication=durable_publication,
            )
            if protection_root is not None and self.projection.units else None
        )
        if self.protection is not None:
            # 材料任务的缺失祖先也是实际内部创建范围。
            # 不能只检查末级task_root而改变保留或无需操作的父链。
            missing = tuple(
                path for path, kind in self.protection._task_expected.chain
                if kind == "absent"
            )
            if missing and any(
                overlaps(missing[0], observed.path)
                for observed in (*self.kept, *self.unchanged)
            ):
                raise ValueError("保护必要缺失祖先与保留或无需操作范围重叠")

    @property
    def results(self):
        return (*self._results, *_active_results(self._active))

    @property
    def completed_units(self):
        return self._completed_units

    @property
    def active_change(self):
        return self._active if self._active is not None else self._scene

    @property
    def consumed(self):
        return self._lifecycle != "prepared"

    def _stage(self, target, phase):
        self.current, self.phase = target, phase

    def _remember_files(self, observed):
        for _, member in _files(observed):
            key = identity(member.path)
            previous = self._original_files.setdefault(key, member)
            if not _same_file_facts(previous, member):
                raise ValueError("同一固定文件位置具有矛盾观察")

    def _validate_cleanup_overlap(self, retained, effect):
        if not isinstance(retained, TreeObservation):
            raise ValueError("保留文件不能与实际改变重叠")
        if (
            effect.after.kind != "absent"
            or not contains(retained.path, effect.before.path)
            or identity(retained.path) == identity(effect.before.path)
        ):
            raise ValueError("保留树只能与其内部固定删除effect组合")

        relative = effect.before.path.relative_to(retained.path).as_posix()
        if isinstance(effect.before, FileObservation):
            fixed = dict(retained.files).get(relative)
            if fixed is None or not _same_file_facts(fixed, effect.before):
                raise ValueError("删除文件effect不对应完整保留树的固定观察")
            return

        prefix = relative + "/"
        directories = {
            position[len(prefix):] if position != relative else "": member
            for position, member in retained.directories
            if position == relative or position.startswith(prefix)
        }
        files = {
            position[len(prefix):]: member
            for position, member in retained.files
            if position.startswith(prefix)
        }
        if (
            set(directories) != set(dict(effect.before.directories))
            or set(files) != set(dict(effect.before.files))
        ):
            raise ValueError("删除子树没有准确对应完整保留范围")

        for position, member in effect.before.directories:
            fixed = directories[position]
            if (
                member.path != fixed.path
                or member.object_identity != fixed.object_identity
            ):
                raise ValueError("删除子树目录身份与固定保留范围矛盾")
        for position, member in effect.before.files:
            if not _same_file_facts(files[position], member):
                raise ValueError("删除子树文件与固定保留范围矛盾")

    def _validate_scope(self):
        entries = tuple(self.evidence.document.value["objects"])
        covered = set()
        effects = tuple(
            effect for unit in self.projection.units for effect in unit.effects
        )
        for effect in effects:
            matches = tuple(
                entry for entry in entries
                if contains(Path(entry["target"]), effect.before.path)
            )
            if len(matches) != 1:
                raise ValueError("恢复effect必须对应唯一严格原对象")
            entry = matches[0]
            whole = identity(entry["target"]) == identity(effect.before.path)
            if whole:
                if effect.after != _entry_state(entry):
                    raise ValueError("整体effect不对应严格依据原状态")
                source = self.evidence.materials.get(entry["id"])
                times = entry["file_times"]
            else:
                if (
                    entry["original"]["kind"] != "absent"
                    or effect.after.kind != "absent"
                ):
                    raise ValueError("子范围恢复仅允许原不存在对象的固定清理")
                source, times = None, ()

            if (
                effect.after.kind == "absent"
                and isinstance(effect.before, FileObservation)
                and effect.before.kind == "absent"
            ):
                raise ValueError("无工作不存在对象应归无需操作，不形成effect")
            if not whole and not any(
                isinstance(observed, TreeObservation)
                and identity(observed.path) == identity(entry["target"])
                for observed in self.kept
            ):
                raise ValueError("选择性清理必须具有完整固定保留树观察")

            self._bindings[identity(effect.before.path)] = (source, times)
            self._remember_files(effect.before)
            covered.add(entry["id"])
            if overlaps(effect.before.path, self.evidence.task_root):
                raise ValueError("恢复effect与源依据重叠")

        retained_paths = []
        for is_unchanged, collection in (
            (False, self.kept), (True, self.unchanged),
        ):
            for observed in collection:
                state, times = observation_state(observed)
                if any(
                    overlaps(observed.path, path) for path in retained_paths
                ):
                    raise ValueError("保留和无需操作范围重复或父子重叠")
                retained_paths.append(observed.path)
                matches = tuple(
                    entry for entry in entries
                    if identity(entry["target"]) == identity(observed.path)
                )
                if len(matches) != 1:
                    raise ValueError("保留或无需操作必须对应完整严格原对象")
                entry = matches[0]
                if is_unchanged and (
                    state != _entry_state(entry)
                    or times != entry["file_times"]
                ):
                    raise ValueError("无需操作尚未满足原内容及正式时间")
                for effect in effects:
                    if overlaps(observed.path, effect.before.path):
                        if (
                            is_unchanged
                            or entry["original"]["kind"] != "absent"
                        ):
                            raise ValueError("保留或无需操作与整体恢复冲突")
                        self._validate_cleanup_overlap(observed, effect)

                covered.add(entry["id"])
                self._remember_files(observed)
                if overlaps(observed.path, self.evidence.task_root):
                    raise ValueError("保留或无需操作范围与源依据重叠")
                if any(
                    contains(observed.path, root)
                    for root in self.projection.forbidden_roots
                ):
                    raise ValueError("保留观察不得包含禁止业务定位根")

        if covered != {entry["id"] for entry in entries}:
            raise ValueError("最终恢复决定必须覆盖全部严格原对象")
        for unit in self.projection.units:
            for path in (*unit.parent_directories, *unit.tree_directories):
                if overlaps(path, self.evidence.task_root):
                    raise ValueError("工作目录与源恢复依据重叠")
                if any(
                    overlaps(path, observed.path)
                    for observed in (*self.kept, *self.unchanged)
                ):
                    raise ValueError("工作目录创建与保留或无需操作范围冲突")
            for source in unit.fixed.reads:
                if any(
                    overlaps(source.path, effect.before.path)
                    for effect in effects
                ):
                    raise ValueError("固定只读来源与恢复改变范围重叠")

    def _removed_links(self):
        counts = {}
        for result in self.results:
            if (
                isinstance(result, FilesystemChangeResult)
                and result.succeeded and result.kind == "remove_file"
            ):
                observed = self._original_files.get(identity(result.target))
                if observed is None:
                    raise ValueError("正常文件移除结果越出最初固定范围")
                identifier = observed.object_identity
                counts[identifier] = counts.get(identifier, 0) + 1
        return counts

    def _created(self):
        created = {}
        for result in self.results:
            if (
                isinstance(result, FilesystemChangeResult)
                and result.succeeded and result.kind == "create_directory"
            ):
                observed = result.created_directory
                if (
                    not isinstance(observed, DirectoryObservation)
                    or observed.path != result.target
                ):
                    raise ValueError("正常创建结果缺少对应真实目录观察")
                key = identity(result.target)
                if key in created:
                    raise ValueError("同一工作目录重复正常创建")
                created[key] = observed
        return created

    def _check_parents(self, observed, created):
        _, chain = checked_chain(
            observed.path.parent, include_observation=True,
        )
        fixed = dict(observed.chain[:-1])
        for path, kind in chain:
            expected = fixed[path]
            if expected == kind:
                continue
            if (
                expected == "absent" and kind == "directory"
                and identity(path) in created
            ):
                continue
            raise ValueError(f"必要父目录偏离固定事实：{path}")

    def _remaining_kept(self, observed, counts):
        if isinstance(observed, FileObservation):
            return _adjust_links(observed, counts)
        removed = {
            identity(result.target)
            for result in self.results
            if isinstance(result, FilesystemChangeResult)
            and result.succeeded
            and result.kind in ("remove_file", "remove_empty_directory")
            and contains(observed.path, result.target)
        }
        records = tuple(
            record for record in observed.content_records
            if identity(observed.path / record[0]) not in removed
        )
        remaining = replace(
            observed,
            directories=tuple(
                member for member in observed.directories
                if identity(member[1].path) not in removed
            ),
            files=tuple(
                member for member in observed.files
                if identity(member[1].path) not in removed
            ),
            content_records=records,
            integrity=content_integrity("directory", records),
            file_times=tuple(
                member for member in observed.file_times
                if identity(observed.path / member[0]) not in removed
            ),
        )
        return _adjust_links(remaining, counts)

    def recheck(self):
        """新边界比较固定来源、真实成功前缀和剩余范围。"""
        recheck_recovery_evidence(self.evidence)
        counts = self._removed_links()
        created = self._created()
        for observed in created.values():
            target, chain = checked_chain(
                observed.path, include_observation=True,
            )
            if chain[-1][1] != "directory":
                raise ValueError("真实创建目录存在性或类型变化")
            attributes = target.lstat()
            if (
                ObjectIdentity(attributes.st_dev, attributes.st_ino)
                != observed.object_identity
            ):
                raise ValueError("真实创建目录身份变化")

        for unit in self.projection.units:
            for source in unit.fixed.reads:
                _recheck(source, False)
            for effect in unit.effects:
                self._check_parents(effect.before, created)
                if effect is self._active_effect:
                    # 本对象逐步约束由活动正式子核心负责；
                    # 其他对象和保留树不因此退出复核。
                    continue
                finished = self._finished.get(identity(effect.before.path))
                if finished is None:
                    _recheck(_adjust_links(effect.before, counts), False)
                else:
                    actual, previous_counts = finished
                    _recheck(
                        _adjust_links(actual, counts, previous_counts), True,
                    )

        for observed in self.kept:
            self._check_parents(observed, created)
            _recheck(self._remaining_kept(observed, counts), True)
        for observed in self.unchanged:
            self._check_parents(observed, created)
            _recheck(_adjust_links(observed, counts), True)

    def invalidate(self):
        if self._lifecycle != "prepared":
            return False
        self._lifecycle = "cancelled"
        self._stage(None, "cancelled")
        return True

    def _result(self, completed=False, error=None):
        return RecoveryExecutionResult(
            self.results, self.completed_units, completed,
            completed and not self.kept,
            self.current, self.phase, self.protection,
            self._fact_error, error,
        )

    def _execute_step(self, step):
        self._stage(step.expected.path, "固定名称步骤执行")
        self._scene = step
        try:
            result = execute_filesystem_change(step)
        except FilesystemChangeError as error:
            self._results = (*self._results, error.result)
            self._stage(error.result.target, error.result.phase)
            raise
        self._results = (*self._results, result)
        self._scene = None
        return result

    def _create_parents(self, unit):
        for target in unit.parent_directories:
            self._stage(target, "固定父目录创建或真实结果复用")
            self.recheck()
            if identity(target) in self._created():
                continue
            effect = next(
                effect for effect in unit.effects
                if dict(effect.before.chain).get(target) == "absent"
            )
            index = next(
                number for number, member in enumerate(effect.before.chain)
                if member[0] == target
            )
            expected = FileObservation(
                target, effect.before.chain[:index + 1], "absent",
                None, None, None, None, None, None,
            )
            self._execute_step(prepare_directory_creation(expected))

    def _execute_composite(self, prepared, executor, **keywords):
        self._active = prepared
        completed = False
        try:
            result = executor(prepared, **keywords)
            completed = True
            return result
        finally:
            # 运行中读取活动前缀；退出后移入正式前缀一次。
            self._results = (*self._results, *_active_results(prepared))
            self._active = None
            if not completed:
                self._scene = prepared
                self._stage(prepared.current, prepared.phase)

    def _dispose(self, artifact):
        if artifact._lifecycle in ("completed", "disposed"):
            return
        if (
            artifact.mechanism == "in_place"
            and artifact._lifecycle == "attempted"
        ):
            return
        try:
            dispose_content_artifact(artifact)
        except (OSError, ValueError) as error:
            # 清理诊断不覆盖主要失败；成果引用仍由失败现场保留。
            self._scene = artifact
            print(
                f"[恢复自有成果清理未完成] {artifact.path}：{error}",
                file=sys.stderr,
            )

    def _publish_file(self, before, source, times):
        self._stage(before.path, "固定原文件完整成果准备")
        artifact = prepare_copy_content_artifact(
            source, before, preserve_target_mode=False,
            options=ContentPublicationOptions(
                durable_publication=self.durable_publication,
                mtime_ns=times[0][1],
            ),
        )
        self._active = artifact
        completed = False
        try:
            self._stage(
                before.path, "完整原文件成果发布前所属范围复核",
            )
            self.recheck()
            _recheck_copy_source(source)
            self._stage(before.path, "固定原文件成果发布")
            try:
                result = publish_content(artifact)
            except ContentPublicationError as error:
                self._results = (*self._results, error.result)
                self._stage(error.result.target, error.result.phase)
                raise
            self._results = (*self._results, result)

            self._stage(
                before.path, "原文件正式内容时间及身份最终核验",
            )
            actual = observe_file(
                before.path, include_digest=True, include_mtime=True,
            )
            expected_identity = (
                before.object_identity
                if artifact.mechanism == "in_place"
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
                raise OSError("原文件最终内容、时间、身份或链接不符")
            completed = True
            return actual
        finally:
            self._active = None
            if not completed:
                self._scene = artifact
            self._dispose(artifact)

    def _verify_effect(self, effect, actual, times):
        if isinstance(actual, TreeObservation):
            kind = "directory"
            records = actual.content_records
            actual_times = actual.file_times
        elif actual.kind == "file":
            kind = "file"
            records = (("", "file", actual.digest),)
            actual_times = (("", actual.mtime_ns),)
        else:
            kind, records, actual_times = "absent", (), ()
        if (
            kind != effect.after.kind
            or records != effect.after.records or actual_times != times
        ):
            raise OSError(
                f"具体恢复最终内容、结构或正式时间不符：{effect.before.path}"
            )

    def _execute_effect(self, effect):
        target = effect.before.path
        source, times = self._bindings[identity(target)]
        before = _adjust_links(effect.before, self._removed_links())
        self._active_effect = effect
        try:
            if effect.after.kind == "absent":
                if isinstance(before, TreeObservation):
                    self._execute_composite(
                        prepare_tree_removal(before), execute_tree_removal,
                        before_step=self.recheck,
                    )
                else:
                    self.recheck()
                    self._execute_step(prepare_file_removal(before))
                actual = observe_file(target)

            elif effect.after.kind == "file":
                if isinstance(before, TreeObservation):
                    self._stage(target, "目录转文件完整成果准备")
                    prepared = prepare_directory_file_replacement(
                        replace(effect, before=before), source,
                        mtime_ns=times[0][1],
                        durable_publication=self.durable_publication,
                    )
                    self._execute_composite(
                        prepared, execute_directory_file_replacement,
                        recheck_context=self.recheck,
                    )
                    actual = observe_file(
                        target, include_digest=True, include_mtime=True,
                    )
                    if (
                        actual.kind != "file" or actual.link_count != 1
                        or actual.object_identity
                        != ObjectIdentity(*prepared.artifact.identity)
                    ):
                        raise OSError("目录转文件最终对象不对应已发布成果")
                else:
                    actual = self._publish_file(before, source, times)

            else:
                if isinstance(before, TreeObservation):
                    self._execute_composite(
                        prepare_tree_removal(before), execute_tree_removal,
                        before_step=self.recheck,
                    )
                elif before.kind == "file":
                    self.recheck()
                    self._execute_step(prepare_file_removal(before))

                self._stage(target, "固定原树重建准备")
                copy = prepare_tree_copy(
                    source, _absent(effect.before),
                    durable_publication=self.durable_publication,
                    file_times=times,
                )
                self._execute_composite(
                    copy, execute_tree_copy, recheck_context=self.recheck,
                )
                actual = _recheck_tree_copy_target(copy)

            self._stage(
                target, "具体effect最终内容结构及正式时间核验",
            )
            self._verify_effect(effect, actual, times)
            self._finished[identity(target)] = (
                actual, dict(self._removed_links()),
            )
        finally:
            self._active_effect = None

    def execute(self):
        if self._lifecycle != "prepared":
            raise ValueError("固定恢复任务已消费或取消，必须重新准备")
        self._lifecycle = "attempted"
        current_unit = None
        unit_start = 0
        try:
            self._stage(
                self.evidence.task_root, "恢复开始前全部固定事实复核",
            )
            self.recheck()
            if self.protection is not None:
                self._stage(
                    self.protection.task_root, "全部必要还原前保护屏障",
                )
                self.protection.protect_all()
                self._stage(
                    self.evidence.task_root,
                    "全量保护后来源及全部工作范围复核",
                )
                self.recheck()

            for current_unit in self.projection.units:
                unit_start = len(self._results)
                self._stage(
                    current_unit.effects[0].before.path,
                    "单元开始前真实剩余状态复核",
                )
                self.recheck()
                if self.protection is not None:
                    self._stage(
                        self.protection.task_root, "工作改变前started屏障",
                    )
                    self.protection.begin(current_unit.unit_id)
                self._create_parents(current_unit)

                for effect in current_unit.effects:
                    self._stage(
                        effect.before.path,
                        "具体effect开始前剩余状态复核",
                    )
                    self.recheck()
                    self._execute_effect(effect)
                    self._stage(
                        effect.before.path,
                        "具体effect完成后来源及剩余状态复核",
                    )
                    self.recheck()

                self._completed_units = (
                    *self._completed_units, current_unit.unit_id,
                )
                if self.protection is not None:
                    self._stage(
                        self.protection.task_root, "工作完成事实发布",
                    )
                    self.protection.complete(
                        current_unit.unit_id, self._results[unit_start:],
                    )

            self._stage(
                self.evidence.task_root,
                "整任务来源保留内容结构及正式时间最终复核",
            )
            self.recheck()
        except (OSError, ValueError) as error:
            if (
                current_unit is not None and self.protection is not None
                and not self.protection.failed
                and self.protection.document is not None
                and any(
                    unit["id"] == current_unit.unit_id
                    and unit["fact"]["state"] == "started"
                    for unit in self.protection.document.value["units"]
                )
            ):
                try:
                    self.protection.uncertain(
                        current_unit.unit_id, self._results[unit_start:],
                    )
                except (OSError, ValueError) as record_error:
                    self._fact_error = record_error
            raise RecoveryExecutionError(
                self._result(error=error), self,
            ) from error

        self._lifecycle = "completed"
        self._stage(None, "completed")
        return self._result(completed=True)


def prepare_recovery_execution(
    evidence, units, *, kept=(), unchanged=(), forbidden_roots=(),
    protection_root=None, protection_task_id=None, durable_publication=True,
):
    """固定最终决定到执行能力；当次确认由所属正式消费者完成。"""
    return RecoveryExecution(
        evidence, units, kept=kept, unchanged=unchanged,
        forbidden_roots=forbidden_roots,
        protection_root=protection_root,
        protection_task_id=protection_task_id,
        durable_publication=durable_publication,
    )
