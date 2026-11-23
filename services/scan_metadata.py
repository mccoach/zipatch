# -*- coding: utf-8 -*-

"""扫描链接目标的明确元信息授权及固定观察；不读取正文或枚举目录。"""

from dataclasses import dataclass
from pathlib import Path

from core.file_observation import ObjectIdentity, observe_file, recheck_file
from core.link_paths import inspect_link_path, recheck_link_path
from core.recovery_paths import checked_chain, contains, identity
from services.scan_scope import (
    describe_scan_access, require_scan_authorization, scan_display_path,
)


@dataclass(frozen=True)
class ScanTargetMetadata:
    entry: object
    access: object
    file: object = None
    directory_identity: object = None
    directory_mtime_ns: int | None = None


def prepare_target_metadata(scope, output, *, authorize):
    """最小路径定位后展示实际位置，再取得所选链接目标元信息。

    路径定位只查询属性与定义；不读取正文、不递归枚举。
    目录大小不累计，目录时间仅表达目录自身修改时间。
    原入口和全部实际访问依赖保留至发布前复核。
    """
    located = []
    for entry in scope.links:
        recheck_link_path(entry)
        # 树内入口观察使用实际路径；目标访问必须恢复名义扫描路线，
        # 才能同时固定扫描根及叶子链接，不能绕过原根访问依赖。
        nominal = scan_display_path(scope, entry.actual_path)
        access = inspect_link_path(nominal, follow_final_link=True)
        if access.kind not in ("absent", "file", "directory"):
            raise ValueError("扫描链接目标没有定位到受支持的普通对象")
        if identity(access.actual_path) == identity(output.path):
            raise ValueError("扫描链接元信息目标是本次输出文件，禁止生成自引用元信息")
        if (
            access.kind == "file" and output.kind == "file"
            and access.object_identity == output.object_identity
        ):
            raise ValueError(
                "扫描输出与链接元信息目标共享硬链接对象，禁止覆盖目标正文"
            )
        if access.kind == "directory" and contains(
            access.actual_path, output.path,
        ):
            raise ValueError(
                "扫描输出位于链接元信息目标目录内；"
                "输出目录、暂存及发布会改变目标目录时间，请选择范围外成果位置"
            )
        located.append((entry, access))
    if not located:
        return ()

    lines = [
        "下面只查询所选链接最终普通目标的大小及修改时间。",
        "不读取文件正文，不枚举目录，不计算目录内容总大小。",
        "普通文件大小是目标文件字节数；目录只查询自身修改时间。",
        "以下是本次固定的全部实际位置，授权不扩展到其他对象。",
        "",
    ]
    for entry, access in located:
        lines.extend((
            describe_scan_access(entry),
            f"元信息实际目标：{access.actual_path}",
            f"目标类型：{access.kind}",
        ))
        for definition in access.followed_links:
            lines.extend((
                f"实际访问经过：{definition.entry_path}",
                f"原定义：{definition.raw_target!r}",
                f"直接引用：{definition.direct_target}",
            ))
        lines.append("")
    require_scan_authorization(
        authorize, "扫描链接目标大小与日期授权", "\n".join(lines),
    )

    result = []
    for entry, access in located:
        recheck_link_path(entry)
        recheck_link_path(access)
        if access.kind == "file":
            observed = observe_file(access.actual_path, include_mtime=True)
            if (
                observed.kind != "file"
                or observed.object_identity != access.object_identity
            ):
                raise ValueError("扫描目标元信息取得期间文件身份变化")
            result.append(ScanTargetMetadata(entry, access, file=observed))
        elif access.kind == "directory":
            target, chain = checked_chain(
                access.actual_path, include_observation=True,
            )
            if chain[-1][1] != "directory":
                raise ValueError("扫描元信息目标不再是普通目录")
            attributes = target.lstat()
            identifier = ObjectIdentity(attributes.st_dev, attributes.st_ino)
            if identifier != access.object_identity:
                raise ValueError("扫描目标元信息取得期间目录身份变化")
            result.append(ScanTargetMetadata(
                entry, access, directory_identity=identifier,
                directory_mtime_ns=attributes.st_mtime_ns,
            ))
        else:
            result.append(ScanTargetMetadata(entry, access))
    result = tuple(result)
    recheck_target_metadata(result)
    return result


def recheck_target_metadata(members):
    """新边界核对固定事实；不枚举、不读正文、不接受新目标。"""
    for member in members:
        recheck_link_path(member.entry)
        recheck_link_path(member.access)
        if member.file is not None:
            current = recheck_file(member.file, check_mtime=True)
            if current.attributes.st_size != member.file.attributes.st_size:
                raise ValueError("扫描链接目标文件大小变化")
        elif member.directory_identity is not None:
            target, chain = checked_chain(
                member.access.actual_path, include_observation=True,
            )
            if chain[-1][1] != "directory":
                raise ValueError("扫描链接目标目录类型变化")
            attributes = Path(target).lstat()
            if (
                ObjectIdentity(attributes.st_dev, attributes.st_ino)
                != member.directory_identity
                or attributes.st_mtime_ns != member.directory_mtime_ns
            ):
                raise ValueError("扫描链接目标目录身份或修改时间变化")