# -*- coding: utf-8 -*-

"""修改包无正文范围探查及授权后固定正文获取。

结构元信息不打开普通文件，不展开目录链接叶子。
名义访问与实际工作观察分开；工作核心只消费实际普通父链。
本模块不决定权限、不建立保护、不改变文件系统。
"""

import os
from dataclasses import dataclass

from core.file_observation import (
    FileObservation, TreeObservation, observe_file,
    _check_directory, _directory, _assert_file_attributes,
)
from core.link_paths import (
    LinkPathObservation, inspect_link_path, recheck_link_path,
    recheck_path_dependencies,
)
from core.recovery_paths import checked_chain, identity, validate_component
from services.patch_ops import DUAL_PATH_OPS


@dataclass(frozen=True)
class PatchScopeTree:
    """一次元信息枚举的普通目录、普通文件及链接叶子。"""

    path: object
    chain: tuple
    directories: tuple
    files: tuple
    links: tuple

    @property
    def content_records(self):
        # 仅用于范围展示；空摘要不是完整ContentState或保护依据。
        return tuple(sorted(
            (
                *((relative, "directory", "") for relative, _ in self.directories),
                *((relative, "file", "") for relative, _ in self.files),
                *((relative, member.kind, "") for relative, member in self.links),
            ),
            key=lambda member: member[0].encode("utf-8"),
        ))


@dataclass(frozen=True)
class PatchOperationScope:
    operation: object
    accesses: tuple
    observations: tuple
    fixed_skip: bool = False

    @property
    def requires_authorization(self):
        return any(access.has_links for access in self.accesses) or any(
            isinstance(member, PatchScopeTree) and member.links
            for member in self.observations
        )


def inspect_scope_tree(path):
    """完整普通结构元信息；不打开文件、不展开链接目标。"""
    root, chain = checked_chain(path, include_observation=True)
    if chain[-1][1] != "directory":
        raise ValueError("结构范围要求既有实际普通目录")
    directories = [("", _directory(root, root.lstat()))]
    files = []
    links = []
    pending = [directories[0][1]]

    while pending:
        directory = _check_directory(pending.pop())
        with os.scandir(directory.path) as entries:
            for entry in entries:
                validate_component(entry.name)
                child = directory.path / entry.name
                access = inspect_link_path(child, follow_final_link=False)
                if access.followed_links or access.actual_path != child:
                    raise ValueError("结构枚举期间实际普通父链变化")
                relative = child.relative_to(root).as_posix()
                if access.entry_link is not None:
                    links.append((relative, access))
                elif access.kind == "directory":
                    observed = _directory(child, child.lstat())
                    if observed.object_identity != access.object_identity:
                        raise ValueError("结构枚举期间目录身份变化")
                    directories.append((relative, observed))
                    pending.append(observed)
                elif access.kind == "file":
                    observed = observe_file(child)
                    if observed.object_identity != access.object_identity:
                        raise ValueError("结构枚举期间文件身份变化")
                    files.append((relative, observed))
                else:
                    raise ValueError("结构枚举期间成员消失")
        _check_directory(directory)

    for _, directory in directories:
        _check_directory(directory)
    for _, observed in files:
        actual = observe_file(observed.path)
        if actual.kind != "file":
            raise ValueError("结构范围文件消失")
        _assert_file_attributes(
            actual.attributes, observed.attributes, observed.path,
        )
    for _, observed in links:
        recheck_link_path(observed)
    order = lambda member: member[0].encode("utf-8")
    return PatchScopeTree(
        root, chain, tuple(sorted(directories, key=order)),
        tuple(sorted(files, key=order)), tuple(sorted(links, key=order)),
    )


def _family(kind):
    if kind in ("file", "file_symlink"):
        return "file"
    if kind in ("directory", "directory_symlink", "junction"):
        return "directory"
    return kind


def prepare_operation_scope(
    operation, primary, *, position, text_ops, authorize_enumeration=None,
    enumerate_directories=True,
):
    """先检查参数与入口；目录枚举可由整包协调延后至集中决定之后。"""
    if type(enumerate_directories) is not bool:
        raise TypeError("目录范围枚举开关必须是布尔值")
    kind = operation["op"]
    text = kind in text_ops
    accesses = [inspect_link_path(primary, follow_final_link=text)]
    if kind in DUAL_PATH_OPS:
        accesses.append(inspect_link_path(
            position(operation["new_path"]), follow_final_link=False,
        ))

    source = accesses[0]
    policy = operation.get("if_exists", "skip" if kind == "create_dir" else "error")
    fixed_skip = False
    if text:
        if source.kind not in ("absent", "file"):
            raise ValueError("正文目标必须是实际普通文件或可创建位置")
        if source.kind == "absent" and any(
            definition.kind == "file_symlink"
            for definition in source.followed_links
        ):
            raise ValueError("悬空文件软链接不取得创建引用目标的资格")
        if kind != "write_file" and source.kind != "file":
            raise ValueError("局部正文操作要求既有实际普通文件")
        if kind == "write_file" and source.kind == "file":
            if policy == "error":
                raise ValueError("整体写入实际目标已存在")
            fixed_skip = policy == "skip"
    elif kind in DUAL_PATH_OPS:
        target = accesses[1]
        required = "directory" if kind.endswith("_dir") else "file"
        if _family(source.kind) != required:
            raise ValueError("来源入口不存在或文件／目录类型不匹配")
        if target.kind != "absent" and _family(target.kind) != required:
            raise ValueError("目标入口文件／目录类型不匹配")
        if kind in ("rename_file", "rename_dir"):
            if source.nominal_path.parent != target.nominal_path.parent:
                raise ValueError("改名要求名义入口具有相同父目录")
            if source.actual_path.parent != target.actual_path.parent:
                raise ValueError("改名要求实际入口具有相同父目录")
        if target.kind != "absent":
            if policy == "error":
                raise ValueError("操作目标已存在")
            fixed_skip = policy == "skip"
    elif kind == "create_dir":
        if source.kind not in ("absent", "directory"):
            raise ValueError("create_dir不把目录链接入口当作普通目录")
        if source.kind == "directory":
            if policy == "error":
                raise ValueError("目录已经存在")
            fixed_skip = True
    else:
        required = "directory" if kind == "delete_dir" else "file"
        if _family(source.kind) != required:
            raise ValueError("指定入口不存在或文件／目录类型不匹配")

    return expand_operation_scope(
        PatchOperationScope(operation, tuple(accesses), (), fixed_skip),
        authorize_enumeration=authorize_enumeration,
        enumerate_directories=enumerate_directories,
    )


def expand_operation_scope(
    scope, *, authorize_enumeration=None, enumerate_directories=True,
):
    """只消费原固定入口；不重新定位来接受新指向或建立新基线。"""
    if not isinstance(scope, PatchOperationScope):
        raise TypeError("结构展开必须消费固定OP范围")
    if type(enumerate_directories) is not bool:
        raise TypeError("目录范围枚举开关必须是布尔值")
    operation = scope.operation
    accesses = scope.accesses
    fixed_skip = scope.fixed_skip
    # 选择交互或其他OP准备跨越新边界，先核对完整原入口。
    for access in accesses:
        recheck_link_path(access)
    observations = []
    for access in accesses:
        if access.entry_link is not None:
            # 转成实际位置的结构入口，不把名义父链交给工作核心。
            observed = inspect_link_path(
                access.actual_path, follow_final_link=False,
            )
        elif access.kind == "directory" and not fixed_skip and enumerate_directories:
            if access.followed_links:
                from services.scan_scope import describe_scan_access

                description = (
                    f'id="{operation["id"]}" {operation["op"]}\n'
                    + describe_scan_access(access)
                    + "\n\n本次仅枚举这个明确实际普通目录及其普通子目录。"
                    "\n树内目录链接仍是叶子，不进入未知后续链接目标。"
                    "\n此授权不读取普通文件正文，不授权删除、覆盖或恢复。"
                    "\n成员范围形成后，仍须进入本包完整范围决定和正式执行确认。"
                )
                if authorize_enumeration is None:
                    raise ValueError(
                        "实际目录尚未取得先行枚举授权，未进入目标目录。\n"
                        + description
                    )
                if not callable(authorize_enumeration):
                    raise TypeError("实际目录枚举授权必须可调用")
                decision = authorize_enumeration(
                    "修改包实际目录枚举授权", description,
                )
                if type(decision) is not bool:
                    raise TypeError("实际目录枚举授权必须返回明确布尔值")
                if not decision:
                    raise ValueError("实际目录枚举已取消，未读取正文或执行")
                recheck_link_path(access)
            observed = inspect_scope_tree(access.actual_path)
        else:
            observed = observe_file(access.actual_path) if access.kind != "directory" else access
        observations.append(observed)
        recheck_link_path(access)
    return PatchOperationScope(
        operation, tuple(accesses), tuple(observations), fixed_skip,
    )


def materialize_scope_observation(metadata):
    """授权后的新获取边界，正文一次获取并比回固定结构事实。"""
    if isinstance(metadata, PatchScopeTree):
        from types import MappingProxyType
        from core.link_paths import link_definition_digest
        from core.recovery_content import content_integrity

        # 授权等待是新边界；目录结构属性必须仍对应原枚举。
        # 正文获取消费同一固定成员，不扫描来重新选择或扩大范围。
        for _, directory in metadata.directories:
            _check_directory(directory)
        for _, observed in metadata.links:
            recheck_link_path(observed)
        files = []
        for relative, original in metadata.files:
            observed = observe_file(
                original.path, include_bytes=True,
                include_digest=True, include_mtime=True,
            )
            if observed.kind != "file":
                raise ValueError("授权后固定文件消失")
            _assert_file_attributes(
                observed.attributes, original.attributes, original.path,
            )
            files.append((relative, observed))
        # 晚文件获取可能跨越早成员变化，结束关口仅比较属性和定义。
        for _, directory in metadata.directories:
            _check_directory(directory)
        for _, observed in files:
            from core.file_observation import _check_observed_file_attributes
            _check_observed_file_attributes(observed)
        for _, observed in metadata.links:
            recheck_link_path(observed)
        records = tuple(sorted(
            (
                *((relative, "directory", "") for relative, _ in metadata.directories),
                *((relative, "file", observed.digest) for relative, observed in files),
                *((relative, observed.kind, link_definition_digest(observed.entry_link))
                  for relative, observed in metadata.links),
            ),
            key=lambda member: member[0].encode("utf-8"),
        ))
        return TreeObservation(
            metadata.path, metadata.chain, metadata.directories,
            tuple(files), records,
            MappingProxyType(content_integrity("directory", records)),
            tuple((relative, observed.mtime_ns) for relative, observed in files),
            metadata.links,
        )
    if isinstance(metadata, LinkPathObservation):
        return recheck_link_path(metadata)
    if not isinstance(metadata, FileObservation):
        raise TypeError("正文获取必须消费正式范围元信息")
    actual = observe_file(
        metadata.path, include_bytes=True,
        include_digest=True, include_mtime=True,
    )
    if actual.kind != metadata.kind:
        raise ValueError("授权后实际文件存在性变化")
    if actual.kind == "file":
        _assert_file_attributes(
            actual.attributes, metadata.attributes, metadata.path,
        )
    return actual


def recheck_scope_accesses(accesses):
    """依赖只复核，不把工作结束后的末级身份比回最初身份。"""
    for access in accesses:
        recheck_path_dependencies(access)


def format_package_scope(scopes, root):
    """范围正文只消费元信息；不读取工作正文。"""
    from services.patch_file_plan import TEXT_WRITE_OPS
    from services.patch_link_authorization import (
        format_link_content_access, format_link_structure_access,
    )

    lines = [
        "【本包全部固定范围】",
        "以下范围已完成元信息探查，普通文件正文尚未读取。",
        "目录链接叶子不展开；名义父链访问仅定位列出的实际工作范围。",
        "全部授权仅适用于这里已经列出的本包，不授权其他任务或未来OP。",
    ]
    for scope in scopes:
        operation = scope.operation
        lines.extend(("", "=" * 48, f'id="{operation["id"]}" {operation["op"]}'))
        for number, access in enumerate(scope.accesses):
            lines.extend((
                f"名义{'来源／入口' if number == 0 else '目标'}：{access.nominal_path}",
                f"实际{'来源／入口' if number == 0 else '目标'}：{access.actual_path}",
                f"类型：{access.kind}",
            ))
            for definition in access.followed_links:
                lines.extend((
                    f"访问依赖入口：{definition.entry_path}",
                    f"访问依赖原定义：{definition.raw_target!r}",
                    f"直接引用位置：{definition.direct_target}",
                ))
        if scope.fixed_skip:
            lines.append("if_exists固定跳过；不读取正文、不保护、不执行。")
        elif operation["op"] in TEXT_WRITE_OPS:
            if scope.accesses[0].has_links:
                lines.append(format_link_content_access(
                    operation, scope.accesses[0], root,
                ))
            else:
                lines.append("只编辑列出的实际普通文件正文。")
        else:
            lines.append(format_link_structure_access(
                operation, scope.observations, root,
            ))
    lines.extend((
        "",
        "选择跳过会裁剪该OP的正文、保护和访问依赖。",
        "保留范围的正文定位、实际冲突及保护规划仍必须全部通过。",
        "范围决定不是正式工作执行确认；完整预演通过后再确认执行。",
    ))
    return "\n".join(lines)


def patch_target_groups(scopes):
    """业务只提供实际改变关联键；共同归组不复制来源侧算法。

    正文按实际位置及文件身份关联，结构按操作入口关联；
    复制关联目标，移动关联两端，固定跳过不连接其他成员。
    """
    from core.work_selection import group_related_indices
    from services.patch_file_plan import TEXT_WRITE_OPS

    keys_by_member = []
    for scope in scopes:
        keys = []
        if not scope.fixed_skip:
            kind = scope.operation["op"]
            accesses = scope.accesses
            changed = (
                accesses[1:] if kind in ("copy_file", "copy_dir") else
                accesses if kind in DUAL_PATH_OPS else accesses[:1]
            )
            for access in changed:
                keys.append(("position", identity(access.actual_path)))
                if (
                    kind in TEXT_WRITE_OPS and access.kind == "file"
                    and access.object_identity is not None
                ):
                    keys.append(("content_object", access.object_identity))
        keys_by_member.append(tuple(keys))
    return group_related_indices(keys_by_member)


def apply_patch_target_decisions(scopes, decisions):
    """任一实际目标取消，同组全部OP取消；固定跳过不扩大取消范围。"""
    scopes = tuple(scopes)
    if (
        type(decisions) is not tuple or len(decisions) != len(scopes)
        or any(type(value) is not str or value not in ("authorize", "skip")
               for value in decisions)
    ):
        raise ValueError("目标决定必须准确对应全部固定OP")
    result = list(decisions)
    for numbers in patch_target_groups(scopes):
        if any(
            result[number] == "skip" and not scopes[number].fixed_skip
            for number in numbers
        ):
            for number in numbers:
                result[number] = "skip"
    for number, scope in enumerate(scopes):
        if scope.fixed_skip:
            result[number] = "skip"
    return tuple(result)