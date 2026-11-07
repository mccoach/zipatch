# -*- coding: utf-8 -*-

"""固定单目录创建、文件名称移除及空目录移除。

消费正式观察和一次执行资格，不解析OP、不确认、不保护，
不创建未计划父目录、不递归删除、不自动重试或回滚。
调用报错不证明没有副作用；程序缺陷自然传播。
"""

import hashlib
import os
import stat
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from core.atomic_file import sync_directory
from core.content_publication import (
    ContentPublicationError, ContentPublicationOptions,
    ContentPublicationResult, dispose_content_artifact,
    prepare_copy_content_artifact, publish_content,
)
from core.file_observation import (
    ObjectIdentity, observe_file, project_removed_file_links,
)
from core.recovery_paths import entity_type_from_attributes, overlaps

from core.recovery_content import content_integrity

from core.file_observation import (
    DirectoryObservation, FileObservation, TreeObservation,
    observe_tree, recheck_file, recheck_tree,
)
from core.recovery_paths import checked_chain


@dataclass(frozen=True, init=False)
class PreparedFilesystemChange:
    """正式准备过程拥有固定前提；生命周期只表达执行资格。"""

    kind: str
    expected: FileObservation | TreeObservation
    _lifecycle: str

    def __init__(self, *arguments, **keywords):
        raise TypeError("文件系统步骤必须通过正式准备过程创建")


@dataclass(frozen=True)
class FilesystemChangeResult:
    """一个实际步骤的结果，不从计划或诊断文字推导完成。"""

    target: Path
    kind: str
    attempted: bool
    completed: bool
    phase: str
    created_directory: DirectoryObservation | None = None
    error: OSError | ValueError | None = None

    @property
    def succeeded(self):
        return self.error is None and self.attempted and self.completed


class FilesystemChangeError(OSError):
    """明确I/O或固定状态失败，携带所属位置的实际步骤事实。"""

    def __init__(self, result):
        self.result = result
        super().__init__(
            f"固定文件系统步骤未完整结束：{result.target}\n"
            f"动作：{result.kind}；阶段：{result.phase}\n"
            f"改变调用已发起：{'是' if result.attempted else '否'}\n"
            f"正常完成已确认：{'是' if result.completed else '否'}\n"
            "调用报错或结果核验失败不能证明目标没有改变。\n"
            "本步骤资格已消费；停止后续，不自动重试或回滚。\n"
            f"{result.error}"
        )


def _validate_path(path):
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("固定步骤必须具有明确绝对目标")
    if path == path.parent:
        raise ValueError("固定步骤不得改变文件系统根")


def _new_change(kind, expected):
    change = object.__new__(PreparedFilesystemChange)
    object.__setattr__(change, "kind", kind)
    object.__setattr__(change, "expected", expected)
    object.__setattr__(change, "_lifecycle", "prepared")
    return change


def prepare_directory_creation(expected):
    """只消费固定不存在观察；必要父目录由所属协调按计划组织。"""
    if not isinstance(expected, FileObservation):
        raise TypeError("目录创建必须消费正式不存在文件观察")
    _validate_path(expected.path)
    if expected.kind != "absent":
        raise ValueError("单目录创建目标必须固定为不存在")
    if any(value is not None for value in (
        expected.attributes, expected.object_identity, expected.link_count,
        expected.source_bytes, expected.digest, expected.mtime_ns,
    )):
        raise ValueError("不存在观察不得带既有对象事实")
    return _new_change("create_directory", expected)


def prepare_file_removal(expected):
    """指定名称移除，固定原内容和对象；不清空共享硬链接正文。"""
    if not isinstance(expected, FileObservation):
        raise TypeError("文件名称移除必须消费FileObservation")
    _validate_path(expected.path)
    if expected.kind != "file":
        raise ValueError("文件名称移除要求固定普通文件")
    if (
        expected.attributes is None
        or expected.object_identity is None
        or type(expected.link_count) is not int
        or expected.link_count < 1
        or expected.source_bytes is None and expected.digest is None
    ):
        raise ValueError("文件名称移除缺少固定身份、链接或原内容前提")
    return _new_change("remove_file", expected)


def prepare_empty_directory_removal(expected):
    """完整空树观察只授权根自身rmdir，不授权后代删除。"""
    if not isinstance(expected, TreeObservation):
        raise TypeError("空目录移除必须消费TreeObservation")
    _validate_path(expected.path)
    if (
        len(expected.directories) != 1
        or expected.directories[0][0] != ""
        or expected.directories[0][1].path != expected.path
        or expected.files
        or expected.content_records != (("", "directory", ""),)
        or expected.file_times
    ):
        raise ValueError("空目录移除必须具有完整空目录根观察")
    return _new_change("remove_empty_directory", expected)


def cancel_filesystem_change(change):
    """明确取消未执行步骤；没有暂存或工作对象需要清理。"""
    if not isinstance(change, PreparedFilesystemChange):
        raise TypeError("取消必须消费PreparedFilesystemChange")
    if change._lifecycle != "prepared":
        raise ValueError("步骤已经消费或取消，不能再次操作")
    object.__setattr__(change, "_lifecycle", "cancelled")


def _check_creation_parent(target):
    _, chain = checked_chain(target.parent, include_observation=True)
    if any(kind != "directory" for _, kind in chain):
        raise ValueError("固定单目录创建要求必要父链已为普通目录")


def _confirm_absent(target):
    _, chain = checked_chain(target, include_observation=True)
    if chain[-1][1] != "absent":
        raise OSError(f"名称移除正常返回，但目标仍存在：{target}")


def execute_filesystem_change(change):
    """一次消费固定步骤，复核后改变，正常返回后核验实际结果。

    全部异常都会使执行资格失效；仅明确I/O和状态失败包装结果。
    空目录执行期完整复核属于新时间边界，rmdir本身仍是最后
    空性约束，不在其失败后改用递归删除。
    """
    if not isinstance(change, PreparedFilesystemChange):
        raise TypeError("执行必须消费PreparedFilesystemChange")
    if change._lifecycle != "prepared":
        raise ValueError("步骤已消费或取消，必须重新准备")
    object.__setattr__(change, "_lifecycle", "attempted")

    expected = change.expected
    target = expected.path
    phase = "固定前提复核"
    attempted = False
    created_directory = None
    try:
        if change.kind == "create_directory":
            recheck_file(expected)
            phase = "必要父目录复核"
            _check_creation_parent(target)
            phase = "固定单目录创建"
            attempted = True
            target.mkdir(exist_ok=False)
            phase = "创建结果核验"
            created = observe_tree(target)
            if created.content_records != (("", "directory", ""),):
                raise OSError(f"新建目录结果不再是空普通目录：{target}")
            created_directory = created.directories[0][1]
        elif change.kind == "remove_file":
            recheck_file(expected)
            phase = "指定文件名称移除"
            attempted = True
            target.unlink()
            phase = "名称移除结果核验"
            _confirm_absent(target)
        elif change.kind == "remove_empty_directory":
            recheck_tree(expected)
            phase = "固定空目录名称移除"
            attempted = True
            target.rmdir()
            phase = "名称移除结果核验"
            _confirm_absent(target)
        else:
            raise RuntimeError("活动步骤包含未知文件系统动作")
    except (OSError, ValueError) as error:
        result = FilesystemChangeResult(
            target, change.kind, attempted, False, phase,
            created_directory, error,
        )
        raise FilesystemChangeError(result) from error

    object.__setattr__(change, "_lifecycle", "completed")
    return FilesystemChangeResult(
        target, change.kind, True, True, "completed",
        created_directory,
    )


@dataclass(frozen=True, init=False)
class PreparedTreeRemoval:
    """完整固定树的移除资格；成功前缀只来自实际步骤结果。"""

    expected: TreeObservation
    steps: tuple
    _lifecycle: str
    _results: tuple
    _current: Path | None
    _phase: str

    def __init__(self, *arguments, **keywords):
        raise TypeError("完整树移除必须通过正式准备过程创建")

    @property
    def results(self):
        return self._results

    @property
    def current(self):
        return self._current

    @property
    def phase(self):
        return self._phase


@dataclass(frozen=True)
class TreeRemovalResult:
    target: Path
    results: tuple
    completed: bool
    phase: str
    current: Path | None
    error: OSError | ValueError | None = None

    @property
    def attempted(self):
        return any(result.attempted for result in self.results)

    @property
    def succeeded(self):
        return self.error is None and self.completed and self.current is None


class TreeRemovalError(OSError):
    """完整树动作保留前序结果和当前未确认位置，不猜测零副作用。"""

    def __init__(self, result):
        self.result = result
        super().__init__(
            f"固定完整树移除未完整结束：{result.target}\n"
            f"阶段：{result.phase}\n"
            f"已确认成功步骤："
            f"{sum(member.succeeded for member in result.results)}\n"
            f"改变调用已发起：{'是' if result.attempted else '否'}\n"
            f"当前未确认位置：{result.current or '不适用'}\n"
            "前序结果保留；当前调用报错不能证明没有副作用。\n"
            "资格已消费，停止后续，不自动重试或回滚。\n"
            f"{result.error}"
        )


def _validate_tree_observation(expected):
    """校验固定观察的必要结构，不重新扫描或补猜文件事实。"""
    if not isinstance(expected, TreeObservation):
        raise TypeError("完整树移除必须消费TreeObservation")
    _validate_path(expected.path)
    directories = {}
    files = {}
    records = []

    for relative, directory in expected.directories:
        if not isinstance(directory, DirectoryObservation):
            raise TypeError("完整树缺少正式目录观察")
        if (
            type(relative) is not str
            or relative in directories
            or relative in files
        ):
            raise ValueError("完整树目录位置非法或重复")
        _validate_tree_member_path(expected.path, relative, directory.path)
        directories[relative] = directory
        records.append((relative, "directory", ""))

    if "" not in directories:
        raise ValueError("完整树缺少固定普通目录根")

    for relative, observed in expected.files:
        if not isinstance(observed, FileObservation):
            raise TypeError("完整树缺少正式文件观察")
        if (
            type(relative) is not str
            or not relative
            or relative in directories
            or relative in files
        ):
            raise ValueError("完整树文件位置非法或重复")
        _validate_tree_member_path(expected.path, relative, observed.path)
        prepare_file_removal(observed)
        if (
            observed.digest is None
            or type(observed.digest) is not str
            or len(observed.digest) != 64
            or any(value not in "0123456789abcdef" for value in observed.digest)
        ):
            raise ValueError("完整树文件缺少固定内容指纹")
        files[relative] = observed
        records.append((relative, "file", observed.digest))

    for relative in (*directories, *files):
        if relative:
            parent = Path(relative).parent.as_posix()
            parent = "" if parent == "." else parent
            if parent not in directories:
                raise ValueError("完整树成员缺少固定普通父目录")

    canonical = tuple(sorted(
        records, key=lambda record: record[0].encode("utf-8"),
    ))
    if (
        expected.content_records != canonical
        or expected.integrity != content_integrity("directory", canonical)
    ):
        raise ValueError("完整树观察明细与内容完整性矛盾")

    requested_times = any(
        observed.mtime_ns is not None for observed in files.values()
    )
    times = tuple(
        (relative, observed.mtime_ns)
        for relative, observed in sorted(
            files.items(), key=lambda member: member[0].encode("utf-8"),
        )
    ) if requested_times else ()
    if (
        requested_times
        and any(type(modified) is not int for _, modified in times)
        or expected.file_times != times
    ):
        raise ValueError("完整树正式文件时间没有准确覆盖固定文件")


def _validate_tree_member_path(root, relative, path):
    if (
        relative
        and (
            relative.startswith("/")
            or "\\" in relative
            or any(part in ("", ".", "..") for part in relative.split("/"))
            or any(ord(character) < 32 for character in relative)
        )
        or path != (root / relative if relative else root)
    ):
        raise ValueError("完整树成员越出固定相对位置")


def prepare_tree_removal(expected):
    """完整观察明确作为移除范围；业务授权、保护和started归协调层。

    准备不观察磁盘、不创建目录、不删除内容。
    文件按UTF-8位置顺序移除，再自底向上移除空目录。
    """
    _validate_tree_observation(expected)
    file_steps = tuple(
        ("remove_file", relative, observed.path)
        for relative, observed in sorted(
            expected.files, key=lambda member: member[0].encode("utf-8"),
        )
    )
    directory_steps = tuple(
        ("remove_empty_directory", relative, directory.path)
        for relative, directory in sorted(
            expected.directories,
            key=lambda member: (
                -len(Path(member[0]).parts),
                member[0].encode("utf-8"),
            ),
        )
    )
    removal = object.__new__(PreparedTreeRemoval)
    for name, value in (
        ("expected", expected),
        ("steps", file_steps + directory_steps),
        ("_lifecycle", "prepared"),
        ("_results", ()),
        ("_current", None),
        ("_phase", "prepared"),
    ):
        object.__setattr__(removal, name, value)
    return removal


def cancel_tree_removal(removal):
    if not isinstance(removal, PreparedTreeRemoval):
        raise TypeError("取消必须消费PreparedTreeRemoval")
    if removal._lifecycle != "prepared":
        raise ValueError("完整树移除已经消费或取消")
    object.__setattr__(removal, "_lifecycle", "cancelled")


def _remaining_removal_tree(removal):
    """只扣除正常完成前缀；自有unlink减少同对象剩余名称链接数量。"""
    results = removal.results
    if len(results) > len(removal.steps):
        raise ValueError("完整树结果越出固定步骤")
    for result, (kind, _, path) in zip(results, removal.steps):
        if (
            not isinstance(result, FilesystemChangeResult)
            or not result.succeeded
            or result.kind != kind
            or result.target != path
        ):
            raise ValueError("剩余树只接受固定步骤真实成功前缀")

    removed = {
        relative for _, relative, _ in removal.steps[:len(results)]
    }
    expected = removal.expected
    removed_links = {}
    for relative, observed in expected.files:
        if relative in removed:
            number = removed_links.get(observed.object_identity, 0)
            removed_links[observed.object_identity] = number + 1

    records = tuple(
        record for record in expected.content_records
        if record[0] not in removed
    )
    remaining = replace(
        expected,
        directories=tuple(
            member for member in expected.directories
            if member[0] not in removed
        ),
        files=tuple(
            member for member in expected.files
            if member[0] not in removed
        ),
        content_records=records,
        integrity=content_integrity("directory", records) if records else None,
        file_times=tuple(
            member for member in expected.file_times
            if member[0] not in removed
        ),
    )
    # 整棵剩余树一次消费计数，避免逐文件重复复制和校验同一映射。
    return project_removed_file_links(remaining, removed_links)


def _recheck_remaining_removal(removal):
    remaining = _remaining_removal_tree(removal)
    if not remaining.content_records:
        _confirm_absent(removal.expected.path)
    else:
        recheck_tree(
            remaining,
            check_mtime=bool(removal.expected.file_times),
        )
    return remaining


def _prepare_remaining_removal_step(remaining, kind, relative):
    if kind == "remove_file":
        observed = dict(remaining.files)[relative]
        return prepare_file_removal(observed)

    directory = dict(remaining.directories)[relative]
    prefix = relative + "/" if relative else ""
    if any(
        position != relative
        and (not relative or position.startswith(prefix))
        for position, _, _ in remaining.content_records
    ):
        raise ValueError("固定目录仍有未移除成员，不能准备空目录移除")

    # 只投影原观察中同一目录的已扣除状态；
    # 不从后来扫描取得的新内容建立删除资格。
    empty = TreeObservation(
        directory.path, remaining.chain,
        (("", directory),), (),
        (("", "directory", ""),),
        content_integrity("directory", (("", "directory", ""),)), (),
    )
    return prepare_empty_directory_removal(empty)


def execute_tree_removal(removal, *, before_step=None):
    """固定完整树逐步移除；所属回调只复核来源及其他固定范围。

    回调不确认、不保护、不发布started、不改变移除范围。
    活动树剩余约束仍由本核心从真实成功前缀投影。
    """
    if not isinstance(removal, PreparedTreeRemoval):
        raise TypeError("执行必须消费PreparedTreeRemoval")
    if before_step is not None and not callable(before_step):
        raise TypeError("树步骤前复核必须是可调用对象")
    if removal._lifecycle != "prepared":
        raise ValueError("完整树移除已消费或取消，必须重新准备")
    object.__setattr__(removal, "_lifecycle", "attempted")
    object.__setattr__(removal, "_phase", "完整固定树开始前复核")
    object.__setattr__(removal, "_current", removal.expected.path)
    try:
        _recheck_remaining_removal(removal)
        for kind, relative, path in removal.steps:
            object.__setattr__(removal, "_current", path)
            if before_step is not None:
                object.__setattr__(removal, "_phase", "固定步骤前所属来源复核")
                before_step()
            object.__setattr__(removal, "_phase", "固定步骤前剩余树复核")
            remaining = _recheck_remaining_removal(removal)
            object.__setattr__(removal, "_phase", "固定剩余步骤准备")
            step = _prepare_remaining_removal_step(remaining, kind, relative)
            object.__setattr__(removal, "_phase", "固定名称步骤执行")
            try:
                result = execute_filesystem_change(step)
            except FilesystemChangeError as error:
                object.__setattr__(
                    removal, "_results", (*removal.results, error.result),
                )
                raise
            object.__setattr__(
                removal, "_results", (*removal.results, result),
            )
            object.__setattr__(removal, "_phase", "实际成功前缀后剩余树复核")
            _recheck_remaining_removal(removal)

    except (OSError, ValueError) as error:
        result = TreeRemovalResult(
            removal.expected.path, removal.results, False,
            removal.phase, removal.current, error,
        )
        raise TreeRemovalError(result) from error

    object.__setattr__(removal, "_lifecycle", "completed")
    object.__setattr__(removal, "_phase", "completed")
    object.__setattr__(removal, "_current", None)
    return TreeRemovalResult(
        removal.expected.path, removal.results, True, "completed", None,
    )


@dataclass(frozen=True, init=False)
class PreparedDirectoryAttributes:
    """固定目录身份和基础属性；准备不查询磁盘。"""

    expected: DirectoryObservation
    file_mode: int
    atime_ns: int
    mtime_ns: int
    durable_publication: bool
    _lifecycle: str

    def __init__(self, *arguments, **keywords):
        raise TypeError("目录属性步骤必须通过正式准备过程创建")


def prepare_directory_attributes(
    expected, *, file_mode, atime_ns, mtime_ns,
    durable_publication=False,
):
    if not isinstance(expected, DirectoryObservation):
        raise TypeError("目录属性必须消费DirectoryObservation")
    _validate_path(expected.path)
    if (
        type(file_mode) is not int
        or not 0 <= file_mode <= 0o7777
        or type(atime_ns) is not int
        or type(mtime_ns) is not int
    ):
        raise TypeError("目录基础权限和时间必须是合法整数")
    if type(durable_publication) is not bool:
        raise TypeError("目录持久化开关必须是布尔值")
    change = object.__new__(PreparedDirectoryAttributes)
    for name, value in (
        ("expected", expected), ("file_mode", file_mode),
        ("atime_ns", atime_ns), ("mtime_ns", mtime_ns),
        ("durable_publication", durable_publication),
        ("_lifecycle", "prepared"),
    ):
        object.__setattr__(change, name, value)
    return change


def cancel_directory_attributes(change):
    if not isinstance(change, PreparedDirectoryAttributes):
        raise TypeError("取消必须消费PreparedDirectoryAttributes")
    if change._lifecycle != "prepared":
        raise ValueError("目录属性步骤已消费或取消")
    object.__setattr__(change, "_lifecycle", "cancelled")


def _directory_identity_attributes(expected):
    target, chain = checked_chain(expected.path, include_observation=True)
    if chain[-1][1] != "directory":
        raise ValueError(f"固定目录存在性或类型变化：{target}")
    attributes = target.lstat()
    if (
        entity_type_from_attributes(target, attributes) != "directory"
        or ObjectIdentity(attributes.st_dev, attributes.st_ino)
        != expected.object_identity
    ):
        raise ValueError(f"固定目录对象身份变化：{target}")
    return attributes


def execute_directory_attributes(change):
    """一次设置基础时间及权限，正常返回后核验适用成果。

    后代范围由所属树协调复核。访问时间只作基础设置，
    不将其升级为后续读取之后仍须精确保持的恢复要求。
    """
    if not isinstance(change, PreparedDirectoryAttributes):
        raise TypeError("执行必须消费PreparedDirectoryAttributes")
    if change._lifecycle != "prepared":
        raise ValueError("目录属性步骤已消费或取消")
    object.__setattr__(change, "_lifecycle", "attempted")
    expected = change.expected
    phase = "固定目录身份及基础权限复核"
    attempted = False
    try:
        attributes = _directory_identity_attributes(expected)
        if stat.S_IMODE(attributes.st_mode) != stat.S_IMODE(
            expected.attributes.st_mode
        ):
            raise ValueError("固定目录基础权限变化")
        phase = "目录基础时间设置"
        attempted = True
        os.utime(
            expected.path, ns=(change.atime_ns, change.mtime_ns),
        )
        phase = "目录权限设置前身份复核"
        _directory_identity_attributes(expected)
        phase = "目录基础权限设置"
        os.chmod(expected.path, change.file_mode)
        phase = "目录基础属性结果核验"
        actual = _directory_identity_attributes(expected)
        if (
            actual.st_mtime_ns != change.mtime_ns
            or stat.S_IMODE(actual.st_mode) != change.file_mode
        ):
            raise OSError(f"目录基础属性未准确设置：{expected.path}")
        if change.durable_publication:
            phase = "目录属性持久化确认"
            sync_directory(expected.path)
    except (OSError, ValueError) as error:
        raise FilesystemChangeError(FilesystemChangeResult(
            expected.path, "set_directory_attributes",
            attempted, False, phase, error=error,
        )) from error
    object.__setattr__(change, "_lifecycle", "completed")
    return FilesystemChangeResult(
        expected.path, "set_directory_attributes", True, True, "completed",
    )


@dataclass(frozen=True)
class TreeCopyStepResult:
    """真实步骤结果及该步骤固定的对象身份约束。

    文件身份来自已准备成果，目录身份来自正常创建结果。
    身份不证明步骤完成，完成只消费change的正式结果。
    """

    change: FilesystemChangeResult | ContentPublicationResult
    object_identity: ObjectIdentity | None

    @property
    def target(self):
        return self.change.target

    @property
    def kind(self):
        return (
            "content" if isinstance(self.change, ContentPublicationResult)
            else self.change.kind
        )

    @property
    def attempted(self):
        return (
            self.change.mutation_attempted
            if isinstance(self.change, ContentPublicationResult)
            else self.change.attempted
        )

    @property
    def succeeded(self):
        return self.change.succeeded


@dataclass(frozen=True, init=False)
class PreparedTreeCopy:
    """固定来源、不存在目标、一次资格及有序真实执行现场。"""

    source: TreeObservation
    expected: FileObservation
    file_times: tuple
    durable_publication: bool
    _lifecycle: str
    _results: tuple
    _current: Path | None
    _phase: str
    _artifact: object

    def __init__(self, *arguments, **keywords):
        raise TypeError("独立树复制必须通过正式准备过程创建")

    @property
    def results(self):
        return self._results

    @property
    def current(self):
        return self._current

    @property
    def phase(self):
        return self._phase


@dataclass(frozen=True)
class TreeCopyResult:
    source: Path
    target: Path
    results: tuple
    completed: bool
    phase: str
    current: Path | None
    error: OSError | ValueError | None = None

    @property
    def attempted(self):
        return any(member.attempted for member in self.results)

    @property
    def succeeded(self):
        return self.error is None and self.completed and self.current is None


class TreeCopyError(OSError):
    def __init__(self, result):
        self.result = result
        super().__init__(
            f"固定独立树复制未完整结束：{result.source} -> {result.target}\n"
            f"阶段：{result.phase}；当前未确认位置："
            f"{result.current or '不适用'}\n"
            f"已确认成功步骤："
            f"{sum(member.succeeded for member in result.results)}\n"
            f"工作改变调用已发起：{'是' if result.attempted else '否'}\n"
            "前序真实结果保留；失败不证明没有副作用。"
            "资格已消费，不自动重试、删除工作成果或回滚。\n"
            f"{result.error}"
        )


def prepare_tree_copy(
    source, expected, *, durable_publication=False, file_times=None,
):
    """固定来源独立重建；正式恢复可显式消费清单权威文件时间。

    来源观察及其属性不改写。file_times只约束目标成果，
    不追认来源后来属性，不改变来源一致性检查。
    """
    _validate_tree_observation(source)
    prepare_directory_creation(expected)
    if overlaps(source.path, expected.path):
        raise ValueError("复制来源树与目标不得相同或父子重叠")
    if type(durable_publication) is not bool:
        raise TypeError("树复制持久化开关必须是布尔值")
    for relative, observed in source.files:
        if (
            not isinstance(observed.source_bytes, bytes)
            or len(observed.source_bytes) != observed.attributes.st_size
            or type(observed.mtime_ns) is not int
            or observed.mtime_ns != observed.attributes.st_mtime_ns
            or hashlib.sha256(observed.source_bytes).hexdigest()
            != observed.digest
        ):
            raise ValueError(
                f"复制来源缺少一致的完整bytes及正式时间：{relative}"
            )
    times = source.file_times if file_times is None else file_times
    if (
        type(times) is not tuple
        or any(
            type(member) is not tuple or len(member) != 2
            or type(member[0]) is not str or type(member[1]) is not int
            for member in times
        )
        or tuple(relative for relative, _ in times)
        != tuple(relative for relative, _ in source.files)
    ):
        raise ValueError("目标正式文件时间必须准确覆盖固定来源文件")
    copy = object.__new__(PreparedTreeCopy)
    for name, value in (
        ("source", source), ("expected", expected), ("file_times", times),
        ("durable_publication", durable_publication),
        ("_lifecycle", "prepared"), ("_results", ()),
        ("_current", None), ("_phase", "prepared"), ("_artifact", None),
    ):
        object.__setattr__(copy, name, value)
    return copy


def cancel_tree_copy(copy):
    if not isinstance(copy, PreparedTreeCopy):
        raise TypeError("取消必须消费PreparedTreeCopy")
    if copy._lifecycle != "prepared":
        raise ValueError("独立树复制已消费或取消")
    object.__setattr__(copy, "_lifecycle", "cancelled")


def _recheck_tree_copy_source(copy):
    """新边界比较固定来源，不用后来属性重建复制政策。"""
    current = recheck_tree(copy.source, check_mtime=True)
    fixed_directories = dict(copy.source.directories)
    for relative, directory in current.directories:
        fixed = fixed_directories[relative]
        if (
            stat.S_IMODE(directory.attributes.st_mode)
            != stat.S_IMODE(fixed.attributes.st_mode)
            or directory.attributes.st_mtime_ns
            != fixed.attributes.st_mtime_ns
        ):
            raise ValueError(f"固定复制来源目录属性变化：{directory.path}")
    fixed_files = dict(copy.source.files)
    for relative, observed in current.files:
        if stat.S_IMODE(observed.attributes.st_mode) != stat.S_IMODE(
            fixed_files[relative].attributes.st_mode
        ):
            raise ValueError(f"固定复制来源文件权限变化：{observed.path}")


def _tree_copy_target_constraints(copy):
    """目标状态只由实际成功前缀及当前完整自有暂存投影。"""
    directories, files, modes, times = {}, {}, {}, {}
    source_directories = dict(copy.source.directories)
    source_files = dict(copy.source.files)
    target_times = dict(copy.file_times)
    for receipt in copy.results:
        if not receipt.succeeded:
            raise ValueError("目标前缀只接受实际成功结果")
        relative = receipt.target.relative_to(copy.expected.path).as_posix()
        relative = "" if relative == "." else relative
        if receipt.kind == "content":
            source = source_files[relative]
            files[relative] = (
                receipt.object_identity, source.digest, target_times[relative],
                stat.S_IMODE(source.attributes.st_mode),
            )
        elif receipt.kind == "create_directory":
            directory = receipt.change.created_directory
            directories[relative] = receipt.object_identity
            modes[relative] = stat.S_IMODE(directory.attributes.st_mode)
        elif receipt.kind == "set_directory_attributes":
            source = source_directories[relative]
            modes[relative] = stat.S_IMODE(source.attributes.st_mode)
            times[relative] = source.attributes.st_mtime_ns
        else:
            raise ValueError("复制结果包含不属于固定树重建的步骤")

    artifact = copy._artifact
    if artifact is not None and artifact._lifecycle == "prepared":
        relative = artifact.path.relative_to(copy.expected.path).as_posix()
        if relative in files or relative in directories:
            raise ValueError("复制暂存位置与正式成功前缀冲突")
        files[relative] = (
            ObjectIdentity(*artifact.identity),
            artifact.digest, artifact.options.mtime_ns,
            artifact.options.file_mode,
        )
    return directories, files, modes, times


def _recheck_tree_copy_target(copy):
    """完整扫描是新边界比较，不从后来树取得新创建或删除授权。

    完整自有暂存明确参与约束，不能被当作外部新增对象，
    也不能按文件名模式忽略其他临时文件。
    """
    directories, files, modes, times = _tree_copy_target_constraints(copy)
    if not directories:
        recheck_file(copy.expected)
        return None
    current = observe_tree(copy.expected.path, include_mtime=True)
    if (
        set(dict(current.directories)) != set(directories)
        or set(dict(current.files)) != set(files)
    ):
        raise ValueError("复制目标结构偏离实际成功前缀")
    for relative, directory in current.directories:
        if (
            directory.object_identity != directories[relative]
            or stat.S_IMODE(directory.attributes.st_mode) != modes[relative]
            or relative in times
            and directory.attributes.st_mtime_ns != times[relative]
        ):
            raise ValueError(f"复制目标目录身份或属性变化：{directory.path}")
    for relative, observed in current.files:
        expected_identity, digest, modified, mode = files[relative]
        if (
            observed.object_identity != expected_identity
            or observed.link_count != 1
            or observed.digest != digest
            or observed.mtime_ns != modified
            or stat.S_IMODE(observed.attributes.st_mode) != mode
        ):
            raise ValueError(
                f"独立复制文件或暂存的身份、内容、链接或属性变化："
                f"{observed.path}"
            )
    return current


def _append_tree_copy_result(copy, change, object_identity=None):
    receipt = TreeCopyStepResult(change, object_identity)
    object.__setattr__(copy, "_results", (*copy.results, receipt))


def _dispose_tree_copy_artifact(copy):
    artifact = copy._artifact
    if artifact is None:
        return
    if artifact._lifecycle not in ("completed", "disposed"):
        try:
            dispose_content_artifact(artifact)
        except (OSError, ValueError) as cleanup_error:
            # 所有权拒绝或清理故障不替换主失败，不删除工作树。
            print(
                f"[树复制文件暂存清理失败] {artifact.path}：{cleanup_error}",
                file=sys.stderr,
            )
            return
    object.__setattr__(copy, "_artifact", None)


def execute_tree_copy(copy, *, recheck_context=None):
    """固定独立重建，直接组合目录步骤、文件成果及唯一内容发布。

    来源新边界读取只服务复核，工作文件仍消费已有固定bytes。
    程序缺陷自然传播，资格已消费，现场及实际结果继续保留。
    """
    if not isinstance(copy, PreparedTreeCopy):
        raise TypeError("执行必须消费PreparedTreeCopy")
    if recheck_context is not None and not callable(recheck_context):
        raise TypeError("树复制所属范围复核必须是可调用对象")
    if copy._lifecycle != "prepared":
        raise ValueError("独立树复制已消费或取消，必须重新准备")
    object.__setattr__(copy, "_lifecycle", "attempted")

    def check_source_context():
        if recheck_context is not None:
            recheck_context()
        _recheck_tree_copy_source(copy)
    object.__setattr__(copy, "_current", copy.expected.path)
    object.__setattr__(copy, "_phase", "完整固定来源及不存在目标复核")
    try:
        check_source_context()
        _recheck_tree_copy_target(copy)
        _check_creation_parent(copy.expected.path)
        directories = sorted(
            copy.source.directories,
            key=lambda member: (
                len(Path(member[0]).parts), member[0].encode("utf-8"),
            ),
        )
        for relative, _ in directories:
            target = copy.expected.path / relative
            object.__setattr__(copy, "_current", target)
            object.__setattr__(copy, "_phase", "目录创建前来源及成功前缀复核")
            check_source_context()
            _recheck_tree_copy_target(copy)
            expected = (
                copy.expected if relative == "" else observe_file(target)
            )
            step = prepare_directory_creation(expected)
            object.__setattr__(copy, "_phase", "固定目录创建")
            try:
                result = execute_filesystem_change(step)
            except FilesystemChangeError as error:
                _append_tree_copy_result(copy, error.result)
                raise
            _append_tree_copy_result(
                copy, result, result.created_directory.object_identity,
            )
            object.__setattr__(copy, "_phase", "目录创建后成功前缀复核")
            _recheck_tree_copy_target(copy)

        target_times = dict(copy.file_times)
        for relative, source in copy.source.files:
            target = copy.expected.path / relative
            object.__setattr__(copy, "_current", target)
            object.__setattr__(copy, "_phase", "文件准备前来源及成功前缀复核")
            check_source_context()
            _recheck_tree_copy_target(copy)
            expected = observe_file(target)
            if expected.kind != "absent":
                raise ValueError(f"固定复制文件位置不再不存在：{target}")
            object.__setattr__(copy, "_phase", "固定来源完整文件成果准备")
            artifact = prepare_copy_content_artifact(
                source, expected, preserve_target_mode=False,
                options=ContentPublicationOptions(
                    durable_publication=copy.durable_publication,
                    mtime_ns=target_times[relative],
                ),
            )
            object.__setattr__(copy, "_artifact", artifact)
            if (
                artifact.mechanism != "atomic_replace"
                or artifact.digest != source.digest
            ):
                raise ValueError("复制文件成果不对应固定来源或独立目标机制")
            object.__setattr__(copy, "_phase", "完整文件准备后来源及目标复核")
            check_source_context()
            _recheck_tree_copy_target(copy)
            object.__setattr__(copy, "_phase", "固定文件成果发布")
            prepared_identity = ObjectIdentity(*artifact.identity)
            try:
                result = publish_content(artifact)
            except ContentPublicationError as error:
                _append_tree_copy_result(
                    copy, error.result, prepared_identity,
                )
                raise
            # 先保留正式发布事实；随后核验失败不能抹去此前结果。
            _append_tree_copy_result(copy, result, prepared_identity)
            object.__setattr__(copy, "_artifact", None)
            object.__setattr__(copy, "_phase", "文件发布后来源及成功前缀复核")
            check_source_context()
            _recheck_tree_copy_target(copy)

        for relative, source in reversed(directories):
            target = copy.expected.path / relative
            object.__setattr__(copy, "_current", target)
            object.__setattr__(copy, "_phase", "目录属性前来源及成功前缀复核")
            check_source_context()
            current = _recheck_tree_copy_target(copy)
            expected = dict(current.directories)[relative]
            attributes = source.attributes
            step = prepare_directory_attributes(
                expected,
                file_mode=stat.S_IMODE(attributes.st_mode),
                atime_ns=attributes.st_atime_ns,
                mtime_ns=attributes.st_mtime_ns,
                durable_publication=copy.durable_publication,
            )
            object.__setattr__(copy, "_phase", "固定目录基础属性设置")
            try:
                result = execute_directory_attributes(step)
            except FilesystemChangeError as error:
                _append_tree_copy_result(
                    copy, error.result, expected.object_identity,
                )
                raise
            _append_tree_copy_result(
                copy, result, expected.object_identity,
            )
            object.__setattr__(copy, "_phase", "目录属性后成功前缀复核")
            _recheck_tree_copy_target(copy)

        object.__setattr__(copy, "_current", copy.expected.path)
        object.__setattr__(copy, "_phase", "完整来源及独立目标最终核验")
        _recheck_tree_copy_source(copy)
        actual = _recheck_tree_copy_target(copy)
        if (
            actual.content_records != copy.source.content_records
            or actual.file_times != copy.file_times
        ):
            raise OSError("独立复制树完整内容或正式文件时间不符")
        if copy.durable_publication:
            object.__setattr__(copy, "_phase", "复制树父目录持久化确认")
            sync_directory(copy.expected.path.parent)
    except (OSError, ValueError) as error:
        raise TreeCopyError(TreeCopyResult(
            copy.source.path, copy.expected.path, copy.results, False,
            copy.phase, copy.current, error,
        )) from error
    finally:
        # 不删除已经创建的目录或已发布文件。
        # 清理失败保留成果引用，不能把未知对象当成本次暂存删除。
        _dispose_tree_copy_artifact(copy)

    object.__setattr__(copy, "_lifecycle", "completed")
    object.__setattr__(copy, "_phase", "completed")
    object.__setattr__(copy, "_current", None)
    return TreeCopyResult(
        copy.source.path, copy.expected.path, copy.results,
        True, "completed", None,
    )
