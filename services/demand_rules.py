# -*- coding: utf-8 -*-

"""按名单合并规则的唯一解析、可取消文件选择和内存去重能力。"""

import ntpath
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from core.rule_lists import clean_rule_quotes, is_exclusion_layout_line
from core.recovery_paths import (
    UnsafePathError, checked_chain, entity_type_from_attributes,
    validate_component,
)


class DemandCheckCancelled(Exception):
    """取消不是文件选择失败。"""


@dataclass(frozen=True)
class ParsedDemandRule:
    expression: str
    disabled: bool
    relative: bool
    root: str
    segments: tuple[str, ...]
    wildcard: bool


@dataclass(frozen=True)
class DemandMatch:
    files: tuple[str, ...] = ()
    completed: bool = False
    disabled: bool = False
    empty: bool = False
    issue: str = ""
    requires_authorization: bool = False
    accesses: tuple = ()
    enumeration_accesses: tuple = ()


@dataclass(frozen=True)
class DemandDuplicate:
    hits: int
    duplicates: int
    additions: int
    determined: bool


@dataclass(frozen=True)
class DemandSelection:
    files: tuple[str, ...]
    duplicates: tuple[DemandDuplicate, ...]
    complete: bool
    has_errors: bool
    requires_authorization: bool = False
    accesses: tuple = ()
    enumeration_accesses: tuple = ()


_INVALID_BODY = re.compile(r'[<>:"|\x00-\x1f]')
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_DEVICE_COMPONENT = re.compile(
    r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])"
    r"(?:[ .].*)?",
    re.IGNORECASE,
)


def check_cancel(cancel):
    if cancel.is_set():
        raise DemandCheckCancelled()



def demand_physical_lines(text):
    """只识别 LF 和 CRLF；孤立 CR 留在正文接受校验。"""
    return text.replace("\r\n", "\n").split("\n")


def windows_path_key(path):
    return ntpath.normpath(os.path.abspath(os.fspath(path))).casefold()


def parse_demand_rule(raw):
    expression = clean_rule_quotes(raw)
    # 复用排除名单的精确排版定义；原表达保留，不扩大到 Unicode 空白。
    if is_exclusion_layout_line(expression):
        return ParsedDemandRule(expression, False, False, "", (), False)
    if expression.startswith("|"):
        return ParsedDemandRule(expression, True, False, "", (), False)

    body = expression.replace("/", "\\")
    if body.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("路径语法错误：不支持设备路径或扩展设备路径。")

    relative = True
    root = ""
    remaining = body
    if body.startswith("\\\\"):
        parts = body[2:].split("\\")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("UNC 路径必须包含服务器名和共享名。")
        server, share = parts[:2]
        if any(
            _INVALID_BODY.search(part) or "*" in part or "?" in part
            for part in (server, share)
        ):
            raise ValueError(
                "路径语法错误：UNC 服务器名和共享名不能包含通配符或非法字符。"
            )
        if any(not part.strip() or part in (".", "..") for part in (server, share)):
            raise ValueError("UNC 路径必须包含服务器名和共享名。")
        root = f"\\\\{server}\\{share}\\"
        remaining = "\\".join(parts[2:])
        relative = False
    elif _DRIVE_PREFIX.match(body):
        if len(body) < 3 or body[2] != "\\":
            raise ValueError(
                "含盘符的路径必须是完整绝对路径，例如 C:\\docs\\a.md。"
            )
        root = body[:3]
        remaining = body[3:]
        relative = False
    elif ":" in body:
        raise ValueError(
            "路径语法错误：只允许英文字母盘符后的冒号，"
            "盘符路径必须是完整绝对路径，例如 C:\\docs\\a.md。"
        )
    elif body.startswith("\\"):
        raise ValueError("路径语法错误：单个开头分隔符不是完整绝对路径。")

    if _INVALID_BODY.search(remaining):
        raise ValueError("路径语法错误：包含非法字符或控制字符。")

    # 用末尾 . 保留目录要求，不把 a.md\ 偷偷变成文件 a.md。
    trailing_separator = bool(remaining) and remaining.endswith("\\")
    path_body = remaining[:-1] if trailing_separator else remaining
    segments = path_body.split("\\") if path_body else []
    if any(segment == "" for segment in segments):
        raise ValueError("路径语法错误：内部连续分隔符形成空路径段。")
    if relative and ".." in segments:
        raise ValueError("相对路径不得使用 ..。")
    for segment in segments:
        if "**" in segment and segment != "**":
            raise ValueError("** 必须独立占据一个路径段。")
        if segment and not segment.strip(" "):
            raise ValueError("路径语法错误：路径段不能只有半角空格。")
        if segment not in (".", ".."):
            if segment.rstrip(" ").endswith("."):
                raise ValueError("路径语法错误：名称组件不能以句点结尾。")
            if "*" not in segment and "?" not in segment:
                if _DEVICE_COMPONENT.fullmatch(segment):
                    raise ValueError("路径语法错误：不支持 Windows 保留设备名称。")
    if trailing_separator:
        segments.append(".")

    return ParsedDemandRule(
        expression, False, relative, root, tuple(segments),
        any("*" in segment or "?" in segment for segment in segments),
    )


def rule_is_relative(raw):
    try:
        return parse_demand_rule(raw).relative
    except ValueError:
        return False


def _segment_pattern(segment):
    expression = "".join(
        ".*" if character == "*"
        else "." if character == "?"
        else re.escape(character)
        for character in segment
    )
    return re.compile(expression, re.IGNORECASE)


def _checked_stat(path, cancel):
    check_cancel(cancel)
    path = Path(path)
    # 父链先检查；末级一次不跟随查询同时取得并分类属性。
    # 不先abspath消除link/..，不重复查询末级属性。
    checked_chain(path.parent)
    check_cancel(cancel)
    # 盘符根没有末级名称，不能把合法根目录当成空文件名拒绝。
    if path.name and path.name not in (".", ".."):
        validate_component(path.name)
    attributes = os.stat(path, follow_symlinks=False)
    check_cancel(cancel)
    entity_type_from_attributes(path, attributes)
    return attributes


def _source_directory(source_folder, cancel):
    """源基准只定位元信息，保留名义路径；不枚举链接实际目录。"""
    from core.link_paths import inspect_link_path

    if not source_folder:
        raise ValueError("相对路径需要有效的源文件夹：尚未设置源文件夹。")
    if not os.path.isabs(source_folder):
        raise ValueError("相对路径需要有效的源文件夹：源文件夹必须是绝对路径。")
    check_cancel(cancel)
    try:
        access = inspect_link_path(source_folder, follow_final_link=True)
    except OSError as error:
        raise ValueError(f"无法访问源文件夹：{error}") from error
    check_cancel(cancel)
    if access.kind != "directory":
        raise ValueError("相对路径需要有效的源文件夹：基准未定位到既有普通目录。")
    return access.nominal_path


def _exact_file(rule, base, cancel):
    """明确路径只探查入口元信息；正文和实际访问授权归正式合并。"""
    from core.link_paths import inspect_link_path

    target = base.joinpath(*rule.segments)
    check_cancel(cancel)
    if rule.segments and rule.segments[-1] == ".":
        return DemandMatch(
            issue="末尾分隔符表示目录要求，不能作为明确文件／链接入口选择。"
        )
    try:
        observed = inspect_link_path(target, follow_final_link=False)
    except UnsafePathError as error:
        return DemandMatch(issue=f"指定入口路径不受支持：{error}")
    except OSError as error:
        return DemandMatch(issue=f"无法检查指定入口：{error}")
    check_cancel(cancel)
    if observed.kind == "absent":
        return DemandMatch(issue="指定入口不存在。")
    if observed.kind == "directory":
        return DemandMatch(
            issue="普通目录不能直接合并，请明确使用通配符选择文件。"
        )
    if observed.kind not in (
        "file", "file_symlink", "directory_symlink", "junction",
    ):
        return DemandMatch(issue="指定入口类型不支持。")
    return DemandMatch(
        (str(observed.nominal_path),), completed=True,
        accesses=(observed,),
    )


def _wildcard_scope(rule, base, cancel):
    """只定位固定前缀，不枚举；供整次任务集中选择实际访问范围。"""
    segments = rule.segments
    first_wildcard = next(
        index for index, segment in enumerate(segments)
        if "*" in segment or "?" in segment
    )
    # 固定前缀先定位元信息；正文和目录枚举不是路径探查的副作用。
    nominal_scope = base.joinpath(*segments[:first_wildcard])
    from core.link_paths import inspect_link_path, recheck_link_path
    from services.scan_scope import describe_scan_access

    check_cancel(cancel)
    try:
        scope_access = inspect_link_path(
            nominal_scope, follow_final_link=True,
        )
    except (OSError, ValueError) as error:
        return DemandMatch(issue=f"无法定位通配符固定前缀：{error}")
    check_cancel(cancel)
    if scope_access.kind != "directory":
        return DemandMatch(issue="通配符固定前缀未定位到既有实际普通目录。")
    if scope_access.has_links and ".." in segments[first_wildcard:]:
        return DemandMatch(
            issue="经目录链接的通配符范围不能用后续..离开已授权子树；"
            "请把所需实际范围写成另一条明确固定前缀规则。"
        )
    return scope_access


def _wildcard_files(
    rule, scope_access, cancel, *, request_enumeration=None,
    source_enumeration=None,
):
    """消费已经固定的前缀；先决定权限，再进行本规则的一次枚举。"""
    from core.link_paths import inspect_link_path, recheck_link_path
    from services.scan_scope import describe_scan_access

    segments = rule.segments
    first_wildcard = next(
        index for index, segment in enumerate(segments)
        if "*" in segment or "?" in segment
    )
    recheck_link_path(scope_access)
    if scope_access.has_links:
        description = (
            f"列表规则：{rule.expression}\n"
            + describe_scan_access(scope_access)
            + "\n\n允许本次任务枚举上述实际普通目录及其普通子树的元信息。"
            "\n实际选择仍由每条规则决定；同一固定入口的枚举权限本次只确认一次。"
            "\n目录链接后代仍为叶子，不进入途中其他链接目标。"
            "\n本阶段不读取普通文件正文、不改变来源。"
            "\n文件选取完成后仍须核对固定来源、实际别名和输出隔离。"
            "\n授权只属于本次合并，不保存到名单或用户配置。"
        )
        if request_enumeration is None:
            return DemandMatch(
                requires_authorization=True,
                enumeration_accesses=(scope_access,),
            )
        if not callable(request_enumeration):
            raise TypeError("列表实际目录枚举请求必须可调用")
        check_cancel(cancel)
        decision = request_enumeration(
            scope_access, "合并列表实际目录枚举授权", description,
        )
        if type(decision) is not bool:
            raise TypeError("列表枚举授权必须返回明确布尔值")
        if not decision:
            raise DemandCheckCancelled("本次列表实际目录枚举授权已取消")
        check_cancel(cancel)
        recheck_link_path(scope_access)
    scope = scope_access.actual_path
    patterns = {
        index: _segment_pattern(segment)
        for index, segment in enumerate(segments)
        if segment not in ("**", ".", "..")
    }
    files = {}
    matched_directory = False

    from services.source_enumeration import SourceEnumeration

    if source_enumeration is None:
        source_enumeration = SourceEnumeration()
    if not isinstance(source_enumeration, SourceEnumeration):
        raise TypeError("列表来源必须使用正式任务枚举状态")
    # 模式推进属于本规则；目录名称和懒属性属于本次来源准备，
    # 多规则及组合模式共用，不把模式状态混入共享名称快照。
    directory_attributes = {}
    processed_positions = {}

    def closure(positions):
        reachable = set(positions)
        pending = list(positions)
        while pending:
            position = pending.pop()
            if (
                position < len(segments)
                and segments[position] in ("**", ".")
                and position + 1 not in reachable
            ):
                reachable.add(position + 1)
                pending.append(position + 1)
        return reachable

    try:
        attributes = _checked_stat(scope, cancel)
        check_cancel(cancel)
        if not stat.S_ISDIR(attributes.st_mode):
            return DemandMatch(issue="通配符指定的目录范围不是文件夹。")
        directory_attributes[windows_path_key(scope)] = attributes
        stack = [(scope, {first_wildcard}, frozenset())]

        while stack:
            check_cancel(cancel)
            folder, positions, ancestors = stack.pop()
            key = windows_path_key(folder)
            attributes = directory_attributes.get(key)
            if attributes is None:
                attributes = _checked_stat(folder, cancel)
                directory_attributes[key] = attributes
            check_cancel(cancel)
            if not stat.S_ISDIR(attributes.st_mode):
                continue
            identity = (attributes.st_dev, attributes.st_ino)
            if identity in ancestors:
                return DemandMatch(
                    issue=f"无法完整检查指定目录范围：发现目录链接循环：{folder}"
                )

            reachable = closure(positions)
            previous = processed_positions.setdefault(key, set())
            fresh = reachable - previous
            previous.update(fresh)
            if len(segments) in fresh:
                matched_directory = True
            active = {
                position for position in fresh
                if position < len(segments)
                and segments[position] not in (".", "..")
            }

            parent_positions = {
                position + 1 for position in fresh
                if position < len(segments) and segments[position] == ".."
            }
            if parent_positions:
                check_cancel(cancel)
                parent = checked_chain(folder / "..")
                # 显式 .. 不是递归链接进入；状态集合负责限制重复推进。
                stack.append((parent, parent_positions, frozenset()))
            if not active:
                continue
            next_ancestors = ancestors | {identity}

            def consume_entry(name, path, entry_attributes):
                nonlocal matched_directory
                check_cancel(cancel)
                matched = {
                    position for position in active
                    if segments[position] == "**"
                    or patterns[position].fullmatch(name) is not None
                }
                if not matched:
                    return
                check_cancel(cancel)
                validate_component(name)
                from core.link_paths import _entry_kind

                entry_kind = _entry_kind(path, entry_attributes)
                is_directory = entry_kind == "directory"
                is_file = entry_kind in (
                    "file", "file_symlink", "directory_symlink", "junction",
                )
                # 三类链接仅在末级命中时选入，不递归进入目标。
                # scope已是先授权的实际普通位置，名义前缀另行保留。
                if is_directory:
                    next_positions = {
                        position if segments[position] == "**" else position + 1
                        for position in matched
                    }
                    check_cancel(cancel)
                    child = Path(path)
                    # Windows DirEntry.stat 可能不提供有效 st_ino。
                    # 循环判断需要真实目录身份，不能拿多个零身份互相比较。
                    if entry_attributes.st_ino == 0:
                        entry_attributes = _checked_stat(child, cancel)
                        if entry_attributes.st_ino == 0:
                            raise OSError(f"无法取得可靠的目录身份：{child}")
                    directory_attributes[windows_path_key(child)] = entry_attributes
                    stack.append((child, next_positions, next_ancestors))
                elif is_file and any(
                    position == len(segments) - 1 for position in matched
                ):
                    absolute = os.path.abspath(path)
                    files.setdefault(windows_path_key(absolute), absolute)

            cached = source_enumeration.entries(
                folder, check_cancel=lambda: check_cancel(cancel),
            )
            for entry_index, (name, path, entry_attributes) in enumerate(cached):
                check_cancel(cancel)
                matched = any(
                    segments[position] == "**"
                    or patterns[position].fullmatch(name) is not None
                    for position in active
                )
                if not matched:
                    continue
                validate_component(name)
                entry_attributes = source_enumeration.attributes(
                    cached, entry_index,
                )
                consume_entry(name, path, entry_attributes)

    except FileNotFoundError as error:
        return DemandMatch(
            issue=f"无法完整检查指定目录范围：路径不存在或检查期间消失：{error}"
        )
    except UnsafePathError as error:
        return DemandMatch(
            issue=f"无法完整检查指定目录范围，包含不支持对象：{error}"
        )
    except OSError as error:
        return DemandMatch(issue=f"无法完整检查指定目录范围：{error}")

    check_cancel(cancel)
    ordered = tuple(sorted(
        files.values(), key=lambda path: (path.casefold(), path),
    ))
    check_cancel(cancel)
    if ordered:
        accesses = []
        try:
            recheck_link_path(scope_access)
            for actual in ordered:
                check_cancel(cancel)
                actual_path = Path(actual)
                nominal = scope_access.nominal_path / actual_path.relative_to(scope)
                access = inspect_link_path(nominal, follow_final_link=False)
                if access.actual_path != actual_path or access.kind not in (
                    "file", "file_symlink", "directory_symlink", "junction",
                ):
                    raise ValueError("列表枚举结果偏离固定名义／实际入口")
                accesses.append(access)
            # 晚入口探查可能跨越早访问依赖变化。
            recheck_link_path(scope_access)
            for access in accesses:
                recheck_link_path(access)
        except (OSError, ValueError) as error:
            return DemandMatch(issue=f"列表固定结果观察失败：{error}")
        check_cancel(cancel)
        return DemandMatch(
            tuple(str(access.nominal_path) for access in accesses),
            completed=True, accesses=tuple(accesses),
            enumeration_accesses=(scope_access,),
        )
    recheck_link_path(scope_access)
    if matched_directory:
        return DemandMatch(issue="匹配结果只有文件夹，没有可合并文件。")
    return DemandMatch(issue="通配符未命中任何文件。")



def prepare_demand_inspection(jobs, source_folder, cancel):
    """全部规则先解析、定位固定前缀；不枚举通配符实际目录。

    明确入口只查元信息。错误仍保留为正式规则结果，
    不因后续范围选择而变成合法跳过。
    """
    prepared = []
    source_checked = False
    source_base = None
    source_issue = ""
    for identifier, expression in jobs:
        check_cancel(cancel)
        try:
            rule = parse_demand_rule(expression)
        except ValueError as error:
            prepared.append((identifier, None, None, DemandMatch(issue=str(error))))
            continue
        if is_exclusion_layout_line(rule.expression):
            prepared.append((identifier, rule, None, DemandMatch(completed=True, empty=True)))
            continue
        if rule.disabled:
            prepared.append((identifier, rule, None, DemandMatch(completed=True, disabled=True)))
            continue
        if rule.relative:
            if not source_checked:
                try:
                    source_base = _source_directory(source_folder, cancel)
                except ValueError as error:
                    source_issue = str(error)
                source_checked = True
            if source_issue:
                prepared.append((identifier, rule, None, DemandMatch(issue=source_issue)))
                continue
            base = source_base
        else:
            base = Path(rule.root)
        check_cancel(cancel)
        if rule.wildcard:
            scope = _wildcard_scope(rule, base, cancel)
            if isinstance(scope, DemandMatch):
                prepared.append((identifier, rule, None, scope))
            else:
                prepared.append((identifier, rule, scope, None))
        else:
            prepared.append((identifier, rule, None, _exact_file(rule, base, cancel)))
    check_cancel(cancel)
    return tuple(prepared)


def inspect_prepared_demand_rules(
    prepared, cancel, *, request_enumeration=None, skipped_prefixes=(),
    source_enumeration=None,
):
    """消费同次固定前缀和范围决定；未选范围不开始枚举。

    skipped_prefixes仅由所属任务的明确范围选择形成，
    不是错误规则的继续执行通道。
    """
    if request_enumeration is not None and not callable(request_enumeration):
        raise TypeError("列表枚举请求必须可调用")
    from services.source_enumeration import SourceEnumeration

    if source_enumeration is None:
        source_enumeration = SourceEnumeration()
    if not isinstance(source_enumeration, SourceEnumeration):
        raise TypeError("列表准备必须消费正式任务枚举状态")
    skipped = set(skipped_prefixes)
    results = {}
    for identifier, rule, scope, result in prepared:
        check_cancel(cancel)
        if result is not None:
            results[identifier] = result
        elif windows_path_key(scope.nominal_path) in skipped:
            results[identifier] = DemandMatch(completed=True, disabled=True)
        else:
            results[identifier] = _wildcard_files(
                rule, scope, cancel, request_enumeration=request_enumeration,
                source_enumeration=source_enumeration,
            )
    check_cancel(cancel)
    return results


def inspect_demand_rules(
    jobs, source_folder, cancel, *, request_enumeration=None,
):
    """后台无授权返回待授权事实；正式调用也消费同一准备和枚举链。"""
    return inspect_prepared_demand_rules(
        prepare_demand_inspection(jobs, source_folder, cancel),
        cancel, request_enumeration=request_enumeration,
    )


def build_demand_selection(matches):
    """规则结果、待授权、路径顺序及固定依赖均消费本次结果。"""
    selected = []
    seen = set()
    duplicates = []
    prefix_known = True
    complete = True
    has_errors = False
    requires_authorization = False
    accesses = {}
    enumeration_accesses = {}
    for match in matches:
        if match is None:
            prefix_known = False
            complete = False
            duplicates.append(DemandDuplicate(0, 0, 0, False))
            continue
        if match.empty or match.disabled:
            duplicates.append(DemandDuplicate(0, 0, 0, True))
            continue
        if match.requires_authorization:
            # 元信息检查已经得出“需要当次授权”的完整结论，
            # 不是非法规则，也不是已经形成可执行文件选择。
            requires_authorization = True
            prefix_known = False
            duplicates.append(DemandDuplicate(0, 0, 0, False))
            continue
        if not match.completed:
            has_errors |= bool(match.issue)
            complete &= bool(match.issue)
            prefix_known = False
            duplicates.append(DemandDuplicate(0, 0, 0, False))
            continue
        for access in match.accesses:
            key = windows_path_key(access.nominal_path)
            previous = accesses.setdefault(key, access)
            if previous != access:
                raise ValueError("同一列表入口具有矛盾的固定选择观察")
        for access in match.enumeration_accesses:
            key = windows_path_key(access.nominal_path)
            previous = enumeration_accesses.setdefault(key, access)
            if previous != access:
                raise ValueError("同一列表枚举前缀在本次选择期间变化")
        duplicate_count = 0
        addition_count = 0
        for path in match.files:
            key = windows_path_key(path)
            if key in seen:
                duplicate_count += 1
            else:
                seen.add(key)
                selected.append(path)
                addition_count += 1
        duplicates.append(DemandDuplicate(
            len(match.files), duplicate_count, addition_count, prefix_known,
        ))
    return DemandSelection(
        tuple(selected), tuple(duplicates), complete, has_errors,
        requires_authorization, tuple(accesses.values()),
        tuple(enumeration_accesses.values()),
    )