# -*- coding: utf-8 -*-

import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.constants import (
    PATCH_START_PREFIX,
    PATCH_END,
    SUPPORTED_PATCH_OPS,
    PATCH_TEXT_BLOCK_STARTERS,
)
from core.paths import safe_join, normalize_rel_path
from core.text_io import read_text_auto, write_text_utf8, split_lines_keep_text
from core.time_utils import now_stamp


@dataclass
class PatchPreviewResult:
    preview_text: str
    patch: dict


@dataclass
class PatchApplyResult:
    log_text: str
    backup_root: str


def parse_bool(value, default=False):
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in ("1", "true", "yes", "y", "on"):
        return True

    if text in ("0", "false", "no", "n", "off"):
        return False

    raise ValueError(f"布尔值非法：{value}")


def parse_int(value, default=1, name="整数"):
    if value is None:
        return default

    try:
        return int(str(value).strip())
    except Exception:
        raise ValueError(f"{name} 非法：{value}")


def validate_boundary(boundary: str):
    if not isinstance(boundary, str) or not boundary:
        raise ValueError("boundary 不能为空")

    if len(boundary) < 32:
        raise ValueError("boundary 太短，至少需要 32 个字符")

    if len(boundary) > 160:
        raise ValueError("boundary 太长，建议不超过 160 个字符")

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", boundary):
        raise ValueError("boundary 只能包含字母、数字、下划线、短横线和点")

    forbidden = {
        PATCH_END,
        "---OP",
        "---CONTENT",
        "---OLD",
        "---NEW",
        "---END_OP",
    }

    if boundary in forbidden:
        raise ValueError(f"boundary 不能等于协议关键字：{boundary}")

    if not boundary.startswith("AI_PATCH_BOUNDARY_"):
        raise ValueError("boundary 必须以 AI_PATCH_BOUNDARY_ 开头")


def parse_start_line(line: str):
    pattern = r'^<<AI_FILE_PATCH_V2\s+boundary="([^"]+)">>$'
    match = re.match(pattern, line.strip())

    if not match:
        raise ValueError(
            "V2 修改包首行格式非法。应为："
            '<<AI_FILE_PATCH_V2 boundary="AI_PATCH_BOUNDARY_...">>'
        )

    boundary = match.group(1)
    validate_boundary(boundary)

    return boundary


def parse_op_line(line: str, line_no: int):
    try:
        parts = shlex.split(line, posix=True)
    except Exception as e:
        raise ValueError(f"第 {line_no} 行 OP 头解析失败：{e}")

    if len(parts) < 2 or parts[0] != "---OP":
        raise ValueError(f"第 {line_no} 行 OP 头格式非法：{line}")

    op_type = parts[1]

    if op_type not in SUPPORTED_PATCH_OPS:
        raise ValueError(f"第 {line_no} 行操作类型不支持：{op_type}")

    attrs = {
        "op": op_type,
        "_line_no": line_no,
    }

    for token in parts[2:]:
        if "=" not in token:
            raise ValueError(f"第 {line_no} 行 OP 参数非法，必须是 key=value：{token}")

        key, value = token.split("=", 1)
        key = key.strip()

        if not key:
            raise ValueError(f"第 {line_no} 行 OP 参数名不能为空：{token}")

        attrs[key] = value

    if "path" not in attrs:
        raise ValueError(f"第 {line_no} 行 OP 缺少 path 参数")

    return attrs


def join_text_block(lines):
    return "\n".join(lines)


def normalize_text_newlines(text: str):
    """
    将文本换行统一为 LF。

    说明：
    - 修改包文本框和解析器通常会把换行归一成 LF；
    - Windows 源文件可能是 CRLF；
    - replace_exact 如果直接做 text.count(old)，会因为 LF/CRLF 不同而误判命中 0 次。
    """
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def ensure_no_duplicate_block(op, key, line_no):
    if key in op:
        raise ValueError(
            f"第 {line_no} 行重复出现文本块 {key}，"
            f"当前操作开始于第 {op.get('_line_no', '?')} 行"
        )


def validate_completed_op(op, op_index):
    op_type = op["op"]

    if op_type in ("write_file", "append_text", "replace_between"):
        if "content" not in op:
            raise ValueError(f"第 {op_index} 个操作 {op_type} 缺少 ---CONTENT 文本块")

        if "old" in op or "new" in op:
            raise ValueError(f"第 {op_index} 个操作 {op_type} 不允许包含 ---OLD 或 ---NEW")

    elif op_type == "replace_exact":
        if "old" not in op:
            raise ValueError(f"第 {op_index} 个 replace_exact 缺少 ---OLD 文本块")

        if "new" not in op:
            raise ValueError(f"第 {op_index} 个 replace_exact 缺少 ---NEW 文本块")

        if "content" in op:
            raise ValueError("replace_exact 不允许包含 ---CONTENT 文本块")

    elif op_type == "delete_file":
        if "content" in op or "old" in op or "new" in op:
            raise ValueError("delete_file 不允许包含正文文本块")


def parse_patch_v2(text: str):
    """
    V2 状态机解析器。

    关键规则：
    - 包结束标记只在结构区有效；
    - ---OP / ---END_OP 只在结构区有效；
    - 正文区只识别 boundary 单独成行；
    - 正文区其他内容全部按原文保存。
    """
    lines = split_lines_keep_text(text)

    state = "WAIT_START"
    boundary = None
    operations = []
    current_op = None
    current_block_key = None
    current_block_lines = []
    seen_start = False
    seen_end = False

    for idx, raw_line in enumerate(lines, 1):
        line = raw_line

        if state == "WAIT_START":
            if not line.strip():
                continue

            if line.strip().startswith(PATCH_START_PREFIX):
                if seen_start:
                    raise ValueError(f"第 {idx} 行重复出现修改包开始标记")

                boundary = parse_start_line(line)
                seen_start = True
                state = "STRUCT"
                continue

            raise ValueError(
                f"第 {idx} 行不是 V2 修改包开始行。"
                f"首个非空行必须是 <<AI_FILE_PATCH_V2 boundary=\"...\">>"
            )

        if state == "STRUCT":
            stripped = line.strip()

            if not stripped:
                continue

            if stripped.startswith(PATCH_START_PREFIX):
                raise ValueError(f"第 {idx} 行在结构区重复出现修改包开始标记")

            if stripped == PATCH_END:
                if current_op is not None:
                    raise ValueError(
                        f"第 {idx} 行遇到包结束标记，但当前操作尚未 ---END_OP："
                        f"操作开始于第 {current_op.get('_line_no', '?')} 行"
                    )

                seen_end = True
                state = "END"
                continue

            if stripped.startswith("---OP "):
                if current_op is not None:
                    raise ValueError(
                        f"第 {idx} 行出现新 OP，但上一个 OP 尚未 ---END_OP："
                        f"上一个操作开始于第 {current_op.get('_line_no', '?')} 行"
                    )

                current_op = parse_op_line(stripped, idx)
                continue

            if stripped in PATCH_TEXT_BLOCK_STARTERS:
                if current_op is None:
                    raise ValueError(f"第 {idx} 行出现 {stripped}，但当前没有 OP")

                block_key = PATCH_TEXT_BLOCK_STARTERS[stripped]
                op_type = current_op["op"]

                if op_type == "delete_file":
                    raise ValueError(f"第 {idx} 行 delete_file 不允许包含文本块")

                if block_key == "content" and op_type not in (
                    "write_file",
                    "append_text",
                    "replace_between",
                ):
                    raise ValueError(f"第 {idx} 行 {op_type} 不允许使用 ---CONTENT")

                if block_key in ("old", "new") and op_type != "replace_exact":
                    raise ValueError(f"第 {idx} 行 {op_type} 不允许使用 {stripped}")

                ensure_no_duplicate_block(current_op, block_key, idx)

                current_block_key = block_key
                current_block_lines = []
                state = "IN_BLOCK"
                continue

            if stripped == "---END_OP":
                if current_op is None:
                    raise ValueError(f"第 {idx} 行出现 ---END_OP，但当前没有 OP")

                op_index = len(operations) + 1
                validate_completed_op(current_op, op_index)
                operations.append(current_op)
                current_op = None
                continue

            raise ValueError(
                f"第 {idx} 行结构区出现非法内容：{line}\n"
                "结构区只允许空行、---OP、---CONTENT、---OLD、---NEW、---END_OP、包结束标记。"
            )

        if state == "IN_BLOCK":
            if line == boundary:
                if current_op is None or current_block_key is None:
                    raise ValueError(f"第 {idx} 行内部状态错误：文本块无所属 OP")

                current_op[current_block_key] = join_text_block(current_block_lines)
                current_block_key = None
                current_block_lines = []
                state = "STRUCT"
                continue

            current_block_lines.append(line)
            continue

        if state == "END":
            if not line.strip():
                continue

            raise ValueError(f"第 {idx} 行包结束标记之后仍有非空内容：{line}")

    if state == "WAIT_START":
        raise ValueError("未找到 V2 修改包开始标记")

    if state == "IN_BLOCK":
        raise ValueError(
            f"文本块未用 boundary 单独成行结束。"
            f"当前文本块：{current_block_key}；"
            f"所属操作开始于第 {current_op.get('_line_no', '?') if current_op else '?'} 行；"
            f"期望 boundary：{boundary}"
        )

    if state == "STRUCT":
        if current_op is not None:
            raise ValueError(
                f"修改包结束前仍有未关闭 OP：操作开始于第 {current_op.get('_line_no', '?')} 行，"
                f"缺少 ---END_OP 或包结束标记位置错误"
            )

        if not seen_end:
            raise ValueError(f"缺少包结束标记：{PATCH_END}")

    if not seen_start:
        raise ValueError("未找到 V2 修改包开始标记")

    if not seen_end:
        raise ValueError(f"缺少包结束标记：{PATCH_END}")

    if not operations:
        raise ValueError("修改包中没有任何操作")

    return {
        "version": "2.0",
        "boundary": boundary,
        "operations": operations,
    }


class PatchExecutor:
    def __init__(self, root: Path, allow_delete=False, allow_multi_replace_exact=False):
        self.root = Path(root).resolve()
        self.allow_delete = allow_delete
        self.allow_multi_replace_exact = allow_multi_replace_exact
        self.backup_root = self.root / "99_归档" / "AI文件修改备份" / now_stamp()
        self.logs = []
        self.backed_up = {}

    def log(self, msg):
        self.logs.append(msg)

    def validate_patch(self, patch):
        if not isinstance(patch, dict):
            raise ValueError("内部修改包对象非法")

        if patch.get("version") != "2.0":
            raise ValueError("仅支持 V2 动态 boundary 修改包")

        ops = patch.get("operations")

        if not isinstance(ops, list) or not ops:
            raise ValueError("修改包 operations 必须是非空数组")

        for i, op in enumerate(ops, 1):
            self.validate_op(op, i)

    def validate_op(self, op, index):
        if not isinstance(op, dict):
            raise ValueError(f"第 {index} 个操作非法")

        op_type = op.get("op")

        if op_type not in SUPPORTED_PATCH_OPS:
            raise ValueError(f"第 {index} 个操作类型不支持：{op_type}")

        if "path" not in op:
            raise ValueError(f"第 {index} 个操作缺少 path")

        target = safe_join(self.root, op["path"])

        if op_type == "write_file":
            if "content" not in op:
                raise ValueError(f"第 {index} 个 write_file 缺少 content")

            if_exists = op.get("if_exists", "overwrite")

            if if_exists not in ("overwrite", "skip"):
                raise ValueError(f"第 {index} 个 write_file.if_exists 非法：{if_exists}")

        elif op_type == "append_text":
            if "content" not in op:
                raise ValueError(f"第 {index} 个 append_text 缺少 content")

            if not target.exists():
                raise ValueError(f"第 {index} 个 append_text 目标文件不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"第 {index} 个 append_text 目标是目录：{op['path']}")

        elif op_type == "replace_between":
            if "content" not in op:
                raise ValueError(f"第 {index} 个 replace_between 缺少 content")

            for key in ("start_marker", "end_marker"):
                if key not in op:
                    raise ValueError(f"第 {index} 个 replace_between 缺少 {key}")

            if not target.exists():
                raise ValueError(f"第 {index} 个 replace_between 目标文件不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"第 {index} 个 replace_between 目标是目录：{op['path']}")

        elif op_type == "replace_exact":
            if "old" not in op:
                raise ValueError(f"第 {index} 个 replace_exact 缺少 old")

            if "new" not in op:
                raise ValueError(f"第 {index} 个 replace_exact 缺少 new")

            if not target.exists():
                raise ValueError(f"第 {index} 个 replace_exact 目标文件不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"第 {index} 个 replace_exact 目标是目录：{op['path']}")
            
            expected_count = parse_int(op.get("count"), 1, "count")
            if expected_count > 1 and not self.allow_multi_replace_exact:
                raise ValueError(
                    f"第 {index} 个 replace_exact 要求替换 {expected_count} 处，"
                    "但当前未勾选“允许多处精确替换”。"
                )

        elif op_type == "delete_file":
            if not self.allow_delete:
                raise ValueError(f"第 {index} 个 delete_file 被拒绝：当前未勾选允许删除")

            if not target.exists():
                raise ValueError(f"第 {index} 个 delete_file 目标不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"第 {index} 个 delete_file 不允许删除目录：{op['path']}")

    def backup_file(self, target: Path):
        if not target.exists():
            return None

        target = target.resolve()

        if target in self.backed_up:
            return self.backed_up[target]

        rel = target.relative_to(self.root)
        backup_path = self.backup_root / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        self.backed_up[target] = backup_path

        self.log(f"[备份] {rel} -> {backup_path.relative_to(self.root)}")

        return backup_path

    def preview(self, patch):
        self.validate_patch(patch)

        lines = []
        lines.append("【Dry Run 预演结果】")
        lines.append("协议版本：AI_FILE_PATCH_V2 动态 boundary 原文块协议")
        lines.append(f"项目根目录：{self.root}")
        lines.append(f"boundary：{patch.get('boundary')}")
        lines.append(f"操作数量：{len(patch['operations'])}")
        lines.append("")

        for i, op in enumerate(patch["operations"], 1):
            op_type = op["op"]
            path = normalize_rel_path(op["path"])
            target = safe_join(self.root, path)

            if op_type == "write_file":
                status = "将新建" if not target.exists() else "将全量覆盖"

                if op.get("if_exists", "overwrite") == "skip" and target.exists():
                    status = "将跳过（文件已存在且 if_exists=skip）"

                lines.append(f"{i}. [write_file] {status}：{path}")

            elif op_type == "append_text":
                lines.append(f"{i}. [append_text] 将追加到文件末尾：{path}")

            elif op_type == "replace_between":
                text, enc = read_text_auto(target)
                start_marker = op["start_marker"]
                end_marker = op["end_marker"]
                s_count = text.count(start_marker)
                e_count = text.count(end_marker)

                if s_count != 1 or e_count != 1:
                    raise ValueError(
                        f"第 {i} 个 replace_between 锚点不唯一："
                        f"start_count={s_count}, end_count={e_count}, path={path}"
                    )

                if text.find(start_marker) >= text.find(end_marker):
                    raise ValueError(f"第 {i} 个 replace_between 起始锚点在结束锚点之后：{path}")

                include_markers = parse_bool(op.get("include_markers"), False)

                lines.append(f"{i}. [replace_between] 将替换锚点区间：{path}")
                lines.append(f"   start_marker: {start_marker}")
                lines.append(f"   end_marker: {end_marker}")
                lines.append(f"   include_markers: {include_markers}")

            elif op_type == "replace_exact":
                text, enc = read_text_auto(target)
                normalized_text = normalize_text_newlines(text)
                old = normalize_text_newlines(op["old"])
                expected_count = parse_int(op.get("count"), 1, "count")
                actual_count = normalized_text.count(old)

                if actual_count != expected_count:
                    raise ValueError(
                        f"第 {i} 个 replace_exact 命中次数不符："
                        f"expected={expected_count}, actual={actual_count}, path={path}"
                    )

                lines.append(f"{i}. [replace_exact] 将精确替换 {expected_count} 处：{path}")

            elif op_type == "delete_file":
                lines.append(f"{i}. [delete_file] 将删除文件：{path}")

        lines.append("")
        lines.append("Dry Run 校验通过。执行前会自动备份被修改/删除文件。")

        return "\n".join(lines)

    def apply(self, patch):
        self.validate_patch(patch)

        self.logs = []
        self.backup_root.mkdir(parents=True, exist_ok=True)

        self.log("【开始执行修改包】")
        self.log("协议版本：AI_FILE_PATCH_V2 动态 boundary 原文块协议")
        self.log(f"项目根目录：{self.root}")
        self.log(f"备份目录：{self.backup_root}")
        self.log(f"boundary：{patch.get('boundary')}")
        self.log("")

        for i, op in enumerate(patch["operations"], 1):
            op_type = op["op"]
            path = normalize_rel_path(op["path"])
            target = safe_join(self.root, path)
            rel_display = target.relative_to(self.root)

            self.log(f"---- 操作 {i}: {op_type} {rel_display} ----")

            if op_type == "write_file":
                if target.exists():
                    if op.get("if_exists", "overwrite") == "skip":
                        self.log(f"[跳过] 文件已存在且 if_exists=skip：{rel_display}")
                        continue

                    self.backup_file(target)

                write_text_utf8(target, op["content"])
                self.log(f"[完成] 写入文件：{rel_display}")

            elif op_type == "append_text":
                self.backup_file(target)
                old_text, enc = read_text_auto(target)
                new_text = old_text + op["content"]
                write_text_utf8(target, new_text)
                self.log(f"[完成] 追加文本：{rel_display}")

            elif op_type == "replace_between":
                self.backup_file(target)
                text, enc = read_text_auto(target)

                start_marker = op["start_marker"]
                end_marker = op["end_marker"]
                include_markers = parse_bool(op.get("include_markers"), False)
                content = op["content"]

                s_count = text.count(start_marker)
                e_count = text.count(end_marker)

                if s_count != 1 or e_count != 1:
                    raise ValueError(
                        f"replace_between 锚点不唯一："
                        f"start_count={s_count}, end_count={e_count}, path={path}"
                    )

                s_idx = text.find(start_marker)
                e_idx = text.find(end_marker)

                if s_idx >= e_idx:
                    raise ValueError(f"replace_between 起始锚点在结束锚点之后：{path}")

                if include_markers:
                    before = text[:s_idx]
                    after = text[e_idx + len(end_marker):]
                    new_text = before + content + after
                else:
                    before = text[:s_idx + len(start_marker)]
                    after = text[e_idx:]

                    if not before.endswith(("\n", "\r")):
                        before += "\n"

                    if content and not content.endswith(("\n", "\r")):
                        content += "\n"

                    new_text = before + content + after
                write_text_utf8(target, new_text)
                self.log(f"[完成] 替换锚点区间：{rel_display}")

            elif op_type == "replace_exact":
                self.backup_file(target)
                text, enc = read_text_auto(target)

                normalized_text = normalize_text_newlines(text)
                old = normalize_text_newlines(op["old"])
                new = normalize_text_newlines(op["new"])
                expected_count = parse_int(op.get("count"), 1, "count")
                actual_count = normalized_text.count(old)

                if actual_count != expected_count:
                    raise ValueError(
                        f"replace_exact 命中次数不符："
                        f"expected={expected_count}, actual={actual_count}, path={path}"
                    )

                new_text = normalized_text.replace(old, new, expected_count)
                write_text_utf8(target, new_text)
                self.log(f"[完成] 精确替换 {expected_count} 处：{rel_display}")

            elif op_type == "delete_file":
                if not self.allow_delete:
                    raise ValueError("删除操作被拒绝：未允许删除")

                if target.is_dir():
                    raise ValueError(f"不允许删除目录：{rel_display}")

                self.backup_file(target)
                target.unlink()
                self.log(f"[完成] 删除文件：{rel_display}")

        log_path = self.backup_root / "执行日志.txt"

        self.log("")
        self.log(f"[日志] {log_path}")

        log_path.write_text("\n".join(self.logs), encoding="utf-8")

        return "\n".join(self.logs)


def preview_patch(
    project_root,
    patch_text,
    allow_delete=False,
    allow_multi_replace_exact=False,
):
    root = Path(project_root)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"项目根目录不存在或不是目录：{project_root}")

    if not patch_text.strip():
        raise ValueError("请先粘贴 AI V2 修改包")

    patch = parse_patch_v2(patch_text)

    executor = PatchExecutor(
        root,
        allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
    )
    preview_text = executor.preview(patch)

    return PatchPreviewResult(
        preview_text=preview_text,
        patch=patch,
    )


def apply_patch(
    project_root,
    patch,
    allow_delete=False,
    allow_multi_replace_exact=False,
):
    root = Path(project_root)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"项目根目录不存在或不是目录：{project_root}")

    if patch is None:
        raise ValueError("请先执行 Dry Run，并确保校验通过")

    executor = PatchExecutor(
        root,
        allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
    )
    log_text = executor.apply(patch)

    return PatchApplyResult(
        log_text=log_text,
        backup_root=str(executor.backup_root),
    )
