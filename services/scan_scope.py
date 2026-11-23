# -*- coding: utf-8 -*-

"""扫描来源的剪枝元信息、链接叶子和固定访问边界。

不读取文件正文、不改变来源、不发布输出。
扫描根经链接访问时，枚举前取得明确实际目录授权。
叶子定义只记录入口，不跟随、展开或读取其引用目标。
"""

import os
from dataclasses import dataclass
from pathlib import Path

from core.exclusion_rules import PreparedExclusions
from core.file_observation import observe_file, ObjectIdentity
from core.link_paths import (
    inspect_link_path, recheck_link_path, recheck_path_dependencies,
)
from core.recovery_paths import identity, contains, checked_chain


@dataclass(frozen=True)
class ScanSourceScope:
    root_access: object
    directories: tuple
    files: tuple
    links: tuple

    @property
    def file_count(self):
        return len(self.files) + sum(
            access.kind == "file_symlink" for access in self.links
        )

    @property
    def dir_count(self):
        return len(self.directories) - 1 + sum(
            access.kind in ("directory_symlink", "junction")
            for access in self.links
        )


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
        raise ValueError(
            "扫描链接实际范围尚未取得本次授权，停止。\n" + description
        )
    if not callable(authorize):
        raise TypeError("扫描范围授权必须是可调用对象")
    decision = authorize(title, description)
    if type(decision) is not bool:
        raise TypeError("扫描范围授权必须返回明确布尔值")
    if not decision:
        raise ValueError("本次扫描范围授权已取消，未发布成果")


def prepare_scan_scope(
    source_folder, exclusions, output, *,
    authorize=None, select_scope=None, select_enumeration=None,
):
    if not isinstance(exclusions, PreparedExclusions):
        raise TypeError("扫描必须消费正式排除名单")
    if authorize is not None and not callable(authorize):
        raise TypeError("扫描范围授权必须是可调用对象")
    if select_scope is not None and not callable(select_scope):
        raise TypeError("扫描固定来源选择必须可调用")
    if not source_folder:
        raise ValueError("扫描源文件夹不能为空")
    root_access = inspect_link_path(source_folder, follow_final_link=True)
    if root_access.kind != "directory":
        raise ValueError("扫描来源必须定位到既有实际普通目录")
    root = root_access.actual_path
    if select_enumeration is not None and not callable(select_enumeration):
        raise TypeError("扫描枚举范围选择必须可调用")
    if root_access.has_links and select_enumeration is not None:
        from services.source_authorization import EnumerationAuthorization

        authorization = EnumerationAuthorization(None)
        skipped = authorization.select((root_access,), select_enumeration)
        if skipped:
            # 未进入目标目录，不保留其执行期依赖；调用方可以发布空扫描。
            return None
    elif root_access.has_links:
        require_scan_authorization(
            authorize,
            "扫描实际源目录访问授权",
            describe_scan_access(root_access)
            + "\n\n仅允许枚举这个明确的实际普通目录及未排除的普通子目录。"
            "\n目录链接后代仍作为叶子，不递归进入其目标。"
            "\n本次不读取任何普通文件正文。",
        )
    recheck_link_path(root_access)

    directories = []
    files = []
    links = []
    pending = [(root, ())]
    output_key = identity(output.path)

    while pending:
        folder, relative_parts = pending.pop()
        access = inspect_link_path(folder, follow_final_link=False)
        if (
            access.kind != "directory"
            or access.has_links
            or access.actual_path != folder
        ):
            raise ValueError("扫描枚举的实际目录或普通父链变化")
        directories.append(access)
        import stat
        from core.file_observation import _assert_file_attributes
        from core.recovery_paths import validate_component

        with os.scandir(folder) as entries:
            names = tuple(entry.name for entry in entries)
        for name in names:
            child = folder / name
            if identity(child) == output_key:
                continue
            validate_component(name)
            attributes = child.lstat()
            parts = relative_parts + (name,)
            directory = (
                stat.S_ISDIR(attributes.st_mode)
                or bool(getattr(attributes, "st_file_attributes", 0) & 0x10)
            )
            if (
                directory and exclusions.directories.matches(parts)
                or not directory and exclusions.files.matches(parts)
            ):
                continue
            reparse = (
                stat.S_ISLNK(attributes.st_mode)
                or bool(getattr(attributes, "st_file_attributes", 0) & 0x400)
            )
            if reparse or directory:
                observed = inspect_link_path(child, follow_final_link=False)
                if (
                    observed.followed_links or observed.actual_path != child
                    or observed.object_identity
                    != ObjectIdentity(attributes.st_dev, attributes.st_ino)
                ):
                    raise ValueError("扫描成员实际位置、身份或普通父链变化")
                if observed.entry_link is not None:
                    links.append(observed)
                elif observed.kind == "directory":
                    pending.append((child, parts))
                else:
                    raise ValueError(f"扫描成员类型变化：{child}")
            else:
                # 普通文件不再先走一次完整链接探查、再重复文件观察。
                # 唯一文件观察仍检查普通父链和可靠身份。
                file = observe_file(child)
                if file.kind != "file":
                    raise ValueError("扫描文件元信息取得期间存在性变化")
                _assert_file_attributes(file.attributes, attributes, child)
                files.append(file)
        recheck_link_path(access)

    order = lambda path: (str(path).casefold(), str(path))
    scope = ScanSourceScope(
        root_access,
        tuple(sorted(directories, key=lambda access: order(access.actual_path))),
        tuple(sorted(files, key=lambda file: order(file.path))),
        tuple(sorted(links, key=lambda access: order(access.actual_path))),
    )
    recheck_scan_scope(scope)
    validate_scan_output(scope, output)
    if select_scope is not None:
        from core.work_selection import selected_indices

        # 元信息和来源输出隔离全部先检查；选择不绕过非法来源。
        # 普通目录是已完成枚举的结构上下文，不任意裁剪必要父链。
        members = tuple(sorted(
            (*scope.files, *scope.links),
            key=lambda member: order(
                member.actual_path if hasattr(member, "actual_path") else member.path
            ),
        ))
        retained = selected_indices(
            select_scope(scope, members, output), len(members),
            description="扫描固定来源选择",
        )
        kept = tuple(members[number] for number in retained)
        files = tuple(member for member in kept if not hasattr(member, "entry_link"))
        links = tuple(member for member in kept if hasattr(member, "entry_link"))
        scope = ScanSourceScope(
            scope.root_access, scope.directories, files, links,
        )
        # 选择等待是新的时间边界，只复核保留范围。
        recheck_scan_scope(scope)
        validate_scan_output(scope, output)
    elif scope.links:
        description = "\n\n".join(
            describe_scan_access(access) for access in scope.links
        )
        require_scan_authorization(
            authorize,
            "扫描固定链接入口范围授权",
            "以下为已探查的全部未排除链接入口。\n"
            "仅输出入口元信息和实际原定义，不读取或展开引用目标。\n\n"
            + description
            + f"\n\n固定文件入口数量：{scope.file_count}"
            + f"\n固定目录入口数量：{scope.dir_count}"
            + f"\n成果位置：{output.path}",
        )
        recheck_scan_scope(scope)
    return scope


def validate_scan_output(scope, output):
    """名称隔离及现存输出对象隔离，不扫描其他硬链接名称。"""
    target = output.path
    if any(
        contains(target, access.actual_path)
        for access in scope.directories
    ):
        raise ValueError("扫描输出范围包含实际来源目录或其访问目录")
    if any(identity(target) == identity(access.actual_path) for access in scope.links):
        raise ValueError("扫描输出不能替换来源链接入口")
    if output.kind == "file" and any(
        file.object_identity == output.object_identity for file in scope.files
    ):
        raise ValueError("扫描输出与来源文件共享对象，禁止覆盖来源正文")
    for access in (scope.root_access, *scope.links):
        for definition in access.followed_links:
            if contains(target, definition.entry_path):
                raise ValueError("扫描输出会改变来源访问链接依赖")
        for directory in access.directories:
            if contains(target, directory.path):
                raise ValueError("扫描输出会改变来源访问目录身份")


def recheck_scan_scope(scope):
    """新边界只比较原范围；不读取正文、不枚举或认证新成员。

    目录只约束身份，不要求修改时间不变：
    输出暂存或新输出目录可以正常改变来源目录的成员时间。
    未列出成员不在此取得新的扫描或改变资格。
    """
    if not isinstance(scope, ScanSourceScope):
        raise TypeError("来源复核必须消费固定ScanSourceScope")
    from services.source_recheck import recheck_source_paths

    recheck_source_paths((
        scope.root_access, *scope.directories, *scope.links,
    ))
    for file in scope.files:
        actual = observe_file(file.path)
        if (
            actual.kind != "file"
            or actual.object_identity != file.object_identity
            or actual.link_count != file.link_count
            or actual.attributes.st_size != file.attributes.st_size
            or actual.attributes.st_mtime_ns != file.attributes.st_mtime_ns
            or actual.attributes.st_ctime_ns != file.attributes.st_ctime_ns
        ):
            raise ValueError(f"固定扫描文件元信息变化：{file.path}")


def scan_display_path(scope, actual):
    """成果保留用户名义根下路径，实际位置另行表达。"""
    return scope.root_access.nominal_path / Path(actual).relative_to(
        scope.root_access.actual_path,
    )
