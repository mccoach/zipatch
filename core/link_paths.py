# -*- coding: utf-8 -*-

"""Windows链接入口、直接目标及实际访问位置的固定元信息观察。

只查询路径属性和链接定义，不打开正文、不枚举目录、不改变对象。
观察不授予读取正文、展开目录、创建链接或修改目标的权限。
既有checked_chain继续拒绝链接；业务必须显式消费本模块的事实，
完成预演授权后再进入对应的正式内容或名称操作。

链接定义保留原始表达；direct_target表示原定义直接引用的位置，
不把中间链接压平为最终对象。actual_path只表示本次解析结果。
检查不是文件锁，不承诺消除系统调用之间的全部竞态。
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from core.file_observation import ObjectIdentity
from core.recovery_paths import (
    UnsafePathError, absolute_path, entity_type_from_attributes,
    contains, identity, validate_component,
)


_REPARSE_POINT = 0x400
_DIRECTORY_ATTRIBUTE = 0x10
_SYMLINK_TAG = 0xA000000C
_MOUNT_POINT_TAG = 0xA0000003

# 明确的有限探查政策，不将其解释为Windows的统一平台限制。
# 状态重复检测处理确定循环；上限处理不断增长的递归路径。
MAX_LINK_EXPANSIONS = 64
_LINK_KINDS = frozenset((
    "file_symlink", "directory_symlink", "junction",
))


@dataclass(frozen=True)
class LinkDefinition:
    """实际入口的原定义和直接引用位置；不是目标内容或创建授权。"""

    entry_path: Path
    kind: str
    object_identity: ObjectIdentity
    reparse_tag: int
    raw_target: str
    relative: bool
    direct_target: Path
    revision: tuple


@dataclass(frozen=True)
class PathDirectoryDependency:
    """只固定访问所依赖的目录身份，不因此取得恢复或删除资格。"""

    path: Path
    object_identity: ObjectIdentity


@dataclass(frozen=True, init=False)
class LinkPathObservation:
    """正式探查创建的不可变事实；不提供文件系统执行资格。

    follow_final_link=False时，末级链接保留为entry_link，
    actual_path指向该链接入口，而不是它的目标。
    followed_links仅包含本次实际解析经过的链接。
    first_missing固定最先缺失的位置，不将失效链接当作普通缺失入口。
    """

    nominal_path: Path
    actual_path: Path
    kind: str
    object_identity: ObjectIdentity | None
    followed_links: tuple
    entry_link: LinkDefinition | None
    directories: tuple
    first_missing: Path | None
    follow_final_link: bool

    def __init__(self, *arguments, **keywords):
        raise TypeError("链接路径事实必须通过inspect_link_path创建")

    @property
    def has_links(self):
        return bool(self.followed_links) or self.entry_link is not None

    @property
    def target_unavailable(self):
        return self.kind == "absent" and bool(self.followed_links)

    @property
    def path(self):
        """结构消费者统一使用实际入口位置，不以名义位置代替。"""
        return self.actual_path

    @property
    def chain(self):
        """只为未经过链接的结构入口提供普通父链。

        已解析的内容访问依赖仍由原路径观察拥有，不能伪装成
        checked_chain的普通父链，借此绕过所属业务授权。
        """
        if self.followed_links or self.nominal_path != self.actual_path:
            raise ValueError("经过链接的访问路径不能投影为普通结构父链")
        parents = {
            member.path: member for member in self.directories
        }
        positions = (*reversed(self.actual_path.parents),)
        if any(path not in parents for path in positions):
            raise ValueError("结构入口缺少完整普通父目录身份")
        return (
            *((path, "directory") for path in positions),
            (self.actual_path, self.kind),
        )


def _require_windows():
    if os.name != "nt":
        raise OSError("本项目链接路径探查只支持Windows环境")


def _object_identity(attributes, path):
    if (
        type(attributes.st_dev) is not int
        or attributes.st_dev < 0
        or type(attributes.st_ino) is not int
        or attributes.st_ino <= 0
    ):
        raise OSError(f"无法取得可靠的路径对象身份：{path}")
    return ObjectIdentity(attributes.st_dev, attributes.st_ino)


def _entry_kind(path, attributes):
    """链接分类独立于普通对象政策；普通对象仍消费既有唯一分类。"""
    reparse = bool(
        getattr(attributes, "st_file_attributes", 0) & _REPARSE_POINT
    )
    symbolic = stat.S_ISLNK(attributes.st_mode)
    if not reparse and not symbolic:
        return entity_type_from_attributes(path, attributes)

    tag = getattr(attributes, "st_reparse_tag", None)
    if tag == _SYMLINK_TAG:
        directory = bool(
            getattr(attributes, "st_file_attributes", 0)
            & _DIRECTORY_ATTRIBUTE
        )
        return "directory_symlink" if directory else "file_symlink"
    if tag == _MOUNT_POINT_TAG:
        if not (
            getattr(attributes, "st_file_attributes", 0)
            & _DIRECTORY_ATTRIBUTE
        ):
            raise UnsafePathError(
                f"目录联接标签没有目录属性：{path}",
                kind="invalid_junction",
            )
        return "junction"
    raise UnsafePathError(
        f"不支持的Windows重解析点标签：{path}；tag={tag!r}",
        kind="unsupported_reparse_tag",
    )


def _link_revision(attributes):
    return (
        attributes.st_size,
        attributes.st_mtime_ns,
        attributes.st_ctime_ns,
        getattr(attributes, "st_file_attributes", 0),
        getattr(attributes, "st_reparse_tag", None),
    )


def _validate_path_spelling(path, *, allow_navigation=False):
    """名称检查先于abspath，避免先规范化隐藏非法名称或上级导航。"""
    original = Path(path)
    spelling = os.fspath(path).replace("/", "\\")
    if spelling.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        raise UnsafePathError(
            f"输入路径不支持设备或扩展设备表达：{path}",
            kind="device_path",
        )
    if original.drive and not original.root:
        raise UnsafePathError(
            f"不支持依赖驱动器当前目录的路径：{path}",
            kind="drive_relative",
        )
    if original.root and not original.drive:
        raise UnsafePathError(
            f"路径缺少明确盘符或共享根：{path}",
            kind="root_relative",
        )
    for component in original.parts:
        if component == original.anchor or component == ".":
            continue
        if component == "..":
            if allow_navigation:
                continue
            raise UnsafePathError(
                f"名义路径不得包含上级导航：{path}",
                kind="parent_navigation",
            )
        validate_component(component)
    return original


def _nominal_path(path):
    original = _validate_path_spelling(path)
    return absolute_path(original)


def _direct_target(entry, kind, raw):
    """转换系统返回的目标表达，但原表达完整保存在LinkDefinition中。"""
    if type(raw) is not str or not raw:
        raise UnsafePathError(
            f"链接定义缺少有效目标：{entry}",
            kind="invalid_link_definition",
        )
    if any(ord(character) < 32 for character in raw):
        raise UnsafePathError(
            f"链接定义含控制字符：{entry}",
            kind="invalid_link_definition",
        )
    spelling = raw.replace("/", "\\")
    if spelling.startswith("\\\\?\\UNC\\"):
        spelling = "\\\\" + spelling[len("\\\\?\\UNC\\"):]
    elif spelling.startswith("\\??\\UNC\\"):
        spelling = "\\\\" + spelling[len("\\??\\UNC\\"):]
    elif spelling.startswith("\\\\?\\"):
        spelling = spelling[len("\\\\?\\"):]
        if not (
            len(spelling) >= 3
            and spelling[0].isascii()
            and spelling[0].isalpha()
            and spelling[1:3] == ":\\"
        ):
            raise UnsafePathError(
                f"不支持卷挂载或其他设备目标：{entry}；目标={raw!r}",
                kind="unsupported_link_target",
            )
    elif spelling.startswith("\\??\\"):
        spelling = spelling[len("\\??\\"):]
        if not (
            len(spelling) >= 3
            and spelling[0].isascii()
            and spelling[0].isalpha()
            and spelling[1:3] == ":\\"
        ):
            raise UnsafePathError(
                f"不支持卷挂载或其他设备目标：{entry}；目标={raw!r}",
                kind="unsupported_link_target",
            )

    target = _validate_path_spelling(
        spelling, allow_navigation=True,
    )
    relative = not target.is_absolute()
    if kind == "junction" and relative:
        raise UnsafePathError(
            f"目录联接必须具有明确绝对目标：{entry}",
            kind="invalid_junction",
        )
    located = entry.parent / target if relative else target
    return relative, absolute_path(located)


def _read_definition(path, attributes, kind):
    """一次读取原定义；前后元信息一致才接受该定义。"""
    identifier = _object_identity(attributes, path)
    try:
        raw = os.readlink(path)
    except PermissionError as error:
        code = getattr(error, "winerror", None)
        detail = f"；Windows错误码：{code}" if code is not None else ""
        raise PermissionError(
            f"无法读取链接入口的原定义：{path}\n"
            f"入口类型：{kind}；系统拒绝访问{detail}。\n"
            "仅取得入口属性不代表能够读取链接定义。"
            "本次停止，不猜测指向、不访问引用目标、不修改权限或重建入口。\n"
            f"原始错误：{error}"
        ) from error
    current = path.lstat()
    if (
        _entry_kind(path, current) != kind
        or _object_identity(current, path) != identifier
        or _link_revision(current) != _link_revision(attributes)
    ):
        raise ValueError(f"链接定义读取期间入口前提变化：{path}")
    relative, target = _direct_target(path, kind, raw)
    return LinkDefinition(
        path, kind, identifier,
        getattr(attributes, "st_reparse_tag"),
        raw, relative, target, _link_revision(current),
    )


def _attributes_or_absent(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _components(path):
    return (*reversed(path.parents), path)


def _record_directory(dependencies, path, attributes):
    observed = PathDirectoryDependency(
        path, _object_identity(attributes, path),
    )
    key = identity(path)
    previous = dependencies.setdefault(key, observed)
    if previous != observed:
        raise ValueError(f"链接探查期间目录身份变化：{path}")


def _confirm_directory(dependency):
    attributes = dependency.path.lstat()
    if (
        _entry_kind(dependency.path, attributes) != "directory"
        or _object_identity(attributes, dependency.path)
        != dependency.object_identity
    ):
        raise ValueError(
            f"链接探查结束时访问目录前提变化：{dependency.path}"
        )


def _confirm_link(definition):
    attributes = definition.entry_path.lstat()
    if (
        _entry_kind(definition.entry_path, attributes) != definition.kind
        or _object_identity(attributes, definition.entry_path)
        != definition.object_identity
        or _link_revision(attributes) != definition.revision
    ):
        raise ValueError(
            f"链接探查结束时入口前提变化：{definition.entry_path}"
        )


def _finish(
    nominal, actual, kind, identifier, followed, entry_link,
    dependencies, first_missing, follow_final_link,
):
    """结束边界只复核元信息，不重读定义或扫描目标内容。"""
    for directory in dependencies.values():
        _confirm_directory(directory)
    for definition in followed:
        _confirm_link(definition)
    if entry_link is not None:
        _confirm_link(entry_link)

    if first_missing is not None:
        if _attributes_or_absent(first_missing) is not None:
            raise ValueError(
                f"链接探查结束时缺失位置已出现：{first_missing}"
            )
    else:
        attributes = actual.lstat()
        if (
            _entry_kind(actual, attributes) != kind
            or _object_identity(attributes, actual) != identifier
        ):
            raise ValueError(f"链接探查结束时实际对象前提变化：{actual}")

    observed = object.__new__(LinkPathObservation)
    for name, value in (
        ("nominal_path", nominal),
        ("actual_path", actual),
        ("kind", kind),
        ("object_identity", identifier),
        ("followed_links", tuple(followed)),
        ("entry_link", entry_link),
        ("directories", tuple(dependencies.values())),
        ("first_missing", first_missing),
        ("follow_final_link", follow_final_link),
    ):
        object.__setattr__(observed, name, value)
    return observed


def inspect_link_path(path, *, follow_final_link):
    """探查授权窗口所需的最小路径事实，不取得正文读取资格。

    父目录链接需要解析才能定位末级对象。
    follow_final_link明确决定末级链接是访问依赖还是操作入口。
    缺失祖先出现后不继续查询其后代，actual_path保留完整预计位置。

    mount-point标签只接受可定位的普通盘符或UNC目标；
    卷GUID挂载及其他设备目标不作为目录联接放行。
    """
    _require_windows()
    if type(follow_final_link) is not bool:
        raise TypeError("跟随末级链接开关必须是布尔值")

    nominal = _nominal_path(path)
    pending = nominal
    followed = []
    directories = {}
    expanded_states = set()

    while True:
        chain = _components(pending)
        for number, candidate in enumerate(chain):
            attributes = _attributes_or_absent(candidate)
            if attributes is None:
                return _finish(
                    nominal, pending, "absent", None, followed, None,
                    directories, candidate, follow_final_link,
                )

            kind = _entry_kind(candidate, attributes)
            last = number == len(chain) - 1
            if kind in _LINK_KINDS:
                definition = _read_definition(candidate, attributes, kind)
                if last and not follow_final_link:
                    return _finish(
                        nominal, candidate, kind,
                        definition.object_identity, followed, definition,
                        directories, None, follow_final_link,
                    )
                if not last and kind == "file_symlink":
                    raise UnsafePathError(
                        f"父路径是文件软链接，不能作为目录访问：{candidate}",
                        kind="parent_not_directory",
                    )
                suffix = pending.relative_to(candidate).parts
                state = (
                    identity(candidate), definition.object_identity, suffix,
                )
                if state in expanded_states:
                    raise UnsafePathError(
                        f"发现链接解析循环：{candidate}",
                        kind="link_cycle",
                    )
                if len(followed) >= MAX_LINK_EXPANSIONS:
                    raise UnsafePathError(
                        "链接展开超过本项目有限探查上限"
                        f"{MAX_LINK_EXPANSIONS}：{nominal}",
                        kind="link_expansion_limit",
                    )
                expanded_states.add(state)
                followed.append(definition)
                pending = definition.direct_target.joinpath(*suffix)
                break

            if kind == "directory":
                _record_directory(directories, candidate, attributes)
            elif not last:
                raise UnsafePathError(
                    f"父路径不是目录：{candidate}",
                    kind="parent_not_directory",
                )
            if last:
                return _finish(
                    nominal, candidate, kind,
                    _object_identity(attributes, candidate),
                    followed, None, directories, None, follow_final_link,
                )
        else:
            raise RuntimeError("链接路径探查没有形成结果或下一解析位置")


def recheck_link_path(expected):
    """新边界重探查并比较固定事实，不接受新指向替换原计划。

    内容版本由正式文件观察负责；本复核只负责路径、链接定义、
    目录身份、末级对象身份及最先缺失位置。
    """
    if not isinstance(expected, LinkPathObservation):
        raise TypeError("链接路径复核必须消费LinkPathObservation")
    current = inspect_link_path(
        expected.nominal_path,
        follow_final_link=expected.follow_final_link,
    )
    if current != expected:
        raise ValueError(
            "名义入口、链接定义、访问目录或实际对象偏离固定预演；"
            "旧计划失效，必须重新预演。\n"
            f"名义路径：{expected.nominal_path}\n"
            f"原实际位置：{expected.actual_path}\n"
            f"当前实际位置：{current.actual_path}"
        )
    return current


def recheck_link_dependencies(expected):
    """复核原访问依赖，不把实际末级对象身份当成链接定义。

    文件发布可以正常替换实际末级文件，必要目录也可能由本任务创建。
    因此本能力只比较原链接定义、原目录身份及解析位置。
    末级文件的存在性、身份、链接数和内容由FileObservation约束。

    不接受新链接、原链接消失、指向改变或原目录被替换。
    新出现的普通必要目录不在这里取得创建所有权；
    所属目录状态仍须消费正式创建结果检查它们。
    """
    if not isinstance(expected, LinkPathObservation):
        raise TypeError("访问依赖复核必须消费LinkPathObservation")
    if not expected.follow_final_link or expected.entry_link is not None:
        raise ValueError("文件内容访问依赖必须使用跟随末级链接的观察")
    current = inspect_link_path(
        expected.nominal_path, follow_final_link=True,
    )
    original_directories = {
        identity(member.path): member for member in expected.directories
    }
    current_directories = {
        identity(member.path): member for member in current.directories
    }
    if (
        current.actual_path != expected.actual_path
        or current.followed_links != expected.followed_links
        or current.entry_link is not None
        or any(
            current_directories.get(key) != member
            for key, member in original_directories.items()
        )
    ):
        raise ValueError(
            "链接定义、原访问目录身份或实际访问位置偏离固定预演；"
            "必须停止并重新预演。\n"
            f"名义路径：{expected.nominal_path}\n"
            f"原实际位置：{expected.actual_path}\n"
            f"当前实际位置：{current.actual_path}"
        )
    return current


def link_definition_digest(definition):
    """入口结构摘要只覆盖可重建定义，不混入目标正文或现场身份。

    直接目标位置由入口位置和原定义推导；最终解析目标不属于定义。
    对象身份及revision仍由路径观察单独核验，不以摘要代替身份。
    """
    if not isinstance(definition, LinkDefinition):
        raise TypeError("链接结构摘要必须消费LinkDefinition")
    if (
        definition.kind not in _LINK_KINDS
        or type(definition.raw_target) is not str
        or not definition.raw_target
        or type(definition.relative) is not bool
    ):
        raise ValueError("链接结构定义非法")
    expected_tag = (
        _MOUNT_POINT_TAG if definition.kind == "junction" else _SYMLINK_TAG
    )
    if definition.reparse_tag != expected_tag:
        raise ValueError("链接类型与重解析标签矛盾")
    relative, target = _direct_target(
        definition.entry_path, definition.kind, definition.raw_target,
    )
    if (
        relative != definition.relative
        or target != definition.direct_target
    ):
        raise ValueError("链接原定义与直接目标位置矛盾")
    return link_specification_digest(LinkSpecification(
        definition.kind, definition.raw_target, definition.relative,
    ))


@dataclass(frozen=True)
class LinkSpecification:
    """可持久化的入口定义，不包含现场身份、目标内容或执行授权。"""

    kind: str
    raw_target: str
    relative: bool


def validate_link_specification(specification, entry_path):
    """在明确入口位置解释原定义；不查询目标存在性或展开链接链。"""
    if not isinstance(specification, LinkSpecification):
        raise TypeError("链接重建定义必须是LinkSpecification")
    if (
        type(specification.kind) is not str
        or specification.kind not in _LINK_KINDS
        or type(specification.raw_target) is not str
        or not specification.raw_target
        or type(specification.relative) is not bool
    ):
        raise ValueError("链接重建定义字段非法")
    entry = _nominal_path(entry_path)
    if entry == entry.parent:
        raise ValueError("链接重建不得作用于文件系统根")
    relative, direct_target = _direct_target(
        entry, specification.kind, specification.raw_target,
    )
    if relative != specification.relative:
        raise ValueError("链接相对标志与原始目标表达矛盾")
    return direct_target


def specification_from_link(definition):
    """从正式入口事实提取原定义，不复制访问依赖或目标内容。"""
    if not isinstance(definition, LinkDefinition):
        raise TypeError("定义提取必须消费LinkDefinition")
    # 同时核对原定义、类型标签和直接位置的一致性。
    link_definition_digest(definition)
    return LinkSpecification(
        definition.kind, definition.raw_target, definition.relative,
    )


def encode_link_specification(specification, *, entry_path):
    """产生普通JSON材料bytes；不创建任何文件或活动链接。"""
    import json

    validate_link_specification(specification, entry_path)
    value = {
        "format": "ZIPATCH_LINK_DEFINITION",
        "version": 1,
        "kind": specification.kind,
        "raw_target": specification.raw_target,
        "relative": specification.relative,
    }
    return (
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def decode_link_specification(raw, *, entry_path):
    """严格读取普通定义材料，不猜旧格式、不补字段、不授予创建权限。"""
    import json

    if not isinstance(raw, bytes):
        raise TypeError("链接定义材料必须是不可变bytes")

    def unique_fields(pairs):
        value = {}
        for key, member in pairs:
            if key in value:
                raise ValueError(f"链接定义材料包含重复字段：{key}")
            value[key] = member
        return value

    def reject_constant(value):
        raise ValueError(f"链接定义材料包含非JSON数值常量：{value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_fields,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"链接定义材料无法严格解析：{error}") from error
    if (
        type(value) is not dict
        or set(value) != {
            "format", "version", "kind", "raw_target", "relative",
        }
        or value["format"] != "ZIPATCH_LINK_DEFINITION"
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("链接定义材料格式、版本或字段集合非法")
    specification = LinkSpecification(
        value["kind"], value["raw_target"], value["relative"],
    )
    validate_link_specification(specification, entry_path)
    return specification


def link_specification_digest(specification):
    """唯一结构摘要算法；字段摘要不替代位置解释或创建授权。

    不需要现场入口身份，不查询磁盘。
    路径表达是否可在具体入口重建，由validate_link_specification检查。
    """
    import hashlib
    import json

    if not isinstance(specification, LinkSpecification):
        raise TypeError("链接结构摘要必须消费LinkSpecification")
    if (
        type(specification.kind) is not str
        or specification.kind not in _LINK_KINDS
        or type(specification.raw_target) is not str
        or not specification.raw_target
        or any(ord(character) < 32 for character in specification.raw_target)
        or type(specification.relative) is not bool
        or specification.kind == "junction" and specification.relative
    ):
        raise ValueError("链接结构摘要的定义字段非法")
    encoded = json.dumps(
        (specification.kind, specification.raw_target, specification.relative),
        ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recheck_path_dependencies(expected):
    """正文或结构的名义访问依赖，不约束已由工作核心消费的末级对象。

    正文保留末级文件链接为访问依赖；结构不跟随末级入口。
    实际对象及其后代由固定工作观察负责，不在这里认证或授权。
    必要新普通目录可以出现，其所有权仍由目录创建结果负责。
    """
    if not isinstance(expected, LinkPathObservation):
        raise TypeError("路径依赖复核必须消费LinkPathObservation")
    current = inspect_link_path(
        expected.nominal_path,
        follow_final_link=expected.follow_final_link,
    )
    required_directories = tuple(
        member for member in expected.directories
        if not contains(expected.actual_path, member.path)
    )
    current_directories = {
        identity(member.path): member for member in current.directories
    }
    if (
        current.actual_path != expected.actual_path
        or current.followed_links != expected.followed_links
        or any(
            current_directories.get(identity(member.path)) != member
            for member in required_directories
        )
    ):
        raise ValueError(
            "固定名义访问的链接定义、实际位置或原父目录身份变化；"
            "停止执行或回滚，不访问新指向。\n"
            f"名义位置：{expected.nominal_path}\n"
            f"原实际位置：{expected.actual_path}\n"
            f"当前实际位置：{current.actual_path}"
        )
    return current
