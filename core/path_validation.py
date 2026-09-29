# -*- coding: utf-8 -*-

import os
from dataclasses import dataclass
from pathlib import Path

from core.text_io import is_temporarily_disabled_list_line


@dataclass
class PathValidationResult:
    """
    单个路径校验结果。

    original:
        用户输入的原始文本。
    normalized:
        去引号、展开环境变量后的路径文本。
    resolved:
        最终解析出来的绝对路径。失败时为空。
    is_valid:
        是否有效。
    is_relative:
        原始路径是否是相对路径。
    was_changed:
        是否被程序自动规范化或从相对路径转换为绝对路径。
    issue:
        无效原因。
    """

    original: str
    normalized: str
    resolved: str
    is_valid: bool
    is_relative: bool = False
    was_changed: bool = False
    issue: str = ""


@dataclass
class PathListValidationResult:
    """
    多行路径名单校验结果。
    """

    normalized_text: str
    valid_paths: list
    invalid_line_numbers: list
    changed_line_numbers: list
    issue_counts: dict
    duplicate_count: int = 0


def strip_outer_quotes(value):
    value = (value or "").strip()

    while len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()

    return value


def normalize_windows_display_path(value):
    """
    Windows 路径输入框的显示层清洗。

    只做 UI 文本清洗：
    - 去外层引号；
    - 去首尾空白；
    - 将 / 替换为 \\。

    不解析绝对路径；
    不检查存在性；
    不拼接 base_dir；
    不展开为完整路径。
    """
    return strip_outer_quotes(value).replace("/", "\\")


def normalize_user_path_text(value):
    """
    只做文本层面的规范化：
    - 去外层引号；
    - 展开 ~；
    - 展开环境变量；
    - 标准化分隔符；
    - normpath。
    """
    value = strip_outer_quotes(value)

    if not value:
        return ""

    value = os.path.expandvars(os.path.expanduser(value))
    value = value.replace("\\", os.path.sep).replace("/", os.path.sep)
    value = os.path.normpath(value)

    return value


def is_windows_absolute_path(path_text):
    return len(path_text) >= 2 and path_text[1] == ":"


def is_absolute_path(path_text):
    return os.path.isabs(path_text) or is_windows_absolute_path(path_text)


def resolve_path(
    raw_path,
    base_dir=None,
    allow_relative=False,
):
    """
    解析用户路径。

    - 绝对路径：直接解析；
    - 相对路径：
      - allow_relative=False：报错；
      - allow_relative=True：必须提供 base_dir，并拼接 base_dir。
    """
    original = raw_path or ""
    normalized = normalize_user_path_text(original)

    if not normalized:
        return PathValidationResult(
            original=original,
            normalized=normalized,
            resolved="",
            is_valid=False,
            issue="路径为空",
        )

    is_relative = not is_absolute_path(normalized)

    if is_relative:
        if not allow_relative:
            return PathValidationResult(
                original=original,
                normalized=normalized,
                resolved="",
                is_valid=False,
                is_relative=True,
                issue="非绝对路径",
            )

        if not base_dir:
            return PathValidationResult(
                original=original,
                normalized=normalized,
                resolved="",
                is_valid=False,
                is_relative=True,
                issue="相对路径缺少基准文件夹",
            )

        base = Path(base_dir).expanduser().resolve()
        resolved_path = (base / normalized).resolve()
        was_changed = True

    else:
        resolved_path = Path(normalized).expanduser().resolve()
        was_changed = str(resolved_path) != normalized

    return PathValidationResult(
        original=original,
        normalized=normalized,
        resolved=str(resolved_path),
        is_valid=True,
        is_relative=is_relative,
        was_changed=was_changed,
    )


def ensure_inside_root(path, root):
    """
    校验 path 是否位于 root 内部。
    """
    target = Path(path).resolve()
    root_path = Path(root).resolve()

    try:
        target.relative_to(root_path)
    except ValueError:
        raise ValueError(f"路径逃逸目标根目录：{target}")

    return str(target)


def validate_existing_path(
    raw_path,
    label="路径",
    expected_type="any",
    base_dir=None,
    allow_relative=False,
):
    """
    校验单个已存在路径。

    expected_type:
    - any
    - file
    - dir
    """
    result = resolve_path(
        raw_path,
        base_dir=base_dir,
        allow_relative=allow_relative,
    )

    if not result.is_valid:
        return result

    path = Path(result.resolved)

    if not path.exists():
        result.is_valid = False
        result.issue = f"{label}不存在"
        return result

    if expected_type == "file" and not path.is_file():
        result.is_valid = False
        result.issue = f"{label}不是文件"
        return result

    if expected_type == "dir" and not path.is_dir():
        result.is_valid = False
        result.issue = f"{label}不是文件夹"
        return result

    return result


def validate_output_target_path(raw_path, label="输出路径"):
    """
    输出路径允许不存在，但父目录必须可创建或已存在。
    """
    result = resolve_path(raw_path, allow_relative=False)

    if not result.is_valid:
        return result

    parent = Path(result.resolved).parent

    try:
        parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        result.is_valid = False
        result.issue = f"{label}父目录不可创建：{e}"

    return result


def validate_path_list(
    list_text,
    base_dir=None,
    allow_relative=False,
    expected_type="file",
    deduplicate=True,
):
    """
    多行路径名单校验。

    用于：
    - 按需合并名单；
    - 未来其他路径批处理功能。

    规则：
    - 空行忽略；
    - 每行一个路径；
    - 可选允许相对路径；
    - 相对路径使用 base_dir 拼接；
    - 自动规范化为绝对路径；
    - 无效行记录行号；
    - 自动修改行记录为 changed_line_numbers；
    - 可去重。
    """
    normalized_lines = []
    valid_paths = []
    invalid_line_numbers = []
    changed_line_numbers = []
    issue_counts = {}
    seen = set()
    duplicate_count = 0

    for raw_line in (list_text or "").splitlines():
        if not raw_line.strip():
            continue

        if is_temporarily_disabled_list_line(raw_line):
            normalized_lines.append(raw_line.strip())
            continue

        result = validate_existing_path(
            raw_line,
            label="路径",
            expected_type=expected_type,
            base_dir=base_dir,
            allow_relative=allow_relative,
        )

        line_number = len(normalized_lines) + 1

        if result.is_valid:
            path_key = os.path.normcase(os.path.abspath(result.resolved))

            if deduplicate and path_key in seen:
                duplicate_count += 1
                continue

            seen.add(path_key)
            normalized_lines.append(result.resolved)
            valid_paths.append(result.resolved)

            if result.was_changed or result.is_relative or result.original.strip() != result.resolved:
                changed_line_numbers.append(line_number)

        else:
            normalized_lines.append(result.normalized or result.original)
            invalid_line_numbers.append(line_number)
            issue = result.issue or "路径无效"
            issue_counts[issue] = issue_counts.get(issue, 0) + 1

    if duplicate_count:
        issue_counts["重复路径已自动去重"] = duplicate_count

    return PathListValidationResult(
        normalized_text="\n".join(normalized_lines),
        valid_paths=valid_paths,
        invalid_line_numbers=invalid_line_numbers,
        changed_line_numbers=changed_line_numbers,
        issue_counts=issue_counts,
        duplicate_count=duplicate_count,
    )


def summarize_path_issues(issue_counts):
    if not issue_counts:
        return ""

    return "；".join(
        f"{name} {count} 行"
        for name, count in issue_counts.items()
    )


def normalize_rel_path(rel_path: str):
    """
    修改包执行器专用：标准化相对路径，禁止路径逃逸。

    这是修改包内相对路径安全规则的唯一入口：
    - path 必须是非空字符串；
    - 禁止绝对路径；
    - 禁止 Windows 盘符路径；
    - 禁止 .. 路径穿越；
    - 内部统一使用 / 作为协议路径分隔符。
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path 必须是非空字符串")

    rel_path = rel_path.replace("\\", "/").strip()

    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise ValueError(f"不允许绝对路径：{rel_path}")

    if len(rel_path) >= 2 and rel_path[1] == ":":
        raise ValueError(f"不允许 Windows 盘符绝对路径：{rel_path}")

    parts = Path(rel_path).parts

    if any(part == ".." for part in parts):
        raise ValueError(f"不允许路径穿越 '..'：{rel_path}")

    return rel_path


def safe_join(root: Path, rel_path: str):
    """
    修改包执行器专用：把相对路径安全拼接到项目根目录下。

    这一步是路径逃逸防护的最终保险：
    即使前置文本校验遗漏，resolve 后仍必须位于 root 内部。
    """
    rel_path = normalize_rel_path(rel_path)

    root_real = Path(root).resolve()
    target = (root_real / rel_path).resolve()

    try:
        target.relative_to(root_real)
    except ValueError:
        raise ValueError(f"目标路径逃逸项目根目录：{target}")

    return target
