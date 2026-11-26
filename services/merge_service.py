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
    MTIME_FORMAT, MTIME_UNAVAILABLE, STRUCTURAL_NEWLINE,
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
    link_counts: tuple = ()

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
    traversal, root_access=None,
):
    """授权节点按实体业务展开；排除判断先于链接定义及目标访问。"""
    from core.exclusion_rules import PreparedExclusions

    if not isinstance(exclusions, PreparedExclusions):
        raise TypeError("目录合并必须消费正式排除名单")
    root = root_access if root_access is not None else traversal.resolve(source_folder)
    if root is None:
        return []
    if root.kind != "directory":
        raise ValueError("目录来源必须定位到普通目录")
    return sorted(
        (
            {"path": str(access.nominal_path),
             "merge_content": True, "access": access}
            for access, parts in traversal.walk(root, exclusions)
            if access.kind == "file"
        ),
        key=lambda entry: entry["path"].lower(),
    )


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
            label = "文件夹" if kind == "directory" else "文件"
            explanation = ""
            if kind == "directory":
                successful = {
                    identity(member) for member in successful_paths
                    if identity(member) != identity(path) and contains(path, member)
                }
                if successful:
                    explanation = (
                        f"；此处仅表示该文件夹来源被忽略，另有 {len(successful)} "
                        "项通过其他明确来源成功输出，见正文；并非整个文件夹的内容全部未输出"
                    )
            lines.append(
                f"- [{label}] {path}；{'；'.join(reasons)}{explanation}"
            )
    return normalize_newlines("\n".join(lines) + "\n", STRUCTURAL_NEWLINE)


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

    output_rules=None, match_root=None,
    enumeration_accesses=(), ignored_entries=(), show_actual_path=True,
):
    """消费已形成来源清单及固定输出，完整生成后才发布。"""
    if not isinstance(expected_output, FileObservation):
        raise TypeError("合并容器必须消费固定输出FileObservation")
    validate_markers(code_header_line, code_footer_line)

    for name, value in (
        ("标准化换行", normalize_text_newlines),
        ("记录修改时间", record_file_mtime),
        ("显示实际路径", show_actual_path),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name}开关必须是布尔值")
    if normalize_text_newlines:
        validate_file_rules(output_rules)

    file_plan = tuple(file_plan)
    output_key = windows_path_key(expected_output.path)
    if any(windows_path_key(entry["path"]) == output_key for entry in file_plan):
        raise ValueError("输出文件被实际选中，不能执行按名单合并。")
    from core.link_paths import inspect_link_path, recheck_link_path
    from core.recovery_paths import identity

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
            if access.has_links:
                raise ValueError("链接来源必须先完成逐节点授权及实体定位")
        recheck_link_path(access)
        if access.kind != "file" or access.entry_link is not None:
            raise ValueError("合并正文块只接受已授权定位的普通文件")
        if identity(access.actual_path) == identity(expected_output.path):
            raise ValueError("合并实际来源与输出位置相同")

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
        actual_key = ("file_object", access.object_identity)
        previous_index = actual_positions.get(actual_key)
        if previous_index is not None:
            previous_entry, previous_access = prepared[previous_index]
            if previous_access.kind != access.kind:
                raise ValueError("同一实际来源的入口类型矛盾")
            if (
                normalize_text_newlines and access.kind == "file"
                and entry["merge_content"] and previous_entry["merge_content"]
            ):
                def newline_for(member, observed):
                    nominal = observed.nominal_path
                    member_root = member.get("match_root", root)
                    within_root = (
                        member_root is not None and contains(member_root, nominal)
                    )
                    parts = (
                        nominal.relative_to(member_root).parts
                        if within_root else nominal.parts[1:]
                    )
                    return output_rules.matches(parts, ignore_root=not within_root)

                if newline_for(entry, access) != newline_for(previous_entry, previous_access):
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
    definitions = {}
    for access in (*enumeration_accesses, *source_accesses):
        for definition in access.followed_links:
            key = identity(definition.entry_path)
            previous = definitions.setdefault(key, definition)
            if previous != definition:
                raise ValueError("合并固定来源具有矛盾的链接节点定义")
    link_counts = {
        kind: sum(member.kind == kind for member in definitions.values())
        for kind in ("file_symlink", "directory_symlink", "junction")
    }
    ignored_entries = list(ignored_entries)
    failure_entries = []
    omitted_content_targets = []
    successful_paths = []
    # 每个链接节点已经在首次跟随前决定；不再按目录将后代归组重选，
    # 也不对同一范围增加只读确认。普通来源沿原业务自动保留。
    for _, access in prepared:
        recheck_link_path(access)
    file_plan = tuple(prepared)
    # 正文块只统计文件；链接节点另由固定访问路线统计。
    entry_counts = {"file": 0}
    total = read_failure_count = 0
    excluded_count = len(ignored_entries)
    mtime_failures = []

    def collect_blocks(container):
        nonlocal total, excluded_count, read_failure_count
        for entry, access in file_plan:
            source_path = entry["path"]
            recheck_link_path(access)

            actual_source = access.actual_path
            display_path = os.path.abspath(source_path)
            if any(ord(character) < 32 for character in display_path):
                raise ValueError(f"合并来源路径包含控制字符：{display_path!r}")
            reasons = []
            if not entry["merge_content"]:
                ignored_entries.append((
                    display_path, "file", "文件排除名单",
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
                # 该项没有进入成果，不用其原末级身份阻断遗漏报告发布。
                # 原父目录和链接路线仍由共同来源复核保留；
                # 安全拒绝、资源故障及成功来源漂移不进入此遗漏通道。
                omitted_content_targets.append(access.actual_path)
                failure_entries.append((
                    display_path, "file",
                    f"{type(error).__name__}：{error}",
                ))
                if log_func:
                    log_func(f"合并来源失败，正文未输出：{display_path}；{error}")
                continue

            if normalize_text_newlines:
                member_root = entry.get("match_root", root)
                within_root = (
                    member_root is not None and contains(member_root, display_path)
                )
                parts = (
                    Path(display_path).relative_to(member_root).parts
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
                origin_path=(
                    str(access.actual_path)
                    if show_actual_path and access.has_links else None
                ),
                mtime=(
                    modified_at if modified_at is not None else MTIME_UNAVAILABLE
                ) if record_file_mtime else None,
            )

    def recheck_sources():
        from core.file_observation import recheck_file

        from services.source_recheck import recheck_source_paths

        recheck_source_paths(
            (*enumeration_accesses, *source_accesses),
            omitted_targets=tuple(omitted_content_targets),
        )
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

                f"已授权文件软链接节点：{link_counts['file_symlink']} 个",
                f"已授权文件夹软链接节点：{link_counts['directory_symlink']} 个",
                f"已授权文件夹联接（Junction）节点：{link_counts['junction']} 个",
                f"成功输出入口块合计：{total} 个",
                f"主动忽略记录：{excluded_count} 条",
                f"正文读取／解码失败：{read_failure_count} 个",
                f"文件修改时间缺失：{len(mtime_failures)} 个",
                "仅输出文件正文块，不单列文件夹；实际路径说明按显示开关输出，拆分不用于定位。",
                "主动忽略记录可能代表整个文件夹，不等于忽略文件总数。",
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
        tuple(link_counts.items()),
    )


def _log_completed(result, mode, log_func):
    if log_func is None:
        return
    try:
        counts = dict(result.entry_counts)
        log_func(
            f"{mode}完成：成功正文文件 {counts.get('file', 0)} 个，"
            f"已授权链接节点 {sum(dict(result.link_counts).values())} 个；"
            f"合计 {result.total_files} 个文件正文块"
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


def _select_merge_rules(text, base, cancel, traversal, object_kind):
    """同次来源任务解释规则；目录与文件只在所选实体类别上不同。"""
    jobs = tuple(enumerate(demand_physical_lines(text), 1))
    prepared = prepare_demand_inspection(
        jobs, base, cancel, traversal=traversal, object_kind=object_kind,
    )
    matches = inspect_prepared_demand_rules(
        prepared, cancel, traversal=traversal, object_kind=object_kind,
    )
    selection = build_demand_selection(
        tuple(matches[number] for number, _ in jobs)
    )
    if (
        not selection.complete or selection.has_errors
        or selection.requires_authorization
    ):
        name = "文件夹列表" if object_kind == "directory" else "文件列表"
        reasons = [
            f"第 {number} 行：{matches[number].issue}"
            for number, _ in jobs if matches[number].issue
        ]
        raise ValueError(
            f"{name}未形成完整授权来源。\n" + "\n".join(reasons)
        )
    return selection


def merge_files(
    mode, relative_base_folder, directory_list_text, list_text,
    output_file, exclusions,
    preamble_text, ending_text, code_header_line, code_footer_line,
    log_func=None, *, normalize_text_newlines=False, record_file_mtime=True,
    output_rules=None, select_source_links=None, authorize_merge_scope=None,
    show_actual_path=True,
):
    """全部源目录共用一次节点决定、名称枚举及成果发布。

    每个目录独立保留根限定语义；规则顺序决定目录顺序，
    每个目录内部沿用原排序，文件列表在目录来源之后补充。
    相对基准只解释相对规则，不自动成为合并来源。
    """
    from core.exclusion_rules import PreparedExclusions
    from services.source_traversal import SourceTraversal

    if type(mode) is not str or mode not in MERGE_MODES:
        raise ValueError("合并模式必须是 directory、list 或 combined")
    for name, value in (
        ("相对路径基准", relative_base_folder),
        ("文件夹列表", directory_list_text),
        ("文件列表", list_text),
    ):
        if type(value) is not str:
            raise TypeError(f"{name}必须是字符串")
    if type(show_actual_path) is not bool:
        raise TypeError("显示实际路径必须是布尔值")
    if mode in ("directory", "combined") and not isinstance(
        exclusions, PreparedExclusions,
    ):
        raise TypeError("目录合并必须消费正式排除名单")
    validate_markers(code_header_line, code_footer_line)
    if normalize_text_newlines:
        validate_file_rules(output_rules)

    expected = observe_file(output_file, include_digest=True)
    cancel = Event()
    traversal = SourceTraversal(
        output=expected, selector=select_source_links,
        authorize=authorize_merge_scope,
    )
    roots = ()
    plans = []
    directory_owners = {}
    if mode in ("directory", "combined"):
        ignored_start = len(traversal.ignored)
        directories = _select_merge_rules(
            directory_list_text, relative_base_folder,
            cancel, traversal, "directory",
        )
        roots = directories.accesses
        by_nominal = {
            windows_path_key(access.nominal_path): access for access in roots
        }
        roots = tuple(by_nominal[windows_path_key(path)] for path in directories.files)
        if not roots and len(traversal.ignored) == ignored_start:
            raise ValueError("文件夹列表至少需要一条实际选中文件夹的启用规则")
        for root in roots:
            before = len(traversal.ignored)
            current = build_regular_merge_plan(
                root.nominal_path, exclusions, expected.path,
                traversal=traversal, root_access=root,
            )
            current = [
                {**entry, "match_root": root.nominal_path}
                for entry in current
            ]
            plans.append(current)
            for entry in current:
                directory_owners.setdefault(
                    windows_path_key(entry["path"]), root.nominal_path,
                )
            for path, kind, reason in traversal.ignored[before:]:
                if kind == "file" and reason == "文件排除名单":
                    directory_owners.setdefault(
                        windows_path_key(path), root.nominal_path,
                    )

    plan = [entry for current in plans for entry in current]
    positions = {}
    for number, entry in enumerate(plan):
        positions.setdefault(windows_path_key(entry["path"]), number)

    if mode in ("list", "combined"):
        ignored_start = len(traversal.ignored)
        selection = _select_merge_rules(
            list_text, relative_base_folder, cancel, traversal, "file",
        )
        if (
            mode == "list" and not selection.files
            and len(traversal.ignored) == ignored_start
        ):
            raise ValueError("文件列表至少需要一条实际选中文件的启用规则")
        for entry in build_demand_merge_plan(selection.files, selection.accesses):
            access = entry["access"]
            expanded = (
                (
                    {"path": str(child.nominal_path),
                     "merge_content": True, "access": child}
                    for child, _ in traversal.walk(
                        access, include_root=True, skip_output=False,
                    )
                    if child.kind == "file"
                )
                if access.kind == "directory" else (entry,)
            )
            for member in expanded:
                key = windows_path_key(member["path"])
                member = {
                    **member, "match_root": directory_owners.get(key),
                }
                previous = positions.get(key)
                if previous is None:
                    positions[key] = len(plan)
                    plan.append(member)
                elif plan[previous]["access"] != member["access"]:
                    raise ValueError("文件夹列表与文件列表的同名来源观察不一致")

    if mode == "combined":
        # 明确补回的目录文件排除项归入首次对应目录的原排序区；
        # 排除目录内部及其他外部补充仍保持文件列表顺序。
        ordered = []
        for root in roots:
            ordered.extend(sorted(
                (
                    entry for entry in plan
                    if entry.get("match_root") == root.nominal_path
                ),
                key=lambda entry: entry["path"].lower(),
            ))
        ordered.extend(
            entry for entry in plan if entry.get("match_root") is None
        )
        plan = ordered

    # 全部使用中的列表和目录元信息已经准备完毕。
    # 集中选择只裁剪原计划；不重新枚举或读取未选正文。
    from core.recovery_paths import identity

    retained = traversal.finalize()
    plan = [
        entry for entry in plan
        if identity(entry["access"].nominal_path) in retained
    ]
    included = {windows_path_key(entry["path"]) for entry in plan}
    ignored = tuple(
        member for member in traversal.ignored
        if member[1] == "directory"
        or windows_path_key(member[0]) not in included
    )
    if log_func:
        log_func(
            f"开始合并：{mode}；源文件夹 {len(roots)} 个；输出路径：{expected.path}"
        )
    dependencies = tuple(
        access for access in traversal.accesses if access.kind == "directory"
    )
    result = write_merge_file(
        plan, expected, preamble_text, ending_text,
        code_header_line, code_footer_line, log_func,
        normalize_text_newlines=normalize_text_newlines,
        record_file_mtime=record_file_mtime, output_rules=output_rules,
        enumeration_accesses=dependencies, ignored_entries=ignored,
        show_actual_path=show_actual_path,
    )
    _log_completed(result, "合并", log_func)
    return result
