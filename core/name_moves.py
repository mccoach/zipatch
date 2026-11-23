# -*- coding: utf-8 -*-

"""名称移动的固定对象身份、链接数量和结果核验。"""

import os
import stat
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from core.content_publication import (
    ContentPublicationError, ContentPublicationOptions, ContentPublicationResult,
    cleanup_content_artifact, prepare_copy_content_artifact, publish_content,
)
from core.file_observation import (
    FileObservation, TreeObservation,
    project_removed_file_links, recheck_file, recheck_tree,
)
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult,
    tree_execution_results,
    execute_filesystem_change, execute_tree_copy, execute_tree_removal,
    prepare_directory_creation, prepare_file_removal,
    prepare_tree_copy, prepare_tree_removal,
)
from core.recovery_paths import checked_chain, overlaps
from core.execution_ledger import ExecutionLedger
from core.link_paths import (
    LinkPathObservation, inspect_link_path, recheck_link_path,
    specification_from_link,
)
from core.filesystem_changes import prepare_link_creation, prepare_link_removal
from core.link_paths import validate_link_specification


class NameMoveError(OSError):
    """名称操作系统调用或结果核验失败，不能推定没有副作用。"""


@dataclass(frozen=True)
class NameMovePlan:
    source: Path
    target: Path
    source_kind: str
    source_identity: tuple
    target_identity: tuple | None
    target_links: int | None
    file_states: tuple
    destination_device: int
    target_kind: str

    @property
    def has_hardlinks(self):
        return any(number > 1 for _, _, number in self.file_states)

    @property
    def cross_device(self):
        return self.source_identity[0] != self.destination_device


def _attributes(path, kind):
    """名称身份查询不跟随末级链接，普通对象继续使用原链政策。"""
    if kind in ("file_symlink", "directory_symlink", "junction"):
        observed = inspect_link_path(path, follow_final_link=False)
        if (
            observed.kind != kind or observed.entry_link is None
            or observed.followed_links or observed.actual_path != path
        ):
            raise ValueError(f"固定名称入口类型或父链变化：{path}")
        target = observed.actual_path
        attributes = target.lstat()
        if (
            (attributes.st_dev, attributes.st_ino)
            != (
                observed.object_identity.device,
                observed.object_identity.inode,
            )
        ):
            raise ValueError(f"名称入口身份查询期间变化：{path}")
    else:
        target = checked_chain(path)
        attributes = target.lstat()
        expected_mode = stat.S_ISREG if kind == "file" else stat.S_ISDIR
        if not expected_mode(attributes.st_mode):
            raise ValueError(f"名称操作对象不再是普通{kind}：{target}")
    if (
        type(attributes.st_dev) is not int
        or attributes.st_dev < 0
        or type(attributes.st_ino) is not int
        or attributes.st_ino <= 0
    ):
        raise OSError(f"无法取得可靠的名称操作对象身份：{target}")
    if kind == "file" and (
        type(attributes.st_nlink) is not int or attributes.st_nlink < 1
    ):
        raise OSError(f"无法取得可靠的文件链接数量：{target}")
    return attributes


def _object_identity(attributes):
    return attributes.st_dev, attributes.st_ino


def _destination_device(target):
    """消费最近既有普通父目录，不创建目录或猜测目标设备。"""
    _, observation = checked_chain(target.parent, include_observation=True)
    for path, kind in reversed(observation):
        if kind == "directory":
            return _attributes(path, "directory").st_dev
    raise ValueError(f"移动目标没有可定位的普通父目录：{target}")


def _check_hardlink_device(source_device, destination_device, file_states):
    if (
        source_device != destination_device
        and any(number > 1 for _, _, number in file_states)
    ):
        raise ValueError(
            "跨文件系统移动会拆断硬链接关系，已拒绝；"
            "不会复制后删除来源，也不会扫描其他关联名称。"
        )


def recheck_name_move(plan):
    """新时间边界核对固定对象，拒绝同名同字节的新对象。"""
    if not isinstance(plan, NameMovePlan):
        raise TypeError("名称移动必须消费固定NameMovePlan")
    source_attributes = _attributes(plan.source, plan.source_kind)
    if _object_identity(source_attributes) != plan.source_identity:
        raise ValueError(
            f"移动来源对象身份已偏离固定计划：{plan.source}；"
            "必须重新预演及确认。"
        )
    target_access = inspect_link_path(plan.target, follow_final_link=False)
    if target_access.followed_links or target_access.actual_path != plan.target:
        raise ValueError("名称移动目标父链不得经过链接")
    target_kind = target_access.kind
    if plan.target_identity is None:
        if target_kind != "absent":
            raise ValueError(f"移动目标不再不存在：{plan.target}")
    else:
        if target_kind != plan.target_kind:
            raise ValueError(f"移动目标类型已偏离固定计划：{plan.target}")
        attributes = _attributes(plan.target, target_kind)
        if _object_identity(attributes) != plan.target_identity:
            raise ValueError(
                f"移动目标对象身份已偏离固定计划：{plan.target}；"
                "必须重新预演及确认。"
            )
        if _object_identity(attributes) == plan.source_identity:
            raise ValueError("移动来源和目标实际成为同一对象，禁止移除")
        if (
            plan.target_links is not None
            and attributes.st_nlink != plan.target_links
        ):
            raise ValueError(
                f"移动目标文件链接数量已偏离固定计划：{plan.target}；"
                "必须重新预演及确认。"
            )

    for path, expected_identity, expected_links in plan.file_states:
        attributes = (
            source_attributes
            if plan.source_kind == "file" and path == plan.source
            else _attributes(path, "file")
        )
        if _object_identity(attributes) != expected_identity:
            raise ValueError(
                f"移动来源文件身份已偏离固定计划：{path}；"
                "必须重新预演及确认。"
            )
        if attributes.st_nlink != expected_links:
            raise ValueError(
                f"移动来源文件链接数量已偏离固定计划：{path}；"
                "必须重新预演及确认。"
            )
    destination_device = _destination_device(plan.target)
    if destination_device != plan.destination_device:
        raise ValueError(
            f"移动目标文件系统已偏离固定计划：{plan.target}；"
            "必须重新预演及确认。"
        )
    _check_hardlink_device(
        source_attributes.st_dev, destination_device, plan.file_states,
    )


def check_native_move_result(plan):
    """原生调用正常返回后核验来源移除、目标身份及适用链接数量。"""
    _, observation = checked_chain(plan.source, include_observation=True)
    if observation[-1][1] != "absent":
        raise OSError(f"原生移动正常返回，但来源名称仍存在：{plan.source}")
    attributes = _attributes(plan.target, plan.source_kind)
    if _object_identity(attributes) != plan.source_identity:
        raise OSError(f"原生移动结果未保持来源对象身份：{plan.target}")
    for original_path, expected_identity, expected_links in plan.file_states:
        destination = (
            plan.target if plan.source_kind == "file"
            else plan.target / original_path.relative_to(plan.source)
        )
        attributes = _attributes(destination, "file")
        if (
            _object_identity(attributes) != expected_identity
            or attributes.st_nlink != expected_links
        ):
            raise OSError(
                f"原生移动结果的文件身份或链接数量不符：{destination}"
            )

@dataclass(frozen=True)
class NativeNameMoveResult:
    """一次实际原生名称调用及其结果核验，不代替整个复合动作。"""

    source: Path
    target: Path
    attempted: bool
    completed: bool
    phase: str
    error: OSError | ValueError | None = None

    @property
    def succeeded(self):
        return self.error is None and self.attempted and self.completed


@dataclass(frozen=True, init=False)
class PreparedNameMove:
    """固定观察、固定机制、一次资格和真实执行现场。"""

    source: FileObservation | TreeObservation
    expected: FileObservation | TreeObservation
    plan: NameMovePlan
    mechanism: str
    _lifecycle: str
    _execution: ExecutionLedger
    _current: Path | None
    _phase: str
    _artifact: object
    _final_observations: tuple

    def __init__(self, *arguments, **keywords):
        raise TypeError("名称移动必须通过正式固定观察准备过程创建")

    @property
    def final_observations(self):
        """正常结束后交付算法已核验的两端约束，不重新授权现场对象。"""
        return self._final_observations

    @property
    def results(self):
        return self._execution.results

    @property
    def current(self):
        return self._current

    @property
    def phase(self):
        return self._phase

    @property
    def active_change(self):
        """失败子核心现场只读保留，不恢复其执行资格。"""
        return self._execution.active_change


@dataclass(frozen=True)
class NameMoveResult:
    source: Path
    target: Path
    mechanism: str
    results: tuple
    completed: bool
    current: Path | None
    phase: str
    error: OSError | ValueError | None = None

    @property
    def attempted(self):
        return any(
            member.mutation_attempted
            if isinstance(member, ContentPublicationResult)
            else member.attempted
            for member in self.results
        )

    @property
    def succeeded(self):
        return self.error is None and self.completed and self.current is None


class FixedNameMoveError(NameMoveError):
    """保留实际前序步骤及当前未确认位置，不猜测零副作用。"""

    def __init__(self, result):
        self.result = result
        super().__init__(
            f"固定名称移动未完整结束：{result.source} -> {result.target}\n"
            f"固定机制：{result.mechanism}；阶段：{result.phase}\n"
            f"当前未确认位置：{result.current or '不适用'}\n"
            f"已确认成功步骤："
            f"{sum(member.succeeded for member in result.results)}\n"
            f"改变调用已发起：{'是' if result.attempted else '否'}\n"
            "前序真实结果保留；调用报错不能证明没有副作用。\n"
            "本次资格已消费，不切换机制，不自动重试或回滚。\n"
            f"{result.error}"
        )


def _move_kind(observed):
    if isinstance(observed, TreeObservation):
        return "directory"
    if isinstance(observed, (FileObservation, LinkPathObservation)):
        return observed.kind
    raise TypeError("名称移动必须消费正式文件、链接入口或完整树观察")


def _name_family(kind):
    if kind in ("file", "file_symlink"):
        return "file"
    if kind in ("directory", "directory_symlink", "junction"):
        return "directory"
    return kind


def _move_identity(observed):
    if isinstance(observed, TreeObservation):
        return observed.directories[0][1].object_identity
    return observed.object_identity


def _move_files(observed):
    if isinstance(observed, TreeObservation):
        return observed.files
    return (("", observed),) if observed.kind == "file" else ()


def _validate_move_observation(observed, *, source=False):
    kind = _move_kind(observed)
    if source and kind == "absent":
        raise ValueError("名称移动来源必须存在")
    if isinstance(observed, TreeObservation):
        prepare_tree_removal(observed)
    elif isinstance(observed, LinkPathObservation):
        prepare_link_removal(observed)
        if observed.followed_links or observed.nominal_path != observed.actual_path:
            raise ValueError("名称入口不得通过其他目录链接定位")
    elif kind == "file":
        prepare_file_removal(observed)
    elif kind == "absent":
        prepare_directory_creation(observed)
    else:
        raise ValueError("名称移动对象类型非法")
    if (
        not isinstance(observed.path, Path)
        or not observed.path.is_absolute()
        or observed.path == observed.path.parent
    ):
        raise ValueError("名称移动不得作用于文件系统根或非绝对位置")
    return kind


def _absent_move_target(expected):
    """固定目标成功移除后的不存在投影，不查询或重新授权磁盘内容。"""
    return FileObservation(
        expected.path, expected.chain, "absent",
        None, None, None, None, None, None,
    )


def _move_file_states(source):
    return tuple(
        (
            observed.path,
            (observed.object_identity.device, observed.object_identity.inode),
            observed.link_count,
        )
        for _, observed in _move_files(source)
    )


def prepare_observed_name_move(source, expected):
    """消费固定观察；设备判断只查询目标最近既有普通父目录。

    不读取正文、不枚举树、不改变工作目标。
    所属协调负责业务范围、必要父目录、确认、保护及started。
    """
    source_kind = _validate_move_observation(source, source=True)
    target_kind = _validate_move_observation(expected)
    if target_kind != "absent" and _name_family(target_kind) != _name_family(source_kind):
        raise ValueError("名称移动来源与目标的文件／目录语义不匹配")
    if overlaps(source.path, expected.path):
        raise ValueError("名称移动来源目标不得相同或父子重叠")
    source_identity = _move_identity(source)
    target_identity = _move_identity(expected)
    if target_identity is not None and target_identity == source_identity:
        raise ValueError("名称移动来源和目标实际为同一对象")

    states = _move_file_states(source)
    device = _destination_device(expected.path)
    fixed_identity = (source_identity.device, source_identity.inode)
    _check_hardlink_device(fixed_identity[0], device, states)
    mechanism = "copy_remove" if fixed_identity[0] != device else "native"
    if mechanism == "copy_remove" and isinstance(source, LinkPathObservation):
        # 原定义在新入口位置必须可解释；准备时发现错误，
        # 不能等旧目标已经移除后才判断是否能够重建。
        validate_link_specification(
            specification_from_link(source.entry_link), expected.path,
        )
    if mechanism == "copy_remove":
        for _, observed in _move_files(source):
            if (
                not isinstance(observed.source_bytes, bytes)
                or type(observed.mtime_ns) is not int
                or observed.mtime_ns != observed.attributes.st_mtime_ns
            ):
                raise ValueError("跨卷移动来源缺少固定完整bytes及正式时间")
        if isinstance(source, TreeObservation):
            # 复用独立树复制的来源契约；不持有预测目标的执行资格。
            prepare_tree_copy(source, _absent_move_target(expected))

    plan = NameMovePlan(
        source.path, expected.path, source_kind, fixed_identity,
        None if target_identity is None else (
            target_identity.device, target_identity.inode,
        ),
        expected.link_count
        if isinstance(expected, FileObservation) and expected.kind == "file"
        else None,
        states, device, target_kind,
    )
    move = object.__new__(PreparedNameMove)
    for name, value in (
        ("source", source), ("expected", expected), ("plan", plan),
        ("mechanism", mechanism), ("_lifecycle", "prepared"),
        ("_execution", ExecutionLedger(tree_execution_results)),
        ("_current", None), ("_phase", "prepared"), ("_artifact", None),
        ("_final_observations", ()),
    ):
        object.__setattr__(move, name, value)
    return move


def cancel_observed_name_move(move):
    if not isinstance(move, PreparedNameMove):
        raise TypeError("取消必须消费PreparedNameMove")
    if move._lifecycle != "prepared":
        raise ValueError("名称移动已经消费或取消")
    object.__setattr__(move, "_lifecycle", "cancelled")


def _move_stage(move, current, phase):
    """先登记实际检查或调用位置；失败不依赖异常文字定位。"""
    object.__setattr__(move, "_current", current)
    object.__setattr__(move, "_phase", phase)


def _recheck_move_object(expected):
    if isinstance(expected, LinkPathObservation):
        return recheck_link_path(expected)
    if isinstance(expected, TreeObservation):
        current = recheck_tree(
            expected, check_mtime=bool(expected.file_times),
        )
        for fixed_members, current_members in (
            (expected.directories, current.directories),
            (expected.files, current.files),
        ):
            fixed = dict(fixed_members)
            for relative, actual in current_members:
                if stat.S_IMODE(actual.attributes.st_mode) != stat.S_IMODE(
                    fixed[relative].attributes.st_mode
                ):
                    raise ValueError(f"固定名称对象基础权限变化：{actual.path}")
        return current
    current = recheck_file(
        expected,
        check_mtime=expected.kind == "file" and expected.mtime_ns is not None,
    )
    if current.kind == "file" and stat.S_IMODE(
        current.attributes.st_mode
    ) != stat.S_IMODE(expected.attributes.st_mode):
        raise ValueError(f"固定名称对象基础权限变化：{expected.path}")
    return current


def _relocated_move_source(source, target):
    """原生移动结束边界核验原对象，不把后来现场当成新授权。

    普通文件继续消费原固定观察。完整树的链接观察包含入口位置、
    普通父目录依赖及直接引用位置，不能只改树根和普通成员路径。
    目标观察须严格对应原固定结构、成员身份和全部原定义，
    才能交付本次原生移动的正式成果；不读取链接目标正文。
    """
    if isinstance(source, LinkPathObservation):
        actual = inspect_link_path(target, follow_final_link=False)
        if (
            actual.followed_links or actual.actual_path != target
            or actual.entry_link is None
            or actual.kind != source.kind
            or actual.object_identity != source.object_identity
            or specification_from_link(actual.entry_link)
            != specification_from_link(source.entry_link)
        ):
            raise OSError("原生移动未保持链接入口身份及实际原定义")
        return actual
    if isinstance(source, FileObservation):
        return replace(source, path=target)
    from core.file_observation import observe_tree

    actual = observe_tree(
        target, include_mtime=bool(source.file_times),
        include_links=bool(source.links),
    )
    if (
        actual.content_records != source.content_records
        or actual.file_times != source.file_times
        or tuple(
            (relative, member.object_identity)
            for relative, member in actual.directories
        ) != tuple(
            (relative, member.object_identity)
            for relative, member in source.directories
        )
        or tuple(
            (relative, member.object_identity, member.link_count)
            for relative, member in actual.files
        ) != tuple(
            (relative, member.object_identity, member.link_count)
            for relative, member in source.files
        )
        or tuple(
            (
                relative, member.object_identity,
                specification_from_link(member.entry_link),
            )
            for relative, member in actual.links
        ) != tuple(
            (
                relative, member.object_identity,
                specification_from_link(member.entry_link),
            )
            for relative, member in source.links
        )
    ):
        raise OSError(
            "原生移动目标没有保持固定完整结构、原对象身份、"
            "文件链接数量、内容、正式时间或链接原定义"
        )
    for originals, relocated in (
        (source.directories, actual.directories),
        (source.files, actual.files),
    ):
        for (relative, original), (position, member) in zip(
            originals, relocated,
        ):
            if (
                relative != position
                or member.path != target / relative
                or stat.S_IMODE(member.attributes.st_mode)
                != stat.S_IMODE(original.attributes.st_mode)
            ):
                raise OSError("原生移动成员位置或基础权限不对应原固定对象")
    return actual


def _append_move_results(move, results):
    if any(
        not isinstance(member, (
            FilesystemChangeResult, ContentPublicationResult,
            NativeNameMoveResult,
        ))
        for member in results
    ):
        raise TypeError("名称组合必须消费正式步骤结果")
    move._execution.append_results(tuple(results))


def _execute_move_file_removal(move, expected):
    step = prepare_file_removal(expected)
    try:
        result = execute_filesystem_change(step)
    except FilesystemChangeError as error:
        _append_move_results(move, (error.result,))
        _move_stage(move, error.result.target, error.result.phase)
        raise
    _append_move_results(move, (result,))
    return result



def _execute_cross_volume_link(move, source, expected, plan):
    """固定原定义重建目标并核验后，才移除固定来源入口。

    来源及目标均为已授权实际位置，不解析引用目标。
    正常创建身份只来自正式创建结果；失败不清理未知成果，
    不重试、不换机制，所属修改包消费真实前缀决定回滚。
    """
    specification = specification_from_link(source.entry_link)
    validate_link_specification(specification, expected.path)

    def execute_step(step):
        move._execution.scene = step
        try:
            receipt = execute_filesystem_change(step)
        except FilesystemChangeError as error:
            _append_move_results(move, (error.result,))
            _move_stage(move, error.result.target, error.result.phase)
            raise
        _append_move_results(move, (receipt,))
        move._execution.scene = None
        return receipt

    _move_stage(move, source.path, "跨卷链接目标改变前固定来源复核")
    _recheck_move_object(source)
    _move_stage(move, expected.path, "跨卷链接目标改变前固定两端复核")
    _recheck_move_object(expected)
    recheck_name_move(plan)

    if isinstance(expected, TreeObservation):
        _move_stage(move, expected.path, "跨卷链接固定旧目标树移除")
        removal = prepare_tree_removal(expected)
        move._execution.execute_active(
            removal, execute_tree_removal,
            stage=lambda current, phase: _move_stage(move, current, phase),
            before_step=lambda: _recheck_move_object(source),
        )
    elif isinstance(expected, LinkPathObservation):
        _move_stage(move, expected.path, "跨卷链接固定旧目标入口移除")
        execute_step(prepare_link_removal(expected))
    elif expected.kind == "file":
        _move_stage(move, expected.path, "跨卷链接固定旧目标文件名称移除")
        execute_step(prepare_file_removal(expected))
    elif expected.kind != "absent":
        raise ValueError("跨卷链接固定目标类型非法")

    missing = _absent_move_target(expected)
    absent_plan = replace(
        plan, target_identity=None, target_links=None,
        target_kind="absent",
    )
    _move_stage(move, expected.path, "跨卷链接创建前固定缺失目标复核")
    recheck_name_move(absent_plan)
    _recheck_move_object(source)

    _move_stage(move, expected.path, "跨卷链接保存原定义目标创建")
    created = execute_step(prepare_link_creation(missing, specification))
    copied = created.created_link
    if (
        not isinstance(copied, LinkPathObservation)
        or copied.actual_path != expected.path
        or copied.followed_links
        or copied.entry_link is None
        or copied.kind != source.kind
        or specification_from_link(copied.entry_link) != specification
    ):
        raise RuntimeError("正常跨卷链接创建缺少对应的正式完整成果")

    _move_stage(move, copied.path, "来源移除前跨卷链接完整成果复核")
    _recheck_move_object(copied)
    _move_stage(move, source.path, "来源移除前固定链接来源复核")
    _recheck_move_object(source)
    # 来源复核跨越新边界；最后再次核对目标，不以早先成功代替。
    _move_stage(move, copied.path, "来源移除前目标链接最终复核")
    _recheck_move_object(copied)

    _move_stage(move, source.path, "跨卷固定来源链接入口移除")
    execute_step(prepare_link_removal(source))
    _move_stage(move, copied.path, "跨卷链接移动最终成果复核")
    _recheck_move_object(copied)
    return copied


def _execute_native_name(move, source, plan):
    attempted = False
    try:
        _move_stage(move, source.path, "原生调用前固定来源复核")
        _recheck_move_object(source)
        _move_stage(move, plan.target, "原生调用前固定名称前提复核")
        recheck_name_move(plan)
        _, parents = checked_chain(
            plan.target.parent, include_observation=True,
        )
        if any(kind != "directory" for _, kind in parents):
            raise ValueError("固定名称移动要求必要父目录已经存在")
        _move_stage(move, plan.target, "原生名称调用")
        attempted = True
        if plan.source_kind in ("file", "file_symlink"):
            os.replace(plan.source, plan.target)
        else:
            os.rename(plan.source, plan.target)
        _move_stage(move, plan.target, "原生名称结果身份及内容核验")
        check_native_move_result(plan)
        _recheck_move_object(
            _relocated_move_source(source, plan.target),
        )
    except (OSError, ValueError) as error:
        _append_move_results(move, (NativeNameMoveResult(
            plan.source, plan.target, attempted, False, move.phase, error,
        ),))
        raise
    _append_move_results(move, (NativeNameMoveResult(
        plan.source, plan.target, True, True, "completed",
    ),))


def execute_observed_name_move(move, *, removed_link_counts=None):
    """一次消费固定机制；直接组合正式核心，不组织业务授权。

    同卷文件覆盖只替换指定名称。
    跨卷完整独立复制并核验后才移除来源名称或固定来源树。
    原生报错不改成复制删除，不自动重试、删除目标成果或回滚。
    """
    if not isinstance(move, PreparedNameMove):
        raise TypeError("执行必须消费PreparedNameMove")
    if move._lifecycle != "prepared":
        raise ValueError("名称移动已消费或取消，必须重新准备")
    object.__setattr__(move, "_lifecycle", "attempted")
    source, expected, plan = move.source, move.expected, move.plan
    try:
        if removed_link_counts is not None:
            if not isinstance(source, LinkPathObservation):
                source = project_removed_file_links(
                    source, removed_link_counts,
                )
            if not isinstance(expected, LinkPathObservation):
                expected = project_removed_file_links(
                    expected, removed_link_counts,
                )
            plan = replace(
                plan,
                file_states=_move_file_states(source),
                target_links=(
                    expected.link_count
                    if isinstance(expected, FileObservation)
                    and expected.kind == "file" else None
                ),
            )
        # 前序正常名称移除只改变本次局部链接约束。
        # 原实例、原内容、身份、设备和准备时固定机制保持。
        _move_stage(move, source.path, "固定来源复核")
        _recheck_move_object(source)
        _move_stage(move, expected.path, "固定目标及机制复核")
        _recheck_move_object(expected)
        recheck_name_move(plan)
        _, parents = checked_chain(
            expected.path.parent, include_observation=True,
        )
        if any(kind != "directory" for _, kind in parents):
            raise ValueError("名称移动必要父目录必须由所属固定计划先行创建")

        if isinstance(expected, LinkPathObservation):
            _move_stage(move, expected.path, "固定已有目标链接入口移除")
            step = prepare_link_removal(expected)
            try:
                receipt = execute_filesystem_change(step)
            except FilesystemChangeError as error:
                _append_move_results(move, (error.result,))
                _move_stage(move, error.result.target, error.result.phase)
                raise
            _append_move_results(move, (receipt,))
            expected = _absent_move_target(expected)
            plan = replace(
                plan, target_identity=None, target_links=None,
                target_kind="absent",
            )

        if isinstance(source, LinkPathObservation) and move.mechanism == "copy_remove":
            copied = _execute_cross_volume_link(
                move, source, expected, plan,
            )
        elif plan.source_kind == "file" and move.mechanism == "copy_remove":
            _move_stage(move, expected.path, "跨卷固定来源完整独立成果准备")
            artifact = prepare_copy_content_artifact(
                source, expected, preserve_target_mode=False,
                replace_target_name=True,
                options=ContentPublicationOptions(),
            )
            object.__setattr__(move, "_artifact", artifact)
            _move_stage(move, source.path, "跨卷成果准备后固定来源复核")
            _recheck_move_object(source)
            _move_stage(move, expected.path, "跨卷成果准备后固定目标复核")
            _recheck_move_object(expected)
            recheck_name_move(plan)
            _move_stage(move, expected.path, "跨卷指定目标名称发布")
            try:
                publication = publish_content(artifact)
            except ContentPublicationError as error:
                _append_move_results(move, (error.result,))
                _move_stage(move, error.result.target, error.result.phase)
                raise
            _append_move_results(move, (publication,))
            copied = publication.observation
            if not isinstance(copied, FileObservation):
                raise RuntimeError("正常跨卷文件发布缺少正式目标观察")
            _move_stage(move, expected.path, "来源移除前完整目标核验")
            _recheck_move_object(copied)
            _move_stage(move, source.path, "来源移除前固定来源复核")
            _recheck_move_object(source)
            _move_stage(move, source.path, "跨卷来源文件名称移除")
            _execute_move_file_removal(move, source)
            _move_stage(move, expected.path, "跨卷文件最终成果核验")
            _recheck_move_object(copied)
        else:
            if isinstance(expected, TreeObservation):
                _move_stage(move, expected.path, "固定已有目标树名称移除")
                removal = prepare_tree_removal(expected)
                move._execution.execute_active(
                    removal, execute_tree_removal,
                    stage=lambda current, phase: _move_stage(move, current, phase),
                )
                if move.mechanism == "native" and not isinstance(source, LinkPathObservation):
                    # 完整旧目标树已正常移除，才扣除其原有名称数量。
                    source = project_removed_file_links(
                        source,
                        Counter(
                            observed.object_identity
                            for _, observed in expected.files
                        ),
                    )
                expected = _absent_move_target(expected)
                plan = replace(
                    plan, target_identity=None, target_links=None,
                    file_states=_move_file_states(source),
                )

            if move.mechanism == "native":
                _execute_native_name(move, source, plan)
            else:
                _move_stage(move, expected.path, "跨卷固定独立目标树重建")
                copy = prepare_tree_copy(source, expected)
                move._execution.execute_active(
                    copy, execute_tree_copy,
                    stage=lambda current, phase: _move_stage(move, current, phase),
                )
                copied = copy.final_observation
                if not isinstance(copied, TreeObservation):
                    raise RuntimeError("正常跨卷目录复制缺少最终目标观察")
                _move_stage(move, expected.path, "来源移除前完整目标树核验")
                _recheck_move_object(copied)
                _move_stage(move, source.path, "来源移除前固定来源树复核")
                _recheck_move_object(source)
                _move_stage(move, source.path, "跨卷固定来源树名称移除")
                removal = prepare_tree_removal(source)
                move._execution.execute_active(
                    removal, execute_tree_removal,
                    stage=lambda current, phase: _move_stage(move, current, phase),
                )
                _move_stage(move, expected.path, "跨卷目录最终成果核验")
                _recheck_move_object(copied)
    except (OSError, ValueError) as error:
        raise FixedNameMoveError(NameMoveResult(
            move.source.path, move.expected.path, move.mechanism,
            move.results, False, move.current, move.phase, error,
        )) from error
    finally:
        if cleanup_content_artifact(move._artifact):
            object.__setattr__(move, "_artifact", None)

    final_target = (
        _relocated_move_source(source, plan.target)
        if move.mechanism == "native" else copied
    )
    final_source = FileObservation(
        move.source.path,
        (*move.source.chain[:-1], (move.source.path, "absent")),
        "absent", None, None, None, None, None, None,
    )
    object.__setattr__(
        move, "_final_observations", (final_source, final_target),
    )
    object.__setattr__(move, "_lifecycle", "completed")
    _move_stage(move, None, "completed")
    return NameMoveResult(
        move.source.path, move.expected.path, move.mechanism,
        move.results, True, None, "completed",
    )
