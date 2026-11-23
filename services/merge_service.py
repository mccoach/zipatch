# -*- coding: utf-8 -*-

"""三种合并来源的固定选择、严格正文、尾部遗漏名单及唯一发布。"""

import errno
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event

from core.constants import MERGE_MODES
from core.file_observation import FileObservation, observe_file

from core.merged_format import (
    MTIME_FORMAT, MTIME_PREFIX, MTIME_UNAVAILABLE, STRUCTURAL_NEWLINE,
    validate_markers,
)
from core.rule_lists import validate_file_rules
from core.recovery_paths import (
    UnsafePathError, checked_chain, contains, entity_type_from_attributes,
    validate_component,
)
from core.text_io import TextDecodingError, decode_text_bytes
from core.text_newlines import normalize_newlines
from core.time_utils import current_timestamp_text
from services.demand_rules import (
    build_demand_selection, demand_physical_lines,
    prepare_demand_inspection, inspect_prepared_demand_rules, windows_path_key,
)
from services.stream_output import (
    StreamOutputPostError, StreamOutputResult, publish_stream_output,
)


@dataclass(frozen=True)
class MergeResult:
    output_result: StreamOutputResult
    total_files: int
    excluded_count: int = 0
    read_failure_count: int = 0
    mtime_failures: tuple = ()
    entry_counts: tuple = ()

    @property
    def output_file(self):
        return str(self.output_result.target)


def read_text_content_for_merge(file_path, *, expected_access=None, observations=None):
    """一次读取并解码；正式生成可以保留摘要供发布前新边界比较。"""
    observed = observe_file(
        file_path, include_bytes=True,
        include_digest=observations is not None,
    )
    if observed.kind != "file":
        raise FileNotFoundError(f"合并来源文件已经不存在：{observed.path}")
    if expected_access is not None and observed.object_identity != expected_access.object_identity:
        raise ValueError("合并首次正文读取的对象不对应固定入口身份")
    content, _ = decode_text_bytes(
        observed.source_bytes,
        source_description="合并来源文件", path=observed.path,
    )
    if observations is not None:
        from dataclasses import replace
        observations.append(replace(observed, source_bytes=None))
    return content


def build_regular_merge_plan(
    source_folder, exclusions, output_file=None, *,
    request_enumeration=None, root_access=None, ignored_entries=None,
    source_enumeration=None,
):
    """名称排除先于定义读取；每个保留入口只建立一份名义访问观察。"""
    import stat
    from core.exclusion_rules import PreparedExclusions
    from core.link_paths import inspect_link_path, recheck_link_path
    from services.scan_scope import describe_scan_access

    if not isinstance(exclusions, PreparedExclusions):
        raise TypeError("目录合并必须消费正式排除名单")
    if root_access is None:
        root_access = inspect_link_path(source_folder, follow_final_link=True)
    else:
        recheck_link_path(root_access)
        if windows_path_key(source_folder) != windows_path_key(root_access.nominal_path):
            raise ValueError("目录来源根观察不对应本次名义源目录")
    if root_access.kind != "directory":
        raise ValueError("合并源文件夹必须定位到既有实际普通目录")
    if root_access.has_links:
        description = (
            describe_scan_access(root_access)
            + "\n允许本次枚举这个明确实际普通目录及未排除的普通子树元信息。"
            "\n途中目录链接保持叶子；不读取正文；本次许可不保存。"
        )
        if request_enumeration is None or not callable(request_enumeration):
            raise ValueError("合并实际源目录尚未取得枚举授权。\n" + description)
        decision = request_enumeration(
            root_access, "合并实际源目录枚举授权", description,
        )
        if type(decision) is not bool:
            raise TypeError("合并目录枚举请求必须返回明确布尔值")
        if not decision:
            raise ValueError("本次合并实际目录枚举未授权或已取消")
    recheck_link_path(root_access)
    from services.source_enumeration import SourceEnumeration

    if source_enumeration is None:
        source_enumeration = SourceEnumeration()
    if not isinstance(source_enumeration, SourceEnumeration):
        raise TypeError("目录来源必须消费正式任务枚举状态")
    root = root_access.actual_path
    pending = [(root, ())]
    plan = []
    output_key = None if output_file is None else windows_path_key(output_file)

    def ignored(nominal, kind, reason):
        if ignored_entries is not None:
            ignored_entries.append((str(nominal), kind, reason))

    while pending:
        folder, parts = pending.pop()
        fixed = inspect_link_path(folder, follow_final_link=False)
        if fixed.kind != "directory" or fixed.has_links:
            raise ValueError("目录合并实际普通父链变化")
        members = source_enumeration.entries(folder)
        for member_number, (name, spelling, _) in enumerate(members):
            validate_component(name)
            child = folder / name
            if output_key is not None and windows_path_key(child) == output_key:
                continue
            # Windows DirEntry属性可能没有有效设备或编号。
            # 身份比较统一使用共享状态取得的不跟随路径属性，
            # 不删除设备／编号比较，也不把零编号解释成同一对象。
            attributes = source_enumeration.attributes(members, member_number)
            relative = parts + (name,)
            nominal = root_access.nominal_path.joinpath(*relative)
            directory = (
                stat.S_ISDIR(attributes.st_mode)
                or bool(getattr(attributes, "st_file_attributes", 0) & 0x10)
            )
            if directory and exclusions.directories.matches(relative):
                ignored(nominal, "directory", "排除目录名单")
                continue
            if not directory and exclusions.files.matches(relative):
                ignored(nominal, "file", "排除文件名单")
                continue
            access = inspect_link_path(nominal, follow_final_link=False)
            if access.actual_path != child or access.kind == "absent":
                raise ValueError("目录合并成员位置、存在性或父链变化")
            if (
                access.object_identity.device != attributes.st_dev
                or access.object_identity.inode != attributes.st_ino
            ):
                raise ValueError("目录枚举至入口观察期间对象身份变化")
            plan.append({
                "path": str(nominal), "merge_content": True, "access": access,
            })
            if access.kind == "directory":
                pending.append((child, relative))
        recheck_link_path(fixed)
    recheck_link_path(root_access)
    return sorted(plan, key=lambda entry: entry["path"].lower())


def _validate_merge_leaf_positions(accesses):
    """按路径组件排序后检查祖先栈，避免全部来源逐对比较。"""
    from core.recovery_paths import contains, identity

    ordered = sorted(
        accesses,
        key=lambda access: tuple(
            component.casefold() for component in access.nominal_path.parts
        ),
    )
    stack = []
    previous = None
    for access in ordered:
        nominal = access.nominal_path
        key = identity(nominal)
        if key == previous:
            raise ValueError("合并容器包含重复名义入口")
        previous = key
        while stack and not contains(stack[-1].nominal_path, nominal):
            stack.pop()
        if stack and stack[-1].kind != "directory":
            raise ValueError(
                "合并文件或链接结构叶子不能同时包含后代记录；"
                "请分开明确来源，避免生成无法拆分的结构容器"
            )
        stack.append(access)


def format_merge_omissions(ignored, failures, *, successful_paths=()):
    """尾部报告消费已有决定，不扫描目录、不生成可拆分正文块。"""
    from core.recovery_paths import contains, identity

    def compact(records):
        unique = {}
        for path, kind, reason in records:
            key = identity(path)
            member = unique.get(key)
            if member is None:
                unique[key] = [path, kind, [reason]]
            elif reason not in member[2]:
                member[2].append(reason)
        directories = tuple(
            member for member in unique.values() if member[1] == "directory"
        )
        return tuple(
            member for member in unique.values()
            if not any(
                identity(parent[0]) != identity(member[0])
                and contains(parent[0], member[0])
                for parent in directories
            )
        )

    lines = []
    for title, records in (
        ("本次主动忽略（排除名单／未授权）", ignored),
        ("本次被动失败（正文读取／解码）", failures),
    ):
        members = compact(records)
        lines.extend(("", "【" + title + "】"))
        if not members:
            lines.append("无")
            continue
        for path, kind, reasons in members:
            label = "目录" if kind == "directory" else "文件"
            explanation = ""
            if kind == "directory":
                successful = {
                    identity(member) for member in successful_paths
                    if identity(member) != identity(path) and contains(path, member)
                }
                if successful:
                    explanation = (
                        f"；此处仅表示该目录来源被忽略，另有 {len(successful)} "
                        "项通过其他明确来源成功输出，见正文；并非整目录全部未输出"
                    )
            lines.append(
                f"- [{label}] {path}；{'；'.join(reasons)}{explanation}"
            )
    return normalize_newlines("\n".join(lines) + "\n", STRUCTURAL_NEWLINE)


def _authorize_merge(authorize, title, description):
    if authorize is None:
        raise ValueError("合并链接范围尚未取得本次授权。\n" + description)
    if not callable(authorize):
        raise TypeError("合并范围授权必须是可调用对象")
    decision = authorize(title, description)
    if type(decision) is not bool:
        raise TypeError("合并范围授权必须返回明确布尔值")
    if not decision:
        raise ValueError("本次合并范围决定已取消，未发布成果")


def build_demand_merge_plan(file_paths, accesses):
    """消费一次选择的固定入口，不再次定位来建立新的来源基线。"""
    by_path = {
        windows_path_key(access.nominal_path): access for access in accesses
    }
    plan = []
    for path in file_paths:
        access = by_path.get(windows_path_key(path))
        if access is None:
            raise ValueError("正式列表选择缺少对应的固定入口观察")
        plan.append({
            "path": str(path), "merge_content": True, "access": access,
        })
    return plan


def read_source_mtime(file_path):
    """正文决定之后独立观察时间；保持缺失及资源故障政策。"""
    try:
        path = Path(file_path)
        checked_chain(path.parent)
        if path.name and path.name not in (".", ".."):
            validate_component(path.name)
        attributes = os.stat(file_path, follow_symlinks=False)
        entity_type_from_attributes(path, attributes)
    except UnsafePathError:
        raise
    except OSError as error:
        if error.errno in (errno.ENOMEM, errno.EMFILE, errno.ENFILE, errno.ENOSPC):
            raise
        return None, str(error)

    try:
        observed = datetime.fromtimestamp(
            attributes.st_mtime_ns // 1_000_000_000,
        )
    except (OverflowError, OSError, ValueError) as error:
        return None, f"修改时间无法表示为本地时间：{error}"
    return observed.strftime(MTIME_FORMAT), ""


def _extra_text_chunks(text, *, before=False, ending=False):
    """普通附加文本保留布局政策，不套用精确源码正文契约。"""
    if not text:
        return
    complete = normalize_newlines(text, "\n").rstrip("\n")
    if before:
        yield STRUCTURAL_NEWLINE
    yield normalize_newlines(complete, STRUCTURAL_NEWLINE)
    yield STRUCTURAL_NEWLINE if ending else STRUCTURAL_NEWLINE * 2


def write_merge_file(
    file_plan, expected_output, preamble_text, ending_text,
    code_header_line, code_footer_line, log_func=None, *,
    normalize_text_newlines=False, record_file_mtime=True,

    output_rules=None, match_root=None, authorize_merge_scope=None,
    enumeration_accesses=(), select_merge_scope=None, ignored_entries=(),
):
    """消费已形成来源清单及固定输出，完整生成后才发布。"""
    if not isinstance(expected_output, FileObservation):
        raise TypeError("合并容器必须消费固定输出FileObservation")
    validate_markers(code_header_line, code_footer_line)

    for name, value in (
        ("正文标准化", normalize_text_newlines),
        ("记录文件修改时间", record_file_mtime),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name}开关必须是布尔值")
    if normalize_text_newlines:
        validate_file_rules(output_rules)

    file_plan = tuple(file_plan)
    output_key = windows_path_key(expected_output.path)
    if any(windows_path_key(entry["path"]) == output_key for entry in file_plan):
        raise ValueError("输出文件被实际选中，不能执行按名单合并。")
    from core.link_paths import (
        inspect_link_path, recheck_link_path, specification_from_link,
    )

    from core.recovery_paths import identity
    from services.scan_scope import describe_scan_access

    root = Path(os.path.abspath(match_root)) if match_root is not None else None
    prepared = []
    source_observations = []
    source_accesses = []
    actual_positions = {}
    enumeration_accesses = tuple(enumeration_accesses)
    for access in enumeration_accesses:
        recheck_link_path(access)
        if access.kind != "directory" or not access.follow_final_link:
            raise ValueError("列表枚举依赖不是正式实际目录访问观察")
        if contains(expected_output.path, access.actual_path):
            raise ValueError("合并输出范围包含固定列表实际枚举目录")
        for definition in access.followed_links:
            if contains(expected_output.path, definition.entry_path):
                raise ValueError("合并输出会改变列表枚举链接依赖")
        for directory in access.directories:
            if contains(expected_output.path, directory.path):
                raise ValueError("合并输出会改变列表枚举父目录身份")
    for entry in file_plan:
        access = entry.get("access")
        if access is None:
            access = inspect_link_path(entry["path"], follow_final_link=False)
        recheck_link_path(access)
        if access.kind not in (
            "file", "directory", "file_symlink", "directory_symlink", "junction",
        ):
            raise ValueError("合并固定入口不存在或类型不支持")
        if identity(access.actual_path) == identity(expected_output.path):
            raise ValueError("合并实际来源与输出位置相同")
        if access.kind == "directory" and contains(
            expected_output.path, access.actual_path,
        ):
            raise ValueError("合并输出范围包含来源目录")
        if (
            access.kind == "file"
            and expected_output.kind == "file"
            and access.object_identity == expected_output.object_identity
        ):
            raise ValueError("合并来源与输出共享硬链接对象，禁止覆盖")
        for definition in access.followed_links:
            if contains(expected_output.path, definition.entry_path):
                raise ValueError("合并输出会改变来源链接访问依赖")

        # 被去重入口仍属于来源安全依赖，不能因正文只读一次而丢掉。
        source_accesses.append(access)
        actual_key = (
            ("file_object", access.object_identity)
            if access.kind == "file" else
            ("entry", identity(access.actual_path))
        )
        previous_index = actual_positions.get(actual_key)
        if previous_index is not None:
            previous_entry, previous_access = prepared[previous_index]
            if previous_access.kind != access.kind:
                raise ValueError("同一实际来源的入口类型矛盾")
            if (
                normalize_text_newlines and access.kind == "file"
                and entry["merge_content"] and previous_entry["merge_content"]
            ):
                def newline_for(nominal):
                    within_root = root is not None and contains(root, nominal)
                    parts = (
                        nominal.relative_to(root).parts
                        if within_root else nominal.parts[1:]
                    )
                    return output_rules.matches(parts, ignore_root=not within_root)

                if newline_for(access.nominal_path) != newline_for(previous_access.nominal_path):
                    raise ValueError(
                        "同一实际普通正文的名义别名要求不同换行成果；"
                        "请统一规则，不生成矛盾容器"
                    )
            # 首次位置保持；任何明确包含都优先于目录文件排除。
            prepared[previous_index] = (
                {
                    **previous_entry,
                    "merge_content": (
                        previous_entry["merge_content"] or entry["merge_content"]
                    ),
                },
                previous_access,
            )
            continue
        actual_positions[actual_key] = len(prepared)
        prepared.append((entry, access))
    _validate_merge_leaf_positions(source_accesses)
    ignored_entries = list(ignored_entries)
    failure_entries = []
    successful_paths = []
    if select_merge_scope is not None:
        from core.work_selection import selected_indices

        if not callable(select_merge_scope):
            raise TypeError("合并固定来源选择必须可调用")
        retained = selected_indices(
            select_merge_scope(tuple(prepared), tuple(source_accesses), expected_output),
            len(prepared), description="合并固定来源选择",
        )
        from services.source_selection import unselected_merge_records

        ignored_entries.extend(unselected_merge_records(
            prepared, source_accesses, retained,
        ))
        prepared = [prepared[number] for number in retained]
        selected_keys = {
            ("file_object", access.object_identity)
            if access.kind == "file" else
            ("entry", identity(access.actual_path))
            for _, access in prepared
        }
        # 去重后的块与全部实际使用别名共同裁剪。
        # 不保留未选块依赖，也不遗漏所选块的其他名义入口。
        source_accesses = [
            access for access in source_accesses
            if (
                ("file_object", access.object_identity)
                if access.kind == "file" else
                ("entry", identity(access.actual_path))
            ) in selected_keys
        ]
        enumeration_accesses = tuple(
            enumeration for enumeration in enumeration_accesses
            if any(
                contains(enumeration.nominal_path, access.nominal_path)
                for access in source_accesses
            )
        )
    linked = tuple(access for access in source_accesses if access.has_links)
    if linked and select_merge_scope is None:
        _authorize_merge(
            authorize_merge_scope,
            "合并全部固定链接及实际访问范围",
            "\n\n".join(describe_scan_access(access) for access in linked)
            + "\n\n末级链接只保存原定义，不读取引用目标正文。"
            "\n经过目录链接父链的普通文件，仅在列出的实际位置读取正文。"
            "\n普通目录结构块不携带正文；所有范围均属于本次任务。"
            + f"\n成果位置：{expected_output.path}",
        )
    for _, access in prepared:
        recheck_link_path(access)
    file_plan = tuple(prepared)
    entry_counts = dict.fromkeys((
        "file", "directory", "file_symlink", "directory_symlink", "junction",
    ), 0)
    total = read_failure_count = 0
    excluded_count = len(ignored_entries)
    mtime_failures = []

    def collect_blocks(container):
        nonlocal total, excluded_count, read_failure_count
        for entry, access in file_plan:
            source_path = entry["path"]
            recheck_link_path(access)
            if access.kind != "file":
                specification = (
                    None if access.entry_link is None else
                    specification_from_link(access.entry_link)
                )
                container.add_block(
                    source_path, "", kind=access.kind,
                    specification=specification,
                )
                total += 1
                entry_counts[access.kind] += 1
                successful_paths.append(source_path)
                continue
            actual_source = access.actual_path
            display_path = os.path.abspath(source_path)
            if any(ord(character) < 32 for character in display_path):
                raise ValueError(f"合并来源路径包含控制字符：{display_path!r}")
            reasons = []
            if not entry["merge_content"]:
                ignored_entries.append((
                    display_path, "file", "排除文件名单",
                ))
                excluded_count += 1
                continue
            try:
                body = read_text_content_for_merge(
                    actual_source, expected_access=access,
                    observations=source_observations,
                )
            except (
                FileNotFoundError, PermissionError,
                IsADirectoryError, NotADirectoryError, TextDecodingError,
            ) as error:
                read_failure_count += 1
                failure_entries.append((
                    display_path, "file",
                    f"{type(error).__name__}：{error}",
                ))
                if log_func:
                    log_func(f"合并来源失败，正文未输出：{display_path}；{error}")
                continue

            if normalize_text_newlines:
                within_root = root is not None and contains(root, display_path)
                parts = (
                    Path(display_path).relative_to(root).parts
                    if within_root else Path(display_path).parts[1:]
                )
                matched = output_rules.matches(
                    parts, ignore_root=not within_root,
                )
                body = normalize_newlines(body, "\r\n" if matched else "\n")

            modified_at = None
            if record_file_mtime:
                modified_at, failure = read_source_mtime(actual_source)
                if failure:
                    mtime_failures.append((display_path, failure))
                    reasons.append(f"修改时间获取失败：{failure}")

            if log_func:
                if total <= 20 or total % 50 == 0:
                    log_func(f"正在合并第 {total} 个文件：{display_path}")
                for reason in reasons:
                    log_func(f"合并来源：{display_path}；{reason}")
            total += 1
            entry_counts["file"] += 1
            successful_paths.append(display_path)
            container.add_block(
                display_path, body,
                mtime=(
                    modified_at if modified_at is not None else MTIME_UNAVAILABLE
                ) if record_file_mtime else None,
            )

    def recheck_sources():
        from core.file_observation import recheck_file

        from services.source_recheck import recheck_source_paths

        recheck_source_paths((*enumeration_accesses, *source_accesses))
        # 全文生成及暂存关闭是新的边界；比较首次成功读取的摘要，
        # 不重新解码、构建正文或把后来字节替代本次成果。
        for observed in source_observations:
            recheck_file(observed)

    from core.merged_container import MergedContainer

    preamble = (
        current_timestamp_text() + STRUCTURAL_NEWLINE * 2
        + "".join(_extra_text_chunks(preamble_text))
    )
    ending = "".join(_extra_text_chunks(
        ending_text, before=True, ending=True,
    ))
    with MergedContainer(
        code_header_line, code_footer_line,
        preamble=preamble, ending=ending,
    ) as container:
        collect_blocks(container)
        # 尾部名单属于本层说明，不生成源码块；在固定动态标识前加入，
        # 路径、原因和附加正文同样接受完整标识避撞。
        # 汇总直接消费本次成功加入容器的块计数，不重新读取或解析。
        # 放在全部源码块之后，作为本层说明，不成为拆分输入记录。
        container.ending = (
            ending
            + STRUCTURAL_NEWLINE.join((
                "",
                "【本次合并汇总】",
                f"成功正文文件：{entry_counts['file']} 个",
                f"普通目录结构：{entry_counts['directory']} 个",
                f"文件软链接：{entry_counts['file_symlink']} 个",
                f"目录软链接：{entry_counts['directory_symlink']} 个",
                f"目录联接 Junction：{entry_counts['junction']} 个",
                f"成功输出入口块合计：{total} 个",
                f"主动忽略记录：{excluded_count} 条",
                f"正文读取／解码失败：{read_failure_count} 个",
                f"文件修改时间缺失：{len(mtime_failures)} 个",
                "链接入口块仅保存原定义，不计入成功正文文件数。",
                "主动忽略记录可能代表整个目录，不等于忽略文件总数。",
                "",
            ))
            + format_merge_omissions(
                ignored_entries, failure_entries,
                successful_paths=tuple(successful_paths),
            )
        )
        output = publish_stream_output(
            expected_output,
            (chunk.encode("utf-8") for chunk in container.text_chunks()),
            recheck_context=recheck_sources,
        )
    return MergeResult(
        output, total, excluded_count, read_failure_count,
        tuple(mtime_failures), tuple(entry_counts.items()),
    )


def _log_completed(result, mode, log_func):
    if log_func is None:
        return
    try:
        counts = dict(result.entry_counts)
        log_func(
            f"{mode}完成：成功正文文件 {counts.get('file', 0)} 个，"
            f"普通目录结构 {counts.get('directory', 0)} 个，"
            "链接入口 "
            f"{sum(counts.get(kind, 0) for kind in ('file_symlink', 'directory_symlink', 'junction'))} 个；"
            f"合计 {result.total_files} 个容器入口块"
        )
        log_func(
            f"合并成果已发布；主动忽略 {result.excluded_count} 个，"
            f"被动失败 {result.read_failure_count} 个，"
            f"修改时间未取得 {len(result.mtime_failures)} 个。"
        )
    except (OSError, UnicodeError) as error:
        raise StreamOutputPostError(
            f"{mode}成果已经提交：{result.output_file}；"
            "完成日志失败，不代表成果未发布，没有自动撤销。\n"
            f"主动忽略 {result.excluded_count} 个；"
            f"被动失败 {result.read_failure_count} 个；"
            f"修改时间未取得 {len(result.mtime_failures)} 个。\n"
            + "".join(
                f"- {path}：{reason}\n"
                for path, reason in result.mtime_failures
            )
            + str(error),
            result,
        ) from error


def merge_files(
    mode, source_folder, list_text, output_file, exclusions,
    preamble_text, ending_text, code_header_line, code_footer_line,
    log_func=None, *, normalize_text_newlines=False, record_file_mtime=True,

    output_rules=None, authorize_merge_scope=None, select_merge_scope=None,
    select_merge_enumeration=None,
):
    """唯一合并入口：按模式形成一次有序清单，然后生成和发布一次。

    目录排除只作用于目录选择。列表是明确包含：
    与目录已有路径重复时保留目录顺序，但提供真实正文；
    其余列表文件追加在目录文件之后。去重不读取文件正文。
    编辑窗口的检查结果不作为执行资格，本次重新检查所需列表一次。
    """
    if type(mode) is not str or mode not in MERGE_MODES:
        raise ValueError("合并模式必须是 directory、list 或 combined")
    if mode in ("directory", "combined") and not source_folder:
        # 不允许空路径经checked_chain隐式变为当前工作目录。
        raise ValueError("源文件夹不能为空；本次没有选择目录或发布成果")

    from services.source_authorization import EnumerationAuthorization

    # 任务局部决定：列表与目录共用，任务结束不保存到配置或下一任务。
    enumeration_authorization = EnumerationAuthorization(authorize_merge_scope)
    if select_merge_enumeration is not None and not callable(select_merge_enumeration):
        raise TypeError("合并枚举范围选择必须可调用")
    from core.link_paths import inspect_link_path
    from core.recovery_paths import identity
    from services.demand_rules import DemandCheckCancelled

    from services.source_enumeration import SourceEnumeration

    cancel = Event()
    source_enumeration = SourceEnumeration()
    root_access = None
    if mode in ("directory", "combined"):
        from core.exclusion_rules import PreparedExclusions

        if not isinstance(exclusions, PreparedExclusions):
            raise TypeError("目录合并必须消费正式排除名单")
        root_access = inspect_link_path(source_folder, follow_final_link=True)
        if root_access.kind != "directory":
            raise ValueError("合并源文件夹必须定位到既有实际普通目录")

    jobs = (
        tuple(enumerate(demand_physical_lines(list_text), 1))
        if mode in ("list", "combined") else ()
    )
    prepared_rules = prepare_demand_inspection(jobs, source_folder, cancel)
    # 语法、明确入口及固定前缀错误在选择前拒绝，不能跳过错误规则。
    preparation_errors = tuple(
        f"合并列表第 {number} 行：{result.issue}"
        for number, rule, access, result in prepared_rules
        if result is not None and result.issue
    )
    if preparation_errors:
        raise ValueError(
            "合并列表检查未通过，本次未发布成果。\n"
            + "\n".join(preparation_errors)
        )

    prefixes = tuple(
        access for _, _, access, _ in prepared_rules
        if access is not None and access.has_links
    )
    if root_access is not None and root_access.has_links:
        prefixes = (root_access, *prefixes)
    skipped_prefixes = (
        enumeration_authorization.select(prefixes, select_merge_enumeration)
        if select_merge_enumeration is not None else ()
    )
    skip_directory_source = (
        root_access is not None
        and identity(root_access.nominal_path) in skipped_prefixes
    )
    selected_files = ()
    selection_accesses = ()
    enumeration_accesses = ()
    if mode in ("list", "combined"):
        try:
            matches = inspect_prepared_demand_rules(
                prepared_rules, cancel,
                request_enumeration=enumeration_authorization.request,
                skipped_prefixes=skipped_prefixes,
                source_enumeration=source_enumeration,
            )
        except DemandCheckCancelled as error:
            raise ValueError(
                "本次合并列表授权已取消，未发布成果；"
                "没有保存业务授权或自动继续"
            ) from error
        ordered_matches = tuple(matches[number] for number, _ in jobs)
        selection = build_demand_selection(ordered_matches)
        if selection.requires_authorization:
            raise ValueError(
                "合并列表含需要实际目录枚举授权的固定前缀；"
                "本次未进入这些实际目录，也未取得执行文件清单"
            )
        if not selection.complete or selection.has_errors:
            reasons = [
                f"合并列表第 {number} 行：{match.issue}"
                for number, match in zip(
                    (number for number, _ in jobs), ordered_matches,
                )
                if match.issue
            ]
            raise ValueError(
                "合并列表检查未通过，本次未发布成果。\n"
                + "\n".join(reasons)
            )
        selected_files = selection.files
        selection_accesses = selection.accesses
        enumeration_accesses = selection.enumeration_accesses
        if mode == "list" and not selected_files and not skipped_prefixes:
            raise ValueError("仅列表模式至少需要一条实际选中文件的启用规则")

    expected = observe_file(output_file, include_digest=True)
    output_key = windows_path_key(expected.path)
    if any(windows_path_key(path) == output_key for path in selected_files):
        raise ValueError("合并列表选中了本次输出文件，不能合并")

    if log_func:
        log_func(f"开始合并：{mode}")
        if mode in ("directory", "combined"):
            log_func(f"源文件夹：{source_folder}")
        log_func(f"输出文件：{expected.path}")

    ignored_entries = [
        (str(access.nominal_path), "directory", "本次目录枚举未授权")
        for access in prefixes
        if identity(access.nominal_path) in skipped_prefixes
    ]
    plan = (
        build_regular_merge_plan(
            source_folder, exclusions, expected.path,
            request_enumeration=enumeration_authorization.request,
            root_access=root_access, ignored_entries=ignored_entries,
            source_enumeration=source_enumeration,
        )
        if mode in ("directory", "combined") and not skip_directory_source else []
    )
    positions = {
        windows_path_key(entry["path"]): index
        for index, entry in enumerate(plan)
    }
    # 目录来源已经排好序；只有明确遍历到的文件排除项被列表补回时，
    # 才恢复其目录排序位置。排除目录内部没有枚举，不凭名义根包含
    # 将其后代误归为目录来源。链接源根同样消费这些已取得事实。
    directory_order_keys = set(positions)
    directory_order_keys.update(
        windows_path_key(path)
        for path, kind, reason in ignored_entries
        if kind == "file"
    )
    for entry in build_demand_merge_plan(selected_files, selection_accesses):
        key = windows_path_key(entry["path"])
        previous = positions.get(key)
        if previous is not None:
            # 明确包含优先于目录文件排除，不创建重复块或重复正文读取。
            if plan[previous]["access"] != entry["access"]:
                raise ValueError("目录与列表对同一名义来源的固定观察不一致")
            plan[previous]["merge_content"] = True
        else:
            positions[key] = len(plan)
            plan.append(entry)

    if mode == "combined" and not skip_directory_source:
        directory_entries = []
        supplemental_entries = []
        for entry in plan:
            destination = (
                directory_entries
                if windows_path_key(entry["path"]) in directory_order_keys
                else supplemental_entries
            )
            destination.append(entry)
        # 补回文件与既有目录来源一起排序；真正的列表补充保持声明顺序。
        plan = sorted(
            directory_entries, key=lambda entry: entry["path"].lower(),
        ) + supplemental_entries

    included = {
        windows_path_key(entry["path"]) for entry in plan
        if entry["merge_content"]
    }
    ignored_entries = [
        member for member in ignored_entries
        if member[1] == "directory"
        or windows_path_key(member[0]) not in included
    ]
    result = write_merge_file(
        plan, expected, preamble_text, ending_text,
        code_header_line, code_footer_line, log_func,
        normalize_text_newlines=normalize_text_newlines,
        record_file_mtime=record_file_mtime,

        output_rules=output_rules,
        match_root=source_folder if mode in ("directory", "combined") else None,
        authorize_merge_scope=authorize_merge_scope,
        enumeration_accesses=enumeration_accesses,
        select_merge_scope=select_merge_scope,
        ignored_entries=ignored_entries,
    )
    _log_completed(result, "合并", log_func)
    return result
