# -*- coding: utf-8 -*-

import hashlib
import json
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
from core.path_validation import resolve_path
from core.paths import safe_join, normalize_rel_path
from core.text_io import read_text_auto, write_text_utf8, split_lines_keep_text
from core.time_utils import now_stamp


@dataclass
class PatchPreviewResult:
    preview_text: str
    patch: dict
    valid_patch: dict
    has_errors: bool
    success_count: int
    failed_count: int


@dataclass
class PatchApplyResult:
    log_text: str
    backup_root: str


@dataclass
class PatchOpCheckResult:
    index: int
    op_type: str
    path: str
    ok: bool
    message: str
    locator: str
    old_first_line: str = ""


@dataclass
class BackupRestorePreviewResult:
    preview_text: str
    manifest: dict
    has_mismatch: bool


@dataclass
class BackupRestoreApplyResult:
    log_text: str
    backup_root: str


def file_sha256(path: Path):
    path = Path(path)

    if not path.exists() or not path.is_file():
        return ""

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def resolve_backup_base_dir(project_root, backup_dir):
    result = resolve_path(
        backup_dir,
        base_dir=project_root,
        allow_relative=True,
    )

    if not result.is_valid:
        raise ValueError(f"备份目录无效：{result.issue}")

    return Path(result.resolved)


def make_backup_root(project_root, backup_dir, action_name=""):
    stamp = now_stamp()
    safe_action_name = str(action_name or "").strip().replace("/", "_").replace("\\", "_")

    if safe_action_name:
        folder_name = f"{stamp}_{safe_action_name}"
    else:
        folder_name = stamp

    return resolve_backup_base_dir(project_root, backup_dir) / folder_name


def load_manifest(backup_root):
    manifest_path = Path(backup_root) / "manifest.json"

    if not manifest_path.is_file():
        raise ValueError(f"备份目录中未找到 manifest.json：{manifest_path}")

    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"manifest.json 读取失败：{e}")


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def first_non_empty_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line
    return ""


def make_op_locator(op):
    op_type = op.get("op", "")
    path = op.get("path", "")
    count = op.get("count")

    if count is not None:
        return f'---OP {op_type} path="{path}" count="{count}"'

    return f'---OP {op_type} path="{path}"'


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
    def __init__(
        self,
        root: Path,
        allow_delete=False,
        allow_multi_replace_exact=False,
        backup_enabled=True,
        backup_dir="99_归档/AI文件修改备份",
        patch_text="",
        preview_text="",
    ):
        self.root = Path(root).resolve()
        self.allow_delete = allow_delete
        self.allow_multi_replace_exact = allow_multi_replace_exact
        self.backup_enabled = backup_enabled
        self.backup_root = (
            make_backup_root(self.root, backup_dir, "执行修改")
            if backup_enabled
            else None
        )
        self.patch_text = patch_text
        self.preview_text = preview_text
        self.logs = []
        self.backed_up = {}
        self.manifest_operations = []

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
        if not self.backup_enabled:
            return None

        if not target.exists():
            return None

        target = target.resolve()

        if target in self.backed_up:
            return self.backed_up[target]

        rel = target.relative_to(self.root)
        backup_path = self.backup_root / "files" / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        self.backed_up[target] = backup_path

        self.log(f"[备份] {rel} -> {backup_path}")

        return backup_path

    def record_manifest_operation(
        self,
        index,
        op_type,
        rel_path,
        existed_before,
        existed_after,
        before_sha256,
        after_sha256,
        backup_path=None,
    ):
        backup_file = ""

        if backup_path:
            backup_file = str(Path(backup_path).relative_to(self.backup_root)).replace("\\", "/")

        self.manifest_operations.append({
            "index": index,
            "op": op_type,
            "path": str(rel_path).replace("\\", "/"),
            "existed_before": existed_before,
            "existed_after": existed_after,
            "before_sha256": before_sha256,
            "after_sha256": after_sha256,
            "backup_file": backup_file,
        })

    def write_patch_copy_and_manifest(self, patch):
        if not self.backup_enabled:
            return

        patch_path = self.backup_root / "patch" / "修改包原文.txt"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(self.patch_text or "", encoding="utf-8")

        manifest = {
            "version": "1.0",
            "mode": "forward",
            "project_root": str(self.root),
            "backup_root": str(self.backup_root),
            "patch_file": "patch/修改包原文.txt",
            "patch_boundary": patch.get("boundary"),
            "operations": self.manifest_operations,
        }

        save_json(self.backup_root / "manifest.json", manifest)

    def check_one_op(self, op, index):
        op_type = op.get("op", "")
        path = op.get("path", "")

        try:
            self.validate_op(op, index)

            target_path = normalize_rel_path(path)
            target = safe_join(self.root, target_path)

            if op_type == "write_file":
                return PatchOpCheckResult(
                    index=index,
                    op_type=op_type,
                    path=target_path,
                    ok=True,
                    message="校验通过",
                    locator=make_op_locator(op),
                )

            if op_type == "append_text":
                return PatchOpCheckResult(
                    index=index,
                    op_type=op_type,
                    path=target_path,
                    ok=True,
                    message="校验通过",
                    locator=make_op_locator(op),
                )

            if op_type == "replace_between":
                text, enc = read_text_auto(target)
                start_marker = op["start_marker"]
                end_marker = op["end_marker"]
                s_count = text.count(start_marker)
                e_count = text.count(end_marker)

                if s_count != 1 or e_count != 1:
                    raise ValueError(
                        f"replace_between 锚点不唯一：start_count={s_count}, end_count={e_count}"
                    )

                if text.find(start_marker) >= text.find(end_marker):
                    raise ValueError("replace_between 起始锚点在结束锚点之后")

                return PatchOpCheckResult(
                    index=index,
                    op_type=op_type,
                    path=target_path,
                    ok=True,
                    message="校验通过",
                    locator=make_op_locator(op),
                )

            if op_type == "replace_exact":
                text, enc = read_text_auto(target)
                normalized_text = normalize_text_newlines(text)
                old = normalize_text_newlines(op["old"])
                expected_count = parse_int(op.get("count"), 1, "count")
                actual_count = normalized_text.count(old)

                if actual_count != expected_count:
                    raise ValueError(
                        f"replace_exact 命中次数不符：expected={expected_count}, actual={actual_count}"
                    )

                return PatchOpCheckResult(
                    index=index,
                    op_type=op_type,
                    path=target_path,
                    ok=True,
                    message="校验通过",
                    locator=make_op_locator(op),
                    old_first_line=first_non_empty_line(op.get("old", "")),
                )

            if op_type == "delete_file":
                return PatchOpCheckResult(
                    index=index,
                    op_type=op_type,
                    path=target_path,
                    ok=True,
                    message="校验通过",
                    locator=make_op_locator(op),
                )

            raise ValueError(f"未知操作类型：{op_type}")

        except Exception as e:
            return PatchOpCheckResult(
                index=index,
                op_type=op_type,
                path=path,
                ok=False,
                message=str(e),
                locator=make_op_locator(op),
                old_first_line=first_non_empty_line(op.get("old", "")),
            )

    def preview(self, patch):
        if not isinstance(patch, dict):
            raise ValueError("内部修改包对象非法")

        if patch.get("version") != "2.0":
            raise ValueError("仅支持 V2 动态 boundary 修改包")

        ops = patch.get("operations")

        if not isinstance(ops, list) or not ops:
            raise ValueError("修改包 operations 必须是非空数组")

        check_results = []
        valid_ops = []

        for i, op in enumerate(ops, 1):
            result = self.check_one_op(op, i)
            check_results.append(result)

            if result.ok:
                valid_ops.append(op)

        success_count = len(valid_ops)
        failed_count = len(ops) - success_count

        valid_patch = dict(patch)
        valid_patch["operations"] = valid_ops

        lines = []
        lines.append("【Dry Run 预演结果】")
        lines.append("协议版本：AI_FILE_PATCH_V2 动态 boundary 原文块协议")
        lines.append(f"项目根目录：{self.root}")
        lines.append(f"boundary：{patch.get('boundary')}")
        lines.append(f"操作数量：{len(ops)}")
        lines.append(f"校验成功：{success_count}")
        lines.append(f"校验失败：{failed_count}")

        if self.backup_enabled:
            lines.append(f"备份目录：{self.backup_root}")
        else:
            lines.append("备份状态：未启用自动备份")

        lines.append("")
        lines.append("=" * 60)
        lines.append("【校验成功】")
        lines.append("=" * 60)

        for item in check_results:
            if item.ok:
                lines.append(f"{item.index}. [通过] {item.op_type} {item.path}")

        lines.append("")
        lines.append("=" * 60)
        lines.append("【校验失败】")
        lines.append("=" * 60)

        if failed_count == 0:
            lines.append("无")
        else:
            for item in check_results:
                if item.ok:
                    continue

                lines.append("")
                lines.append(f"{item.index}. [失败] {item.op_type} {item.path}")
                lines.append("")
                lines.append("失败原因：")
                lines.append(item.message)
                lines.append("")
                lines.append("修改包定位：")
                lines.append("请在修改包中搜索以下 OP 头：")
                lines.append(item.locator)

                if item.old_first_line:
                    lines.append("")
                    lines.append("或搜索 OLD 片段首个非空行：")
                    lines.append(item.old_first_line)

                lines.append("-" * 60)

        lines.append("")

        if failed_count == 0:
            lines.append("Dry Run 全部校验通过。可以执行完整修改包。")
        elif success_count > 0:
            lines.append("Dry Run 存在失败项。执行时将只允许执行校验成功的 OP，失败 OP 会被跳过。")
        else:
            lines.append("Dry Run 全部失败。没有可执行的 OP。")

        return "\n".join(lines), valid_patch, failed_count > 0, success_count, failed_count

    def apply(self, patch):
        self.validate_patch(patch)

        self.logs = []
        self.manifest_operations = []

        if self.backup_enabled:
            self.backup_root.mkdir(parents=True, exist_ok=True)

        self.log("【开始执行修改包】")
        self.log("协议版本：AI_FILE_PATCH_V2 动态 boundary 原文块协议")
        self.log(f"项目根目录：{self.root}")

        if self.backup_enabled:
            self.log(f"备份目录：{self.backup_root}")
        else:
            self.log("备份状态：未启用自动备份")

        self.log(f"boundary：{patch.get('boundary')}")
        self.log("")

        for i, op in enumerate(patch["operations"], 1):
            op_type = op["op"]
            path = normalize_rel_path(op["path"])
            target = safe_join(self.root, path)
            rel_display = target.relative_to(self.root)

            self.log(f"---- 操作 {i}: {op_type} {rel_display} ----")
            existed_before = target.exists()
            before_hash = file_sha256(target)
            backup_path = None

            if op_type == "write_file":
                if target.exists():
                    if op.get("if_exists", "overwrite") == "skip":
                        self.log(f"[跳过] 文件已存在且 if_exists=skip：{rel_display}")
                        continue

                    backup_path = self.backup_file(target)

                write_text_utf8(target, op["content"])
                self.log(f"[完成] 写入文件：{rel_display}")
                self.record_manifest_operation(
                    i,
                    op_type,
                    rel_display,
                    existed_before,
                    target.exists(),
                    before_hash,
                    file_sha256(target),
                    backup_path,
                )

            elif op_type == "append_text":
                backup_path = self.backup_file(target)
                old_text, enc = read_text_auto(target)
                new_text = old_text + op["content"]
                write_text_utf8(target, new_text)
                self.log(f"[完成] 追加文本：{rel_display}")
                self.record_manifest_operation(
                    i,
                    op_type,
                    rel_display,
                    existed_before,
                    target.exists(),
                    before_hash,
                    file_sha256(target),
                    backup_path,
                )

            elif op_type == "replace_between":
                backup_path = self.backup_file(target)
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
                self.record_manifest_operation(
                    i,
                    op_type,
                    rel_display,
                    existed_before,
                    target.exists(),
                    before_hash,
                    file_sha256(target),
                    backup_path,
                )

            elif op_type == "replace_exact":
                backup_path = self.backup_file(target)
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
                self.record_manifest_operation(
                    i,
                    op_type,
                    rel_display,
                    existed_before,
                    target.exists(),
                    before_hash,
                    file_sha256(target),
                    backup_path,
                )

            elif op_type == "delete_file":
                if not self.allow_delete:
                    raise ValueError("删除操作被拒绝：未允许删除")

                if target.is_dir():
                    raise ValueError(f"不允许删除目录：{rel_display}")

                backup_path = self.backup_file(target)
                target.unlink()
                self.log(f"[完成] 删除文件：{rel_display}")
                self.record_manifest_operation(
                    i,
                    op_type,
                    rel_display,
                    existed_before,
                    target.exists(),
                    before_hash,
                    file_sha256(target),
                    backup_path,
                )

        self.write_patch_copy_and_manifest(patch)

        self.log("")

        if self.backup_enabled:
            log_path = self.backup_root / "执行日志.txt"
            self.log(f"[日志] {log_path}")

            file_log_lines = []

            if self.preview_text:
                file_log_lines.extend([
                    "【Dry Run 预演记录】",
                    self.preview_text,
                    "",
                    "=" * 60,
                    "【执行记录】",
                    "=" * 60,
                    "",
                ])

            file_log_lines.extend(self.logs)
            log_path.write_text("\n".join(file_log_lines), encoding="utf-8")
        else:
            self.log("[日志] 未启用自动备份，未写入备份目录日志文件。")

        return "\n".join(self.logs)


def preview_patch(
    project_root,
    patch_text,
    allow_delete=False,
    allow_multi_replace_exact=False,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
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
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        patch_text=patch_text,
    )

    preview_text, valid_patch, has_errors, success_count, failed_count = executor.preview(patch)

    return PatchPreviewResult(
        preview_text=preview_text,
        patch=patch,
        valid_patch=valid_patch,
        has_errors=has_errors,
        success_count=success_count,
        failed_count=failed_count,
    )


def apply_patch(
    project_root,
    patch,
    allow_delete=False,
    allow_multi_replace_exact=False,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
    patch_text="",
    preview_text="",
):
    root = Path(project_root)

    if not root.exists() or not root.is_dir():
        raise ValueError(f"项目根目录不存在或不是目录：{project_root}")

    if patch is None:
        raise ValueError("请先执行 Dry Run，并确保校验通过")

    if not patch.get("operations"):
        raise ValueError("没有可执行的操作。请检查 Dry Run 结果。")

    executor = PatchExecutor(
        root,
        allow_delete=allow_delete,
        allow_multi_replace_exact=allow_multi_replace_exact,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        patch_text=patch_text,
        preview_text=preview_text,
    )

    log_text = executor.apply(patch)

    return PatchApplyResult(
        log_text=log_text,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
    )

class BackupRestoreExecutor:
    def __init__(
        self,
        project_root,
        source_backup_root,
        backup_enabled=True,
        backup_dir="99_归档/AI文件修改备份",
        force_restore=False,
        preview_text="",
    ):
        self.root = Path(project_root).resolve()
        self.source_backup_root = Path(source_backup_root).resolve()
        self.backup_enabled = backup_enabled
        self.backup_root = (
            make_backup_root(self.root, backup_dir, "备份还原")
            if backup_enabled
            else None
        )
        self.force_restore = force_restore
        self.preview_text = preview_text
        self.logs = []
        self.manifest = load_manifest(self.source_backup_root)
        self.has_mismatch = False
        self.restore_operations = []

    def log(self, msg):
        self.logs.append(msg)

    def backup_current_file(self, target: Path):
        if not self.backup_enabled or not target.exists():
            return ""

        rel = target.resolve().relative_to(self.root)
        backup_path = self.backup_root / "files" / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        return str(backup_path.relative_to(self.backup_root)).replace("\\", "/")

    def validate_manifest(self):
        if self.manifest.get("version") != "1.0":
            raise ValueError("仅支持 version=1.0 的备份 manifest")

        mode = self.manifest.get("mode")
        if mode not in ("forward", "restore"):
            raise ValueError(f"未知备份类型：{mode}")

        operations = self.manifest.get("operations")
        if not isinstance(operations, list):
            raise ValueError("manifest.operations 必须是数组")

    def build_plan(self):
        self.validate_manifest()
        self.restore_operations = []
        self.has_mismatch = False

        for item in self.manifest.get("operations", []):
            rel_path = normalize_rel_path(item["path"])
            target = safe_join(self.root, rel_path)
            current_exists = target.exists()
            current_hash = file_sha256(target)
            expected_hash = item.get("after_sha256", "")
            expected_exists = item.get("existed_after", False)

            mismatch = False
            issue = ""

            if expected_exists:
                if not current_exists:
                    mismatch = True
                    issue = "当前文件不存在，但备份记录显示回退前应存在"
                elif current_hash != expected_hash:
                    mismatch = True
                    issue = "当前文件内容与备份记录不一致"
            else:
                if current_exists:
                    mismatch = True
                    issue = "当前文件存在，但备份记录显示回退前应不存在"

            if mismatch:
                self.has_mismatch = True

            self.restore_operations.append({
                "source": item,
                "path": rel_path,
                "target": target,
                "mismatch": mismatch,
                "issue": issue,
            })

    def preview(self):
        self.build_plan()

        mode = self.manifest.get("mode")
        mode_text = "执行修改备份" if mode == "forward" else "备份还原备份"

        lines = []
        lines.append("【备份还原 Dry Run】")
        lines.append(f"备份类型：{mode_text}")
        lines.append(f"项目根目录：{self.root}")
        lines.append(f"备份来源：{self.source_backup_root}")

        if self.backup_enabled:
            lines.append(f"本次还原前备份目录：{self.backup_root}")
        else:
            lines.append("本次还原前备份：未启用")

        lines.append(f"允许强制还原：{self.force_restore}")
        lines.append("")

        if mode == "forward":
            lines.append("说明：将恢复到这次执行修改之前的状态。")
        else:
            lines.append("说明：将恢复到这次备份还原之前的状态，相当于撤销一次还原。")

        lines.append("")

        for i, plan in enumerate(self.restore_operations, 1):
            item = plan["source"]
            action = "还原文件" if item.get("existed_before", False) else "删除新增文件"
            line = f"{i}. [{action}] {plan['path']}"

            if plan["mismatch"]:
                line += f"  ⚠ 状态不一致：{plan['issue']}"

            lines.append(line)

        lines.append("")

        if self.has_mismatch:
            lines.append("检测到当前文件状态与备份记录不一致。")
            if self.force_restore:
                lines.append("已勾选允许强制还原，执行时会再次弹窗确认。")
            else:
                lines.append("未勾选允许强制还原，禁止执行还原。")
        else:
            lines.append("Dry Run 校验通过，当前文件状态与备份记录一致。")

        return "\n".join(lines)

    def apply(self):
        self.build_plan()

        if self.has_mismatch and not self.force_restore:
            raise ValueError("当前文件状态与备份记录不一致。请先 Dry Run 查看详情；如确认覆盖，请勾选允许强制还原。")

        if self.backup_enabled:
            self.backup_root.mkdir(parents=True, exist_ok=True)

        self.logs = []

        self.log("【开始执行备份还原】")
        self.log(f"项目根目录：{self.root}")
        self.log(f"备份来源：{self.source_backup_root}")

        if self.backup_enabled:
            self.log(f"本次还原前备份目录：{self.backup_root}")
        else:
            self.log("本次还原前备份：未启用")

        self.log("")

        restore_manifest_ops = []

        for i, plan in enumerate(self.restore_operations, 1):
            item = plan["source"]
            target = plan["target"]
            rel_path = plan["path"]
            existed_before_rollback = target.exists()
            before_hash = file_sha256(target)
            current_backup_file = self.backup_current_file(target)

            if item.get("existed_before", False):
                backup_file = item.get("backup_file", "")

                if not backup_file:
                    raise ValueError(f"缺少备份文件记录，无法还原：{rel_path}")

                source_file = self.source_backup_root / backup_file

                if not source_file.is_file():
                    raise ValueError(f"备份文件不存在：{source_file}")

                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                self.log(f"[还原] {rel_path}")
            else:
                if target.exists():
                    target.unlink()
                    self.log(f"[删除新增文件] {rel_path}")
                else:
                    self.log(f"[跳过] 新增文件已不存在：{rel_path}")

            restore_manifest_ops.append({
                "index": i,
                "op": "restore",
                "path": rel_path,
                "existed_before": existed_before_rollback,
                "existed_after": target.exists(),
                "before_sha256": before_hash,
                "after_sha256": file_sha256(target),
                "backup_file": current_backup_file,
            })

        if self.backup_enabled:
            manifest = {
                "version": "1.0",
                "mode": "restore",
                "project_root": str(self.root),
                "backup_root": str(self.backup_root),
                "restore_from": str(self.source_backup_root),
                "operations": restore_manifest_ops,
            }

            save_json(self.backup_root / "manifest.json", manifest)

            log_path = self.backup_root / "执行日志.txt"
            self.log("")
            self.log(f"[日志] {log_path}")

            file_log_lines = []

            if self.preview_text:
                file_log_lines.extend([
                    "【Dry Run 预演记录】",
                    self.preview_text,
                    "",
                    "=" * 60,
                    "【执行记录】",
                    "=" * 60,
                    "",
                ])

            file_log_lines.extend(self.logs)
            log_path.write_text("\n".join(file_log_lines), encoding="utf-8")

        return "\n".join(self.logs)


def preview_backup_restore(
    project_root,
    restore_source_dir,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
    force_restore=False,
):
    executor = BackupRestoreExecutor(
        project_root=project_root,
        source_backup_root=restore_source_dir,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        force_restore=force_restore,
    )

    preview_text = executor.preview()

    return BackupRestorePreviewResult(
        preview_text=preview_text,
        manifest=executor.manifest,
        has_mismatch=executor.has_mismatch,
    )


def apply_backup_restore(
    project_root,
    restore_source_dir,
    backup_enabled=True,
    backup_dir="99_归档/AI文件修改备份",
    force_restore=False,
    preview_text="",
):
    executor = BackupRestoreExecutor(
        project_root=project_root,
        source_backup_root=restore_source_dir,
        backup_enabled=backup_enabled,
        backup_dir=backup_dir,
        force_restore=force_restore,
        preview_text=preview_text,
    )

    log_text = executor.apply()

    return BackupRestoreApplyResult(
        log_text=log_text,
        backup_root=str(executor.backup_root) if executor.backup_root else "",
    )
