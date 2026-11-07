# -*- coding: utf-8 -*-

"""具体固定改变到唯一保护范围、编号关联和预期的纯投影。

不解析OP、不查询磁盘、不准备材料或执行工作。
归并保护祖先不扩大具体effects的改变授权。
"""

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from core.file_observation import (
    DirectoryObservation, FileObservation, ObjectIdentity, TreeObservation,
)
from core.recovery_content import content_integrity
from core.recovery_paths import absolute_path, contains, identity, validate_component


@dataclass(frozen=True)
class ContentState:
    kind: str
    records: tuple
    integrity: object


@dataclass(frozen=True)
class FixedEffect:
    before: FileObservation | TreeObservation
    after: ContentState


@dataclass(frozen=True)
class FixedUnit:
    """解释层明确选择effects；reads只读，不自动成为保护对象。"""

    members: tuple
    effects: tuple
    reads: tuple = ()


@dataclass(frozen=True)
class ProtectionObject:
    object_id: str
    before: FileObservation | TreeObservation
    original: ContentState
    file_times: tuple

    @property
    def target(self):
        return self.before.path

    @property
    def original_type(self):
        return self.original.kind


@dataclass(frozen=True)
class ProtectionUnit:
    unit_id: str
    fixed: FixedUnit
    object_ids: tuple
    parent_directories: tuple
    tree_directories: tuple

    @property
    def members(self):
        return self.fixed.members

    @property
    def effects(self):
        return self.fixed.effects


@dataclass(frozen=True, init=False)
class ProtectionProjection:
    """正式构建过程形成；不是可任意拼装的第二保护计划。"""

    objects: tuple
    units: tuple
    forbidden_roots: tuple

    def __init__(self, *arguments, **keywords):
        raise TypeError("保护投影必须通过build_protection_projection创建")


def validate_absolute_path(path):
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path == path.parent
        or ".." in path.parts
        or absolute_path(path) != path
    ):
        raise ValueError("固定位置必须是规范、非根的明确绝对Path")
    for component in path.parts:
        if component != path.anchor:
            validate_component(component)


def validate_relative_position(relative):
    if type(relative) is not str:
        raise ValueError("内容相对位置必须是字符串")
    if relative and (
        relative.startswith("/")
        or "\\" in relative
        or ":" in relative
        or any(part in ("", ".", "..") for part in relative.split("/"))
        or any(ord(character) < 32 for character in relative)
    ):
        raise ValueError("内容相对位置非法")
    for component in relative.split("/") if relative else ():
        validate_component(component)


def _valid_digest(digest):
    return (
        type(digest) is str
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def validate_content_state(state):
    if not isinstance(state, ContentState):
        raise TypeError("内容状态必须是ContentState")
    if type(state.kind) is not str or state.kind not in (
        "absent", "file", "directory",
    ):
        raise ValueError("内容状态类型非法")
    if type(state.records) is not tuple:
        raise TypeError("内容记录必须是不可变元组")
    if state.kind == "absent":
        if state.records or state.integrity is not None:
            raise ValueError("不存在状态不得携带内容")
        return

    known = {}
    previous = None
    for record in state.records:
        if type(record) is not tuple or len(record) != 3:
            raise ValueError("内容记录非法")
        relative, kind, digest = record
        validate_relative_position(relative)
        if type(kind) is not str or kind not in ("file", "directory"):
            raise ValueError("内容成员类型非法")
        if (
            kind == "file" and not _valid_digest(digest)
            or kind == "directory" and digest != ""
        ):
            raise ValueError("内容成员摘要非法")
        position = relative.encode("utf-8")
        if previous is not None and position <= previous:
            raise ValueError("内容位置必须唯一且按UTF-8排序")
        previous = position
        key = relative.casefold()
        if key in known:
            raise ValueError("内容包含Windows同名位置")
        if relative and known.get(
            relative.rpartition("/")[0].casefold(),
        ) != "directory":
            raise ValueError("内容记录缺少普通父目录")
        known[key] = kind

    if not state.records or state.records[0][:2] != ("", state.kind):
        raise ValueError("内容状态缺少匹配根")
    if state.kind == "file" and len(state.records) != 1:
        raise ValueError("文件状态不得携带后代")
    if state.integrity != content_integrity(state.kind, state.records):
        raise ValueError("内容记录与完整性矛盾")


def absent_state():
    return ContentState("absent", (), None)


def state_from_bytes(content):
    if not isinstance(content, bytes):
        raise TypeError("文件成果必须是完整不可变bytes")
    records = (("", "file", hashlib.sha256(content).hexdigest()),)
    return ContentState(
        "file", records,
        MappingProxyType(content_integrity("file", records)),
    )


def _validate_identity(identifier, attributes):
    if (
        not isinstance(identifier, ObjectIdentity)
        or type(identifier.device) is not int
        or identifier.device < 0
        or type(identifier.inode) is not int
        or identifier.inode <= 0
        or identifier.device != attributes.st_dev
        or identifier.inode != attributes.st_ino
    ):
        raise ValueError("最初对象缺少可靠且对应属性的身份")


def _validate_chain(observed, kind):
    chain = observed.chain
    if type(chain) is not tuple or not chain:
        raise ValueError("固定观察缺少原路径链")
    paths = tuple(
        member[0] for member in chain
        if type(member) is tuple and len(member) == 2
    )
    expected_paths = (*reversed(observed.path.parents), observed.path)
    if paths != expected_paths:
        raise ValueError("固定观察原路径链没有准确覆盖目标祖先")
    for number, (path, current_kind) in enumerate(chain):
        if not isinstance(path, Path) or type(current_kind) is not str:
            raise ValueError("固定观察路径链成员非法")
        if number < len(chain) - 1:
            if current_kind not in ("directory", "absent"):
                raise ValueError("固定观察父链不是普通目录或不存在")
        elif current_kind != kind:
            raise ValueError("固定观察末级类型与对象矛盾")
    missing = False
    for _, current_kind in chain:
        if missing and current_kind != "absent":
            raise ValueError("不存在祖先不能包含最初已存在后代")
        missing |= current_kind == "absent"


def _file_state(observed):
    validate_absolute_path(observed.path)
    _validate_chain(observed, observed.kind)
    if observed.kind == "absent":
        if any(value is not None for value in (
            observed.attributes, observed.object_identity, observed.link_count,
            observed.source_bytes, observed.digest, observed.mtime_ns,
        )):
            raise ValueError("不存在观察不得携带既有对象事实")
        return absent_state(), ()
    if observed.kind != "file":
        raise ValueError("最初文件观察类型非法")
    attributes = observed.attributes
    if attributes is None or not stat.S_ISREG(attributes.st_mode):
        raise ValueError("最初文件缺少普通文件属性")
    _validate_identity(observed.object_identity, attributes)
    if (
        type(observed.link_count) is not int
        or observed.link_count < 1
        or observed.link_count != attributes.st_nlink
        or not isinstance(observed.source_bytes, bytes)
        or len(observed.source_bytes) != attributes.st_size
        or type(observed.mtime_ns) is not int
        or observed.mtime_ns != attributes.st_mtime_ns
    ):
        raise ValueError("最初文件缺少完整bytes、链接或正式时间")
    digest = hashlib.sha256(observed.source_bytes).hexdigest()
    if observed.digest is not None and observed.digest != digest:
        raise ValueError("最初文件摘要与固定bytes矛盾")
    records = (("", "file", digest),)
    return ContentState(
        "file", records,
        MappingProxyType(content_integrity("file", records)),
    ), (("", observed.mtime_ns),)


def observation_state(observed):
    """完整输入校验和状态形成一次；结果供保护及清单直接复用。"""
    if isinstance(observed, FileObservation):
        return _file_state(observed)
    if not isinstance(observed, TreeObservation):
        raise TypeError("最初状态必须是正式文件或完整树观察")
    validate_absolute_path(observed.path)
    _validate_chain(observed, "directory")
    if type(observed.directories) is not tuple or type(observed.files) is not tuple:
        raise TypeError("最初树成员必须是不可变元组")

    directories = {}
    records = []
    for relative, directory in observed.directories:
        validate_relative_position(relative)
        if (
            not isinstance(directory, DirectoryObservation)
            or relative in directories
            or directory.path != observed.path / relative
            or not stat.S_ISDIR(directory.attributes.st_mode)
        ):
            raise ValueError("最初树目录位置、类型或属性非法")
        _validate_identity(directory.object_identity, directory.attributes)
        directories[relative] = directory
        records.append((relative, "directory", ""))
    if "" not in directories:
        raise ValueError("最初树缺少普通目录根")
    file_positions = set()
    times = []
    for relative, member in observed.files:
        validate_relative_position(relative)
        if (
            not relative
            or relative in directories
            or relative in file_positions
            or not isinstance(member, FileObservation)
            or member.path != observed.path / relative
        ):
            raise ValueError("最初树文件位置非法或重复")
        state, file_times = _file_state(member)
        if state.kind != "file":
            raise ValueError("最初树文件成员必须是普通文件")
        file_positions.add(relative)
        records.append((relative, "file", state.records[0][2]))
        times.append((relative, file_times[0][1]))
    canonical = tuple(sorted(
        records, key=lambda record: record[0].encode("utf-8"),
    ))
    state = ContentState(
        "directory", canonical,
        MappingProxyType(content_integrity("directory", canonical)),
    )
    validate_content_state(state)
    if (
        observed.content_records != canonical
        or observed.integrity != state.integrity
        or observed.file_times != tuple(times)
        or tuple(times) != tuple(sorted(
            times, key=lambda member: member[0].encode("utf-8"),
        ))
    ):
        raise ValueError("最初树成员、完整性或正式时间矛盾")
    return state, tuple(times)


def state_from_observation(observed):
    return observation_state(observed)[0]


def _missing_parents(effect):
    if effect.after.kind == "absent":
        return ()
    return tuple(
        path for path, kind in effect.before.chain[:-1]
        if kind == "absent"
    )


def _absent_ancestor(observed, target):
    chain = observed.chain[:next(
        number for number, (path, _) in enumerate(observed.chain)
        if path == target
    ) + 1]
    return FileObservation(
        target, chain, "absent", None, None, None, None, None, None,
    )


def build_protection_projection(units, *, forbidden_roots):
    """一次形成保护对象和所有关联；不查询或改变磁盘。"""
    units = tuple(units)
    forbidden = tuple(forbidden_roots)
    for root in forbidden:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("禁止保护根必须是明确绝对Path")

    states = {}
    candidates = {}
    associations = []
    prepared_units = []
    seen_members = set()
    changed_targets = []

    def fixed_state(observed):
        key = identity(observed.path)
        previous = states.get(key)
        if previous is not None:
            if previous[0] is not observed and previous[0] != observed:
                raise ValueError("同一位置存在矛盾的最初观察")
            return previous[1], previous[2]
        state, times = observation_state(observed)
        states[key] = (observed, state, times)
        return state, times

    for unit in units:
        if not isinstance(unit, FixedUnit):
            raise TypeError("保护投影必须消费FixedUnit")
        if (
            type(unit.members) is not tuple or not unit.members
            or any(type(member) is not str or not member for member in unit.members)
            or len(set(unit.members)) != len(unit.members)
            or seen_members.intersection(unit.members)
        ):
            raise ValueError("共同成员必须非空、唯一且只属于一个单元")
        seen_members.update(unit.members)
        if type(unit.effects) is not tuple or not unit.effects:
            raise ValueError("实际改变单元必须具有具体effects")
        if type(unit.reads) is not tuple:
            raise TypeError("只读来源必须是不可变元组")
        for source in unit.reads:
            fixed_state(source)

        keys = []
        parents = {}
        trees = {}
        for effect in unit.effects:
            if not isinstance(effect, FixedEffect):
                raise TypeError("具体改变必须是FixedEffect")
            original, times = fixed_state(effect.before)
            validate_content_state(effect.after)
            target = effect.before.path
            if any(
                contains(target, previous) or contains(previous, target)
                for previous in changed_targets
            ):
                raise ValueError("具体改变位置重复或父子重叠")
            changed_targets.append(target)
            if any(contains(target, root) for root in forbidden):
                raise ValueError("具体改变范围包含业务定位根")
            missing = _missing_parents(effect)
            for parent in missing:
                parents.setdefault(identity(parent), parent)
            protected = missing[0] if missing else target
            validate_absolute_path(protected)
            if any(contains(protected, root) for root in forbidden):
                raise ValueError("保护范围包含业务定位根")
            if missing:
                before = _absent_ancestor(effect.before, protected)
                protected_state, protected_times = absent_state(), ()
            else:
                before = effect.before
                protected_state, protected_times = original, times
            key = identity(protected)
            candidate = (before, protected_state, protected_times)
            previous = candidates.setdefault(key, candidate)
            if previous != candidate:
                raise ValueError("同一保护位置最初事实矛盾")
            keys.append(key)
            for relative, kind, _ in effect.after.records:
                if kind == "directory":
                    directory = target / relative
                    trees.setdefault(identity(directory), directory)
        if set(parents).intersection(trees):
            raise ValueError("必要父目录与成果树目录重复")
        associations.append(tuple(dict.fromkeys(keys)))
        prepared_units.append((unit, parents, trees))

    retained = {}
    aliases = {}
    for key, candidate in sorted(
        candidates.items(),
        key=lambda member: (len(member[1][0].path.parts), str(member[1][0].path)),
    ):
        before, state, _ = candidate
        ancestor = next((
            previous_key for previous_key, previous in retained.items()
            if contains(previous[0].path, before.path)
        ), None)
        if ancestor is None:
            retained[key] = candidate
            aliases[key] = key
        else:
            if retained[ancestor][1].kind != "absent" or state.kind != "absent":
                raise ValueError("已有原状态保护对象不得父子重叠")
            aliases[key] = ancestor

    objects = tuple(
        ProtectionObject(f"o{number:06d}", before, state, times)
        for number, (before, state, times) in enumerate(retained.values(), 1)
    )
    identifiers = {
        identity(member.target): member.object_id for member in objects
    }
    order = lambda path: (len(path.parts), str(path))
    projected = tuple(
        ProtectionUnit(
            f"u{number:06d}", unit,
            tuple(dict.fromkeys(identifiers[aliases[key]] for key in keys)),
            tuple(sorted(parents.values(), key=order)),
            tuple(sorted(trees.values(), key=order)),
        )
        for number, ((unit, parents, trees), keys) in enumerate(
            zip(prepared_units, associations), 1,
        )
    )
    projection = object.__new__(ProtectionProjection)
    object.__setattr__(projection, "objects", objects)
    object.__setattr__(projection, "units", projected)
    object.__setattr__(projection, "forbidden_roots", forbidden)
    return projection
