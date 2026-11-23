# -*- coding: utf-8 -*-

"""按名单合并规则的唯一解析、可取消文件选择和内存去重能力。"""

import ntpath
import os
import re

from dataclasses import dataclass
from pathlib import Path

from core.rule_lists import clean_rule_quotes, is_exclusion_layout_line
from core.recovery_paths import UnsafePathError


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


def _source_directory(source_folder, cancel, traversal):
    """相对规则基准也经过同一节点闸门，后台不能先解析完整链接链。"""
    if not source_folder or not os.path.isabs(source_folder):
        raise ValueError("相对路径需要明确绝对源文件夹。")
    check_cancel(cancel)
    access = traversal.resolve(source_folder)
    if access is None:
        return None
    if access.kind != "directory":
        raise ValueError("相对规则的源基准不是普通目录。")
    return access.nominal_path


def _exact_file(rule, base, cancel, traversal):
    """明确入口和后台检查共用同一授权前停止机制。"""
    from services.source_traversal import SourceAuthorizationRequired

    if rule.segments and rule.segments[-1] == ".":
        return DemandMatch(issue="末尾分隔符不能作为明确文件选择。")
    check_cancel(cancel)
    try:
        observed = traversal.resolve(base.joinpath(*rule.segments))
    except SourceAuthorizationRequired:
        return DemandMatch(requires_authorization=True)
    except (OSError, UnsafePathError) as error:
        return DemandMatch(issue=f"无法检查指定入口：{error}")
    check_cancel(cancel)
    if observed is None:
        return DemandMatch(completed=True, disabled=True)
    if observed.kind == "directory":
        from core.link_paths import inspect_link_path, recheck_link_path

        # has_links只说明整条路线经过链接，不能代表末端目录入口类型。
        # 已授权完整路线先复核；不跟随末端的分类仍复用正式解析机制，
        # 已决定节点不再询问，也不由分类取得新的目标访问权限。
        recheck_link_path(observed)
        entry = inspect_link_path(
            observed.nominal_path, follow_final_link=False,
            authorize_link=traversal._decide,
        )
        recheck_link_path(observed)
        if entry.entry_link is None:
            return DemandMatch(issue="普通目录不能直接合并，请使用通配符。")
    return DemandMatch(
        (str(observed.nominal_path),),
        completed=True, accesses=(observed,),
    )


def _wildcard_scope(rule, base, cancel, traversal):
    """固定前缀遇到首个待授权节点立即停止，不预先探查下游。"""
    from services.source_traversal import SourceAuthorizationRequired

    first = next(
        number for number, segment in enumerate(rule.segments)
        if "*" in segment or "?" in segment
    )
    check_cancel(cancel)
    try:
        access = traversal.resolve(base.joinpath(*rule.segments[:first]))
    except SourceAuthorizationRequired:
        return DemandMatch(requires_authorization=True)
    except (OSError, UnsafePathError) as error:
        return DemandMatch(issue=f"无法定位通配符固定前缀：{error}")
    if access is None:
        return DemandMatch(completed=True, disabled=True)
    if access.kind != "directory":
        return DemandMatch(issue="通配符固定前缀不是普通目录。")
    if access.has_links and ".." in rule.segments[first:]:
        return DemandMatch(
            issue="链接通配符范围不能用后续..离开已授权子树。"
        )
    return access


def _wildcard_files(rule, scope_access, cancel, *, traversal):
    """规则自身记录本次是否遇到断点，不从去重后的报告数量推断决定。"""
    from core.link_paths import recheck_link_path
    from services.source_traversal import SourceAuthorizationRequired

    segments = rule.segments
    first = next(
        number for number, segment in enumerate(segments)
        if "*" in segment or "?" in segment
    )
    patterns = {
        number: _segment_pattern(segment)
        for number, segment in enumerate(segments)
        if segment not in ("**", ".", "..")
    }

    def closure(positions):
        result = set(positions)
        pending = list(positions)
        while pending:
            number = pending.pop()
            if (
                number < len(segments) and segments[number] in ("**", ".")
                and number + 1 not in result
            ):
                result.add(number + 1)
                pending.append(number + 1)
        return result

    files = {}
    directory_match = False
    blocked = False
    pending = [(scope_access, {first}, frozenset())]
    processed = {}
    try:
        while pending:
            check_cancel(cancel)
            access, positions, ancestors = pending.pop()
            recheck_link_path(access)
            identifier = access.object_identity
            if identifier in ancestors:
                raise ValueError(f"来源目录递归循环：{access.nominal_path}")
            reachable = closure(positions)
            key = windows_path_key(access.nominal_path)
            previous = processed.setdefault(key, set())
            fresh = reachable - previous
            previous.update(fresh)
            directory_match |= len(segments) in fresh
            active = {
                number for number in fresh
                if number < len(segments) and segments[number] not in (".", "..")
            }
            parent_positions = {
                number + 1 for number in fresh
                if number < len(segments) and segments[number] == ".."
            }
            if parent_positions:
                if access.has_links:
                    raise ValueError("链接范围不能通过..离开已授权子树")
                parent = traversal.resolve(access.actual_path.parent)
                if parent is None:
                    blocked = True
                else:
                    pending.append((parent, parent_positions, frozenset()))
            if not active:
                continue
            for nominal, attributes in traversal.children(access, skip_output=False):
                matched = {
                    number for number in active
                    if segments[number] == "**"
                    or patterns[number].fullmatch(nominal.name) is not None
                }
                if not matched:
                    continue
                child = traversal.resolve(nominal)
                if child is None:
                    blocked = True
                    continue
                if child.kind == "directory":
                    next_positions = {
                        number if segments[number] == "**" else number + 1
                        for number in matched
                    }
                    pending.append((
                        child, next_positions, ancestors | {identifier},
                    ))
                elif any(number == len(segments) - 1 for number in matched):
                    files.setdefault(windows_path_key(child.nominal_path), child)
    except SourceAuthorizationRequired:
        return DemandMatch(requires_authorization=True)
    except (FileNotFoundError, PermissionError, UnsafePathError) as error:
        return DemandMatch(issue=f"无法完整检查指定目录范围：{error}")

    ordered = tuple(sorted(
        files.values(),
        key=lambda access: (
            str(access.nominal_path).casefold(), str(access.nominal_path),
        ),
    ))
    if ordered:
        return DemandMatch(
            tuple(str(access.nominal_path) for access in ordered),
            completed=True, accesses=ordered,
            enumeration_accesses=(scope_access,),
        )
    if blocked:
        return DemandMatch(completed=True, disabled=True)
    if directory_match:
        return DemandMatch(issue="匹配结果只有文件夹，没有可合并文件。")
    return DemandMatch(issue="通配符未命中任何文件。")


def prepare_demand_inspection(jobs, source_folder, cancel, *, traversal=None):
    """后台及正式业务共用节点机制；后台没有授权能力，遇节点即返回待授权。"""
    from services.source_traversal import (
        SourceAuthorizationRequired, SourceSelectionCancelled, SourceTraversal,
    )

    if traversal is None:
        traversal = SourceTraversal(check_cancel=lambda: check_cancel(cancel))
    if not isinstance(traversal, SourceTraversal):
        raise TypeError("列表准备必须使用正式来源任务")
    prepared = []
    source_checked = False
    source_base = None
    source_result = None
    for identifier, expression in jobs:
        check_cancel(cancel)
        try:
            rule = parse_demand_rule(expression)
        except ValueError as error:
            prepared.append((identifier, None, None, DemandMatch(issue=str(error))))
            continue
        if is_exclusion_layout_line(rule.expression):
            prepared.append((
                identifier, rule, None, DemandMatch(completed=True, empty=True),
            ))
            continue
        if rule.disabled:
            prepared.append((
                identifier, rule, None, DemandMatch(completed=True, disabled=True),
            ))
            continue
        if rule.relative:
            if not source_checked:
                try:
                    source_base = _source_directory(source_folder, cancel, traversal)
                except SourceAuthorizationRequired:
                    source_result = DemandMatch(requires_authorization=True)
                except SourceSelectionCancelled:
                    raise
                except (OSError, ValueError) as error:
                    source_result = DemandMatch(issue=str(error))
                else:
                    if source_base is None:
                        source_result = DemandMatch(completed=True, disabled=True)
                source_checked = True
            if source_result is not None:
                prepared.append((identifier, rule, None, source_result))
                continue
            base = source_base
        else:
            base = Path(rule.root)
        if rule.wildcard:
            scope = _wildcard_scope(rule, base, cancel, traversal)
            if isinstance(scope, DemandMatch):
                prepared.append((identifier, rule, None, scope))
            else:
                prepared.append((identifier, rule, scope, None))
        else:
            prepared.append((
                identifier, rule, None, _exact_file(rule, base, cancel, traversal),
            ))
    check_cancel(cancel)
    return tuple(prepared)


def inspect_prepared_demand_rules(prepared, cancel, *, traversal=None):
    """只消费原固定前缀；旧枚举回调及前缀跳过参数退出正式链。"""
    from services.source_traversal import SourceTraversal

    if traversal is None:
        traversal = SourceTraversal(check_cancel=lambda: check_cancel(cancel))
    if not isinstance(traversal, SourceTraversal):
        raise TypeError("列表检查必须使用正式来源任务")
    results = {}
    for identifier, rule, scope, result in prepared:
        check_cancel(cancel)
        results[identifier] = (
            result if result is not None else
            _wildcard_files(rule, scope, cancel, traversal=traversal)
        )
    check_cancel(cancel)
    return results


def inspect_demand_rules(jobs, source_folder, cancel):
    """同一后台任务共用一次普通名称枚举；任何链接均在授权前停止。"""
    from services.source_traversal import SourceTraversal

    traversal = SourceTraversal(check_cancel=lambda: check_cancel(cancel))
    prepared = prepare_demand_inspection(
        jobs, source_folder, cancel, traversal=traversal,
    )
    return inspect_prepared_demand_rules(
        prepared, cancel, traversal=traversal,
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