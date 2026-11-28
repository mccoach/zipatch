# -*- coding: utf-8 -*-

"""扫描的已授权实体范围；名义位置与实际访问路线分别保留。"""

from dataclasses import dataclass
from pathlib import Path

from core.exclusion_rules import PreparedExclusions
from core.file_observation import observe_accessed_file, recheck_file
from core.link_paths import recheck_link_path
from core.recovery_paths import contains, identity
from services.source_traversal import SourceTraversal


@dataclass(frozen=True)
class ScanSourceScope:
    root_access: object
    directories: tuple
    files: tuple
    links: tuple
    file_accesses: tuple = ()
    ignored: tuple = ()
    directory_metadata: tuple = ()
    hardlink_count: int = 0

    @property
    def file_count(self):
        return len(self.files)

    @property
    def dir_count(self):
        return max(0, len(self.directories) - 1)


def describe_scan_access(access):
    lines = [
        f"名义位置：{access.nominal_path}",
        f"实际位置：{access.actual_path}",
        f"类型：{access.kind}",
    ]
    for definition in (
        *access.followed_links,
        *((access.entry_link,) if access.entry_link is not None else ()),
    ):
        lines.extend((
            f"链接入口：{definition.entry_path}",
            f"链接类型：{definition.kind}",
            f"实际原定义：{definition.raw_target!r}",
            f"相对定义：{definition.relative}",
            f"直接引用位置：{definition.direct_target}",
        ))
    return "\n".join(lines)


def require_scan_authorization(authorize, title, description):
    if authorize is None:
        raise ValueError("扫描链接范围尚未授权。\n" + description)
    if not callable(authorize):
        raise TypeError("扫描授权必须可调用")
    decision = authorize(title, description)
    if type(decision) is not bool:
        raise TypeError("扫描授权必须返回布尔值")
    if not decision:
        raise ValueError("扫描授权已取消")


def prepare_scan_scope(
    source_folder, exclusions, output, *, select_source_links=None,
    authorize=None, include_date=False,
):
    if not isinstance(exclusions, PreparedExclusions):
        raise TypeError("扫描必须消费正式排除名单")
    if not source_folder:
        raise ValueError("扫描源文件夹不能为空")
    traversal = SourceTraversal(
        output=output, selector=select_source_links, authorize=authorize,
    )
    root = traversal.resolve(source_folder)
    if root is None:
        return ScanSourceScope(None, (), (), (), (), tuple(traversal.ignored))
    if root.kind != "directory":
        raise ValueError("扫描来源必须定位到普通目录")
    directories = [root]
    accesses = []
    for access, parts in traversal.walk(root, exclusions):
        if access.kind == "directory":
            directories.append(access)
        else:
            accesses.append(access)
    retained = traversal.finalize()
    directories = [
        access for access in directories
        if identity(access.nominal_path) in retained
    ]
    accesses = [
        access for access in accesses
        if identity(access.nominal_path) in retained
    ]
    root_retained = identity(root.nominal_path) in retained
    if not root_retained:
        return ScanSourceScope(
            None, (), (), (), (), tuple(traversal.ignored),
        )
    files = [observe_accessed_file(access) for access in accesses]
    # links保存所选路线经过的定义，只供分类统计；不代表结构叶子。
    definitions = {}
    for access in traversal.accesses:
        for definition in access.followed_links:
            definitions.setdefault(identity(definition.entry_path), definition)
    directory_metadata = []
    if include_date:
        from core.file_observation import _directory

        for access in directories:
            # 延续链接目标目录自身日期；不累计大小、不增加授权窗口。
            # 普通目录只提供结构上下文，不新增精确目录时间约束。
            if not access.has_links:
                continue
            recheck_link_path(access)
            observed = _directory(access.actual_path, access.actual_path.lstat())
            if observed.object_identity != access.object_identity:
                raise ValueError("扫描目录日期获取期间身份变化")
            recheck_link_path(access)
            directory_metadata.append((access.nominal_path, observed))
    scope = ScanSourceScope(
        root, tuple(directories), tuple(files), tuple(definitions.values()),
        tuple(accesses), tuple(traversal.ignored), tuple(directory_metadata),
        len({
            identity(observed.path)
            for observed in files if observed.link_count > 1
        }),
    )
    recheck_scan_scope(scope)
    validate_scan_output(scope, output)
    return scope


def validate_scan_output(scope, output):
    for access in (*scope.directories, *scope.file_accesses):
        if identity(access.actual_path) == identity(output.path):
            raise ValueError("扫描来源与输出相同")
        if contains(output.path, access.actual_path):
            raise ValueError("扫描输出包含来源范围")
        for definition in access.followed_links:
            if contains(output.path, definition.entry_path):
                raise ValueError("扫描输出改变链接访问依赖")
    if output.kind == "file" and any(
        observed.object_identity == output.object_identity
        for observed in scope.files
    ):
        raise ValueError("扫描输出与来源共享硬链接对象")


def recheck_scan_scope(scope):
    if not isinstance(scope, ScanSourceScope):
        raise TypeError("扫描复核必须消费正式范围")
    from services.source_recheck import recheck_source_paths

    recheck_source_paths((*scope.directories, *scope.file_accesses))
    from core.file_observation import _check_directory

    for nominal, observed in scope.directory_metadata:
        _check_directory(observed)
    for observed in scope.files:
        current = recheck_file(observed)
        if (
            current.attributes.st_size != observed.attributes.st_size
            or current.attributes.st_mtime_ns != observed.attributes.st_mtime_ns
            or current.attributes.st_ctime_ns != observed.attributes.st_ctime_ns
        ):
            raise ValueError(f"固定扫描文件元信息变化：{observed.path}")


def scan_display_path(scope, actual):
    """只用于源根自身的直接映射；文件展示消费各自名义访问事实。"""
    if scope.root_access is None:
        raise ValueError("未授权根没有可映射的后代")
    return scope.root_access.nominal_path / Path(actual).relative_to(
        scope.root_access.actual_path,
    )