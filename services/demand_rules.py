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
    if not source_folder:
        raise ValueError("相对路径需要有效的源文件夹：尚未设置源文件夹。")
    if not os.path.isabs(source_folder):
        raise ValueError("相对路径需要有效的源文件夹：源文件夹必须是绝对路径。")
    try:
        attributes = _checked_stat(source_folder, cancel)
    except FileNotFoundError as error:
        raise ValueError(
            "相对路径需要有效的源文件夹：源文件夹不存在。"
        ) from error
    except OSError as error:
        raise ValueError(f"无法访问源文件夹：{error}") from error
    check_cancel(cancel)
    if not stat.S_ISDIR(attributes.st_mode):
        raise ValueError("相对路径需要有效的源文件夹：指定基准不是文件夹。")
    return Path(os.path.abspath(source_folder))


def _exact_file(rule, base, cancel):
    target = base.joinpath(*rule.segments)
    try:
        attributes = _checked_stat(target, cancel)
    except FileNotFoundError:
        return DemandMatch(issue="指定文件不存在。")
    except UnsafePathError as error:
        return DemandMatch(issue=f"指定文件路径不受支持：{error}")
    except OSError as error:
        return DemandMatch(issue=f"无法检查指定文件：{error}")
    check_cancel(cancel)
    if stat.S_ISDIR(attributes.st_mode):
        return DemandMatch(
            issue="指定路径是文件夹，请填写具体文件或显式通配符规则。"
        )
    if rule.segments and rule.segments[-1] == ".":
        return DemandMatch(
            issue="指定路径要求文件夹，不能将末尾分隔符或目录路径段当作文件选择。"
        )
    if not stat.S_ISREG(attributes.st_mode):
        return DemandMatch(issue="指定路径不是可合并的普通文件。")
    return DemandMatch((os.path.abspath(target),), completed=True)


def _wildcard_files(rule, base, cancel):
    """固定前缀直接定位；每个目录枚举器在本条规则中最多打开一次。"""
    segments = rule.segments
    first_wildcard = next(
        index for index, segment in enumerate(segments)
        if "*" in segment or "?" in segment
    )
    # 固定前缀保留原路径；检查之前不能消除链接及导航段。
    scope = base.joinpath(*segments[:first_wildcard])
    patterns = {
        index: _segment_pattern(segment)
        for index, segment in enumerate(segments)
        if segment not in ("**", ".", "..")
    }
    files = {}
    matched_directory = False

    # 多个 ** 和父目录路径段可能从不同分支到达同一目录。
    # 目录项缓存只属于本次检查，用于消费新增模式状态，不再次枚举。
    directory_entries = {}
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
                entry_kind = entity_type_from_attributes(path, entry_attributes)
                is_directory = entry_kind == "directory"
                is_file = entry_kind == "file"
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

            cached = directory_entries.get(key)
            if cached is not None:
                for entry_index, (name, path, entry_attributes) in enumerate(cached):
                    check_cancel(cancel)
                    # 首次枚举时未匹配的条目没有查询属性。
                    # 新模式状态再次到达时，只有实际命中才补查一次，
                    # 并归入本次目录缓存；不能把 None 当成有效属性。
                    if entry_attributes is None:
                        matched = any(
                            segments[position] == "**"
                            or patterns[position].fullmatch(name) is not None
                            for position in active
                        )
                        if not matched:
                            continue
                        entry_attributes = _checked_stat(path, cancel)
                        cached[entry_index] = (name, path, entry_attributes)
                    consume_entry(name, path, entry_attributes)
                continue

            cached = []
            check_cancel(cancel)
            with os.scandir(folder) as entries:
                while True:
                    check_cancel(cancel)
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    check_cancel(cancel)
                    # 缓存当前枚举的名称；不预先整体读取目录再处理。
                    # 未匹配项不查询属性，避免无关文件的属性错误。
                    matched = any(
                        segments[position] == "**"
                        or patterns[position].fullmatch(entry.name) is not None
                        for position in active
                    )
                    entry_attributes = None
                    if matched:
                        check_cancel(cancel)
                        entry_attributes = entry.stat(follow_symlinks=False)
                        check_cancel(cancel)
                    cached.append((entry.name, entry.path, entry_attributes))
                    if entry_attributes is not None:
                        consume_entry(entry.name, entry.path, entry_attributes)
            directory_entries[key] = cached

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
        return DemandMatch(ordered, completed=True)
    if matched_directory:
        return DemandMatch(issue="匹配结果只有文件夹，没有可合并文件。")
    return DemandMatch(issue="通配符未命中任何文件。")



def inspect_demand_rules(jobs, source_folder, cancel):
    """规则解析一次，源基准每任务检查一次；取消不交付部分结果。"""
    results = {}
    source_checked = False
    source_base = None
    source_issue = ""
    for identifier, expression in jobs:
        check_cancel(cancel)
        try:
            rule = parse_demand_rule(expression)
        except ValueError as error:
            results[identifier] = DemandMatch(issue=str(error))
            continue
        if is_exclusion_layout_line(rule.expression):
            results[identifier] = DemandMatch(completed=True, empty=True)
            continue
        if rule.disabled:
            results[identifier] = DemandMatch(completed=True, disabled=True)
            continue
        if rule.relative:
            if not source_checked:
                try:
                    source_base = _source_directory(source_folder, cancel)
                except ValueError as error:
                    source_issue = str(error)
                source_checked = True
            if source_issue:
                results[identifier] = DemandMatch(issue=source_issue)
                continue
            base = source_base
        else:
            base = Path(rule.root)
        check_cancel(cancel)
        results[identifier] = (
            _wildcard_files(rule, base, cancel)
            if rule.wildcard else _exact_file(rule, base, cancel)
        )
    check_cancel(cancel)
    return results


def build_demand_selection(matches):
    """完成检查与检查成功是不同事实；仅在内存中计算重复关系。"""
    selected = []
    seen = set()
    duplicates = []
    prefix_known = True
    complete = True
    has_errors = False
    for match in matches:
        if match is None:
            prefix_known = False
            complete = False
            duplicates.append(DemandDuplicate(0, 0, 0, False))
            continue
        if match.empty or match.disabled:
            duplicates.append(DemandDuplicate(0, 0, 0, True))
            continue
        if not match.completed:
            has_errors |= bool(match.issue)
            complete &= bool(match.issue)
            prefix_known = False
            duplicates.append(DemandDuplicate(0, 0, 0, False))
            continue
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
    )