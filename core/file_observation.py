# -*- coding: utf-8 -*-

"""固定文件及完整目录树观察；不解码、授权、保护或改变工作目标。

文件内容按请求一次读取，目录按完整树用途一次枚举。
结束属性核验不重新读取正文或枚举目录。
检查不是文件锁，不承诺跨对象原子快照或检测全部竞态。
"""

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from core.recovery_content import content_integrity
from core.recovery_paths import (
    checked_chain, entity_type_from_attributes, validate_component,
)


_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ObjectIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class FileObservation:
    path: Path
    chain: tuple
    kind: str
    attributes: os.stat_result | None
    object_identity: ObjectIdentity | None
    link_count: int | None
    source_bytes: bytes | None
    digest: str | None
    mtime_ns: int | None
    # path和chain始终属于实际普通对象；access仅保留原预演访问依赖。
    # 发布后的文件身份由本观察拥有，不改写原链接路径快照。
    access: object | None = None


@dataclass(frozen=True)
class DirectoryObservation:
    path: Path
    object_identity: ObjectIdentity
    attributes: os.stat_result


@dataclass(frozen=True)
class TreeObservation:
    path: Path
    chain: tuple
    directories: tuple
    files: tuple
    content_records: tuple
    integrity: object
    file_times: tuple
    # 每项为相对入口位置及不跟随末级链接的正式路径观察。
    # 链接后代不属于该结构树；读取目标须另行授权和观察。
    links: tuple = ()


def _boolean(value, description):
    if type(value) is not bool:
        raise TypeError(f"{description}必须是布尔值")


def _object_identity(attributes, path):
    if (
        type(attributes.st_dev) is not int
        or attributes.st_dev < 0
        or type(attributes.st_ino) is not int
        or attributes.st_ino <= 0
    ):
        raise OSError(f"无法取得可靠的对象身份：{path}")
    return ObjectIdentity(attributes.st_dev, attributes.st_ino)


def _file_links(attributes, path):
    if type(attributes.st_nlink) is not int or attributes.st_nlink < 1:
        raise OSError(f"无法取得可靠的文件链接数量：{path}")
    return attributes.st_nlink


def _revision(attributes):
    """只比较同类查询来源的稳定性；访问时间不参与正文漂移。"""
    return (
        attributes.st_size,
        attributes.st_mtime_ns,
        attributes.st_ctime_ns,
    )


def _assert_file_object(actual, expected, path):
    """路径和句柄共用对象约束，不假定两种查询的时间表示完全相同。"""
    if entity_type_from_attributes(path, actual) != "file":
        raise ValueError(f"观察期间对象不再是普通文件：{path}")
    if _object_identity(actual, path) != _object_identity(expected, path):
        raise ValueError(f"观察期间文件对象身份变化：{path}")
    if _file_links(actual, path) != _file_links(expected, path):
        raise ValueError(f"观察期间文件链接数量变化：{path}")
    if actual.st_size != expected.st_size:
        raise ValueError(f"观察期间文件长度变化：{path}")


def _assert_file_attributes(actual, expected, path):
    """同为路径查询或同为句柄查询时，核对完整稳定性属性。"""
    _assert_file_object(actual, expected, path)
    actual_revision = _revision(actual)
    expected_revision = _revision(expected)
    if actual_revision != expected_revision:
        raise ValueError(
            f"观察期间文件属性或内容前提变化：{path}；"
            f"expected(size, mtime_ns, ctime_ns)={expected_revision!r}；"
            f"actual={actual_revision!r}"
        )


def _read_file(stream, include_bytes, include_digest):
    digest = hashlib.sha256() if include_digest else None
    fragments = [] if include_bytes else None
    length = 0
    while True:
        chunk = stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        length += len(chunk)
        if fragments is not None:
            fragments.append(chunk)
        if digest is not None:
            digest.update(chunk)
    return (
        b"".join(fragments) if fragments is not None else None,
        digest.hexdigest() if digest is not None else None,
        length,
    )


def _checked_file_attributes(path):
    target, chain = checked_chain(path, include_observation=True)
    kind = chain[-1][1]
    if kind == "absent":
        return target, chain, None
    if kind != "file":
        raise IsADirectoryError(f"文件观察目标不是普通文件：{target}")
    attributes = target.lstat()
    if entity_type_from_attributes(target, attributes) != "file":
        raise ValueError(f"文件观察目标类型变化：{target}")
    return target, chain, attributes


def observe_file(
    path, *, include_bytes=False, include_digest=False, include_mtime=False,
):
    """取得最小充分文件事实；未请求内容时不打开文件。

    bytes和digest请求共用一个读取流。
    不存在的source_bytes为None，普通空文件为b""。
    安全拒绝、系统故障和程序缺陷不转换为正文占位。
    """
    _boolean(include_bytes, "原字节获取开关")
    _boolean(include_digest, "指纹获取开关")
    _boolean(include_mtime, "正式时间获取开关")
    target, chain, attributes = _checked_file_attributes(path)
    if attributes is None:
        return FileObservation(
            target, chain, "absent", None, None, None, None, None, None,
        )

    object_identity = _object_identity(attributes, target)
    links = _file_links(attributes, target)
    source_bytes = digest = None
    if include_bytes or include_digest:
        # rb不会创建、截断目标，也不要求来源具有写权限。
        # 路径与句柄的时间表示可能不同；对象关联和期间稳定性分开检查。
        with target.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            _assert_file_object(opened, attributes, target)
            source_bytes, digest, length = _read_file(
                stream, include_bytes, include_digest,
            )
            finished = os.fstat(stream.fileno())
            _assert_file_attributes(finished, opened, target)
            if length != finished.st_size:
                raise OSError(f"文件读取长度与句柄属性不符：{target}")

        # 关闭是新的时间边界：路径仍对应读取句柄，
        # 且路径查询自己的长度和时间前提没有在此次读取期间变化。
        confirmed, confirmed_chain, closed = _checked_file_attributes(target)
        if closed is None:
            raise ValueError(f"观察期间文件消失：{target}")
        _assert_file_object(closed, finished, target)
        _assert_file_attributes(closed, attributes, target)
        target, chain, attributes = confirmed, confirmed_chain, closed

    return FileObservation(
        target, chain, "file", attributes, object_identity, links,
        source_bytes, digest,
        attributes.st_mtime_ns if include_mtime else None,
    )


def recheck_file(expected, *, check_mtime=False):
    """新边界核对固定事实；返回新观察，不自动更新原前提或执行资格。"""
    if not isinstance(expected, FileObservation):
        raise TypeError("文件复核必须消费FileObservation")
    _boolean(check_mtime, "正式时间复核开关")
    if check_mtime and expected.kind == "file" and expected.mtime_ns is None:
        raise ValueError("原观察未请求正式时间，不能补猜时间前提")

    recheck_file_access(expected)
    current = observe_file(
        expected.path,
        include_bytes=expected.source_bytes is not None,
        include_digest=expected.digest is not None,
        include_mtime=check_mtime,
    )
    current = attach_file_access(current, expected.access)
    if current.kind != expected.kind:
        raise ValueError(f"固定文件存在性或类型变化：{expected.path}")
    if current.kind == "absent":
        return current
    if current.object_identity != expected.object_identity:
        raise ValueError(f"固定文件对象身份变化：{expected.path}")
    if current.link_count != expected.link_count:
        raise ValueError(f"固定文件链接数量变化：{expected.path}")
    if (
        expected.source_bytes is not None
        and current.source_bytes != expected.source_bytes
    ):
        raise ValueError(f"固定文件原字节变化：{expected.path}")
    if expected.digest is not None and current.digest != expected.digest:
        raise ValueError(f"固定文件内容指纹变化：{expected.path}")
    if check_mtime and current.mtime_ns != expected.mtime_ns:
        raise ValueError(f"固定文件正式时间变化：{expected.path}")
    return current


def _directory(path, attributes):
    if entity_type_from_attributes(path, attributes) != "directory":
        raise ValueError(f"目录观察目标不再是普通目录：{path}")
    return DirectoryObservation(
        path, _object_identity(attributes, path), attributes,
    )


def _check_directory(expected):
    target, chain = checked_chain(expected.path, include_observation=True)
    if chain[-1][1] != "directory":
        raise ValueError(f"观察期间目录存在性或类型变化：{target}")
    current = _directory(target, target.lstat())
    if current.object_identity != expected.object_identity:
        raise ValueError(f"观察期间目录对象身份变化：{target}")
    if (
        current.attributes.st_mtime_ns != expected.attributes.st_mtime_ns
        or current.attributes.st_ctime_ns != expected.attributes.st_ctime_ns
    ):
        raise ValueError(f"观察期间目录结构属性变化：{target}")
    return current


def _check_observed_file_attributes(expected):
    """树结束边界只复核属性和原链，不第二次读取早先文件正文。"""
    _, _, current = _checked_file_attributes(expected.path)
    if current is None:
        raise ValueError(f"树观察期间文件消失：{expected.path}")
    _assert_file_attributes(current, expected.attributes, expected.path)


def observe_tree(
    path, *, include_bytes=False, include_mtime=False, include_links=False,
):
    """一次完整结构树枚举；显式决定是否记录链接入口。

    默认严格用途继续拒绝链接。include_links=True仅记录入口，
    不读取链接目标正文，也不展开目录链接目标。
    树根仍要求实际普通目录；经链接访问树根由所属协调先行定位。
    Windows的DirEntry属性可能缺少可靠编号，因此枚举消费名称，
    实际对象使用不跟随的lstat；不把零编号当身份。
    """
    import stat
    from core.link_paths import (
        inspect_link_path, link_definition_digest, recheck_link_path,
    )

    _boolean(include_bytes, "树原字节获取开关")
    _boolean(include_mtime, "树正式时间获取开关")
    _boolean(include_links, "树链接入口记录开关")
    root, chain = checked_chain(path, include_observation=True)
    if chain[-1][1] != "directory":
        raise ValueError(f"目录树观察要求既有普通目录：{root}")
    root_observation = _directory(root, root.lstat())
    directories = [("", root_observation)]
    files = []
    links = []
    pending = [root_observation]

    while pending:
        folder = _check_directory(pending.pop())
        with os.scandir(folder.path) as entries:
            for entry in entries:
                validate_component(entry.name)
                child = folder.path / entry.name
                attributes = child.lstat()
                relative = child.relative_to(root).as_posix()
                reparse = (
                    stat.S_ISLNK(attributes.st_mode)
                    or bool(getattr(attributes, "st_file_attributes", 0) & 0x400)
                )
                if include_links and reparse:
                    observed_link = inspect_link_path(
                        child, follow_final_link=False,
                    )
                    if (
                        observed_link.entry_link is None
                        or observed_link.actual_path != child
                        or observed_link.followed_links
                        or observed_link.object_identity
                        != _object_identity(attributes, child)
                    ):
                        raise ValueError(
                            f"树观察期间链接入口或父目录前提变化：{child}"
                        )
                    definition = observed_link.entry_link
                    if (
                        definition.revision[0] != attributes.st_size
                        or definition.revision[1] != attributes.st_mtime_ns
                        or definition.revision[2] != attributes.st_ctime_ns
                    ):
                        raise ValueError(
                            f"树观察期间链接定义前提变化：{child}"
                        )
                    links.append((relative, observed_link))
                    continue
                kind = entity_type_from_attributes(child, attributes)
                if kind == "directory":
                    observed_directory = _directory(child, attributes)
                    directories.append((relative, observed_directory))
                    pending.append(observed_directory)
                else:
                    # 枚举观察与句柄读取是不同时间边界；
                    # observe_file核验原链、句柄及关闭后的同一对象。
                    observed_file = observe_file(
                        child, include_bytes=include_bytes,
                        include_digest=True, include_mtime=include_mtime,
                    )
                    if observed_file.kind != "file":
                        raise ValueError(f"树观察期间文件消失：{child}")
                    _assert_file_attributes(
                        observed_file.attributes, attributes, child,
                    )
                    files.append((relative, observed_file))
        _check_directory(folder)

    # 早对象可能在后对象读取期间变化。
    # 全部结束核验仅查询属性，不再枚举树或重新读取正文。
    for _, observed_file in files:
        _check_observed_file_attributes(observed_file)
    for _, directory in directories:
        _check_directory(directory)
    for _, observed_link in links:
        recheck_link_path(observed_link)

    position_key = lambda member: member[0].encode("utf-8")
    directories.sort(key=position_key)
    files.sort(key=position_key)
    links.sort(key=position_key)
    records = tuple(sorted(
        [
            *((relative, "directory", "") for relative, _ in directories),
            *((relative, "file", observed.digest) for relative, observed in files),
            *((relative, observed.kind, link_definition_digest(observed.entry_link))
              for relative, observed in links),
        ],
        key=position_key,
    ))
    integrity = MappingProxyType(content_integrity("directory", records))
    times = (
        tuple((relative, observed.mtime_ns) for relative, observed in files)
        if include_mtime else ()
    )
    return TreeObservation(
        root, chain, tuple(directories), tuple(files),
        records, integrity, times, tuple(links),
    )


def recheck_tree(expected, *, check_mtime=False):
    """新边界观察完整必要树，核对结构、对象身份、链接及内容。"""
    if not isinstance(expected, TreeObservation):
        raise TypeError("目录树复核必须消费TreeObservation")
    _boolean(check_mtime, "树正式时间复核开关")
    if check_mtime and any(
        observed.mtime_ns is None for _, observed in expected.files
    ):
        raise ValueError("原树观察未请求正式时间，不能补猜时间前提")

    current = observe_tree(
        expected.path, include_mtime=check_mtime,
        include_links=bool(expected.links),
    )
    if current.links != expected.links:
        raise ValueError(f"固定目录树链接定义、位置或身份变化：{expected.path}")
    expected_directories = tuple(
        (relative, observed.object_identity)
        for relative, observed in expected.directories
    )
    current_directories = tuple(
        (relative, observed.object_identity)
        for relative, observed in current.directories
    )
    if current_directories != expected_directories:
        raise ValueError(f"固定目录结构或对象身份变化：{expected.path}")
    if current.content_records != expected.content_records:
        raise ValueError(f"固定目录树内容或结构变化：{expected.path}")
    expected_files = tuple(
        (relative, observed.object_identity, observed.link_count)
        for relative, observed in expected.files
    )
    current_files = tuple(
        (relative, observed.object_identity, observed.link_count)
        for relative, observed in current.files
    )
    if current_files != expected_files:
        raise ValueError(f"固定树文件身份或链接数量变化：{expected.path}")
    if check_mtime and current.file_times != expected.file_times:
        raise ValueError(f"固定目录树正式文件时间变化：{expected.path}")
    return current


def project_removed_file_links(observed, removed_link_counts=None):
    """消费所属协调的正常名称移除数量，投影固定观察的剩余链接。

    不查询磁盘、不改写最初观察、正文、属性或正式时间。
    数量必须来自所属任务已确认的实际步骤；
    本纯投影不认证调用方提交的数量，也不授予改变权限。
    新边界仍须通过正式复核验证该派生前提。
    """
    from collections.abc import Mapping

    if not isinstance(observed, (FileObservation, TreeObservation)):
        raise TypeError("剩余链接投影必须消费正式文件或完整树观察")
    if removed_link_counts is None:
        return observed
    if not isinstance(removed_link_counts, Mapping):
        raise TypeError("正常名称移除数量必须是对象身份映射")
    counts = dict(removed_link_counts)
    for identifier, number in counts.items():
        if (
            not isinstance(identifier, ObjectIdentity)
            or type(identifier.device) is not int
            or identifier.device < 0
            or type(identifier.inode) is not int
            or identifier.inode <= 0
            or type(number) is not int
            or number < 0
        ):
            raise ValueError("正常名称移除的对象身份或数量非法")

    def project_file(member):
        if member.kind == "absent":
            return member
        if (
            member.kind != "file"
            or not isinstance(member.object_identity, ObjectIdentity)
            or type(member.link_count) is not int
            or member.link_count < 1
        ):
            raise ValueError("剩余链接投影缺少可靠普通文件前提")
        removed = counts.get(member.object_identity, 0)
        if not removed:
            return member
        links = member.link_count - removed
        if links < 1:
            raise ValueError("正常名称移除形成非法剩余链接数量")
        from dataclasses import replace
        return replace(member, link_count=links)

    if isinstance(observed, FileObservation):
        return project_file(observed)
    files = tuple(
        (relative, project_file(member))
        for relative, member in observed.files
    )
    if all(
        projected is original
        for (_, projected), (_, original) in zip(files, observed.files)
    ):
        return observed
    from dataclasses import replace
    return replace(observed, files=files)


def recheck_file_access(observed):
    """只核对文件的原访问依赖；不读正文，不更新原授权或内容基线。"""
    if not isinstance(observed, FileObservation):
        raise TypeError("文件访问复核必须消费FileObservation")
    if observed.access is None:
        return None
    from core.link_paths import LinkPathObservation, recheck_link_dependencies

    access = observed.access
    if (
        not isinstance(access, LinkPathObservation)
        or not access.follow_final_link
        or access.entry_link is not None
        or observed.path != access.actual_path
    ):
        raise ValueError("文件访问事实没有对应实际普通对象位置")
    return recheck_link_dependencies(access)


def attach_file_access(observed, access):
    """关联原访问依赖与新的正式文件事实，不改变任何对象执行资格。

    新文件身份必须由正式观察或发布核心取得。
    原路径快照只负责访问关系，不使用其中旧末级身份覆盖新文件事实。
    """
    if not isinstance(observed, FileObservation):
        raise TypeError("访问关联必须消费FileObservation")
    if observed.access is not None and observed.access is not access:
        raise ValueError("不得替换已关联文件的原访问依赖")
    if access is None:
        return observed
    linked = replace(observed, access=access)
    recheck_file_access(linked)
    return linked


def observe_accessed_file(
    access, *, include_bytes=False, include_digest=False, include_mtime=False,
):
    """所属预演完成适用访问授权后，消费固定路径事实取得普通文件事实。

    授权和局部跳过归业务协调，本能力不弹窗、不自行授予权限。
    首次读取前仍使用完整路径快照检查末级身份；
    正文只从实际普通路径读取，继续消费唯一observe_file算法。
    返回观察的path是实际位置，access保留名义位置及原链接依赖。
    """
    from core.link_paths import LinkPathObservation, recheck_link_path

    if not isinstance(access, LinkPathObservation):
        raise TypeError("文件访问必须消费正式LinkPathObservation")
    for value, description in (
        (include_bytes, "原字节获取开关"),
        (include_digest, "指纹获取开关"),
        (include_mtime, "正式时间获取开关"),
    ):
        _boolean(value, description)
    if (
        not access.follow_final_link
        or access.entry_link is not None
        or access.kind not in ("absent", "file")
    ):
        raise ValueError("文件内容访问要求已解析的普通文件或缺失位置")
    recheck_link_path(access)
    observed = observe_file(
        access.actual_path,
        include_bytes=include_bytes,
        include_digest=include_digest,
        include_mtime=include_mtime,
    )
    if (
        observed.kind != access.kind
        or observed.object_identity != access.object_identity
    ):
        raise ValueError("首次文件获取期间实际对象偏离固定路径观察")
    return attach_file_access(observed, access)
