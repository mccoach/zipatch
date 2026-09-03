# -*- coding: utf-8 -*-

import re
import shutil
from pathlib import Path

from core.constants import SUPPORTED_PATCH_OPS
from core.path_validation import safe_join, normalize_rel_path
from core.text_io import read_text_auto, write_text_utf8
from services.patch_backup import (
    copy_path_for_backup,
    make_backup_root,
    make_path_state,
    remove_path,
    save_json,
)
from services.patch_contracts import (
    collect_content_contract_errors,
    collect_path_contract_errors,
    normalize_text_newlines,
    validate_global_patch_contracts,
)
from services.patch_models import PatchOpCheckResult
from services.patch_reporter import format_patch_preview
from services.patch_ops import (
    DUAL_PATH_OPS,
    parse_required_positive_int,
)


def make_op_locator(op):
    return f'id="{op.get("id", "")}"'


def extract_first_real_id(text):
    match = re.search(r'id="([^"]+)"', text or "")

    if not match:
        return ""

    return match.group(1)


def make_result_for_global_error(message):
    op_id = extract_first_real_id(message)

    return PatchOpCheckResult(
        op_id=op_id,
        op_type="global_contract",
        path="全局修改包",
        ok=False,
        message=message,
        locator=f'id="{op_id}"',
    )


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

    def op_label(self, op):
        return f'id="{op.get("id", "")}" {op.get("op", "")}'

    def validate_patch(self, patch):
        """
        执行阶段的防御性复核。

        Dry Run 是生成执行计划的唯一阶段；
        执行阶段只允许消费 Dry Run 产出的 valid_patch。
        这里保留复核，是为了防止 Dry Run 后文件系统状态变化或内部调用异常。
        """
        if not isinstance(patch, dict):
            raise ValueError("内部修改包对象非法")

        if patch.get("version") != "2.0":
            raise ValueError("仅支持 V2 动态 boundary 修改包")

        ops = patch.get("operations")

        if not isinstance(ops, list) or not ops:
            raise ValueError("修改包 operations 必须是非空数组")

        validate_global_patch_contracts(self.root, ops)

        for op in ops:
            self.validate_op(op)

    def validate_op(self, op):
        if not isinstance(op, dict):
            raise ValueError("内部 OP 对象非法")

        op_type = op.get("op")
        op_id = op.get("id", "")
        op_label = self.op_label(op)

        if not op_id:
            raise ValueError("内部 OP 对象缺少 id")

        if op_type not in SUPPORTED_PATCH_OPS:
            raise ValueError(f"{op_label} 操作类型不支持：{op_type}")

        if "path" not in op:
            raise ValueError(f"{op_label} 缺少 path")

        target = safe_join(self.root, op["path"])

        if op_type == "write_file":
            if "content" not in op:
                raise ValueError(f"{op_label} 缺少 content")

            if_exists = op.get("if_exists", "error")

            if if_exists not in ("error", "overwrite", "skip"):
                raise ValueError(f"{op_label}.if_exists 非法：{if_exists}")

            if target.exists() and target.is_dir():
                raise ValueError(f"{op_label} 目标是目录：{op['path']}")

            if if_exists == "error" and target.exists():
                raise ValueError(f"{op_label} 目标已存在：{op['path']}")

        elif op_type == "append_text":
            if "content" not in op:
                raise ValueError(f"{op_label} 缺少 content")

            if not target.exists():
                raise ValueError(f"{op_label} 目标文件不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"{op_label} 目标是目录：{op['path']}")

        elif op_type == "replace_between":
            if "content" not in op:
                raise ValueError(f"{op_label} 缺少 content")

            for key in ("start_marker", "end_marker"):
                if key not in op:
                    raise ValueError(f"{op_label} 缺少 {key}")

            if not target.exists():
                raise ValueError(f"{op_label} 目标文件不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"{op_label} 目标是目录：{op['path']}")

        elif op_type == "replace_exact":
            if "old" not in op:
                raise ValueError(f"{op_label} 缺少 old")

            if "new" not in op:
                raise ValueError(f"{op_label} 缺少 new")

            if not target.exists():
                raise ValueError(f"{op_label} 目标文件不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"{op_label} 目标是目录：{op['path']}")

            expected_count = parse_required_positive_int(op.get("count"), "count")
            if expected_count > 1 and not self.allow_multi_replace_exact:
                raise ValueError(
                    f"{op_label} 要求替换 {expected_count} 处，"
                    "但当前未勾选“允许多处精确替换”。"
                )

        elif op_type in DUAL_PATH_OPS:
            if "new_path" not in op:
                raise ValueError(f"{op_label} 缺少 new_path")

            source = target
            target = safe_join(self.root, op["new_path"])
            if_exists = op.get("if_exists", "error")

            if if_exists not in ("error", "overwrite", "skip"):
                raise ValueError(f"{op_label}.if_exists 非法：{if_exists}")

            source_is_file_op = op_type in ("rename_file", "move_file", "copy_file")
            source_is_dir_op = op_type in ("rename_dir", "move_dir", "copy_dir")

            if not source.exists():
                raise ValueError(f"{op_label} 源路径不存在：{op['path']}")

            if source_is_file_op and not source.is_file():
                raise ValueError(f"{op_label} 源路径不是文件：{op['path']}")

            if source_is_dir_op and not source.is_dir():
                raise ValueError(f"{op_label} 源路径不是目录：{op['path']}")

            if op_type in ("rename_file", "rename_dir") and source.parent != target.parent:
                raise ValueError(f"{op_label} 只能在同一父目录内改名")

            if source_is_dir_op and source.resolve() == self.root:
                raise ValueError(f"{op_label} 不允许作用于项目根目录")

            if target.exists():
                if if_exists == "error":
                    raise ValueError(f"{op_label} 目标已存在：{op['new_path']}")

                if source_is_file_op and not target.is_file():
                    raise ValueError(f"{op_label} 目标已存在但不是文件：{op['new_path']}")

                if source_is_dir_op and not target.is_dir():
                    raise ValueError(f"{op_label} 目标已存在但不是目录：{op['new_path']}")

        elif op_type == "create_dir":
            if_exists = op.get("if_exists", "skip")

            if if_exists not in ("error", "skip"):
                raise ValueError(f"{op_label}.if_exists 非法：{if_exists}")

            if target.resolve() == self.root:
                raise ValueError(f"{op_label} 不允许作用于项目根目录")

            if target.exists():
                if not target.is_dir():
                    raise ValueError(f"{op_label} 目标已存在但不是目录：{op['path']}")

                if if_exists == "error":
                    raise ValueError(f"{op_label} 目标目录已存在：{op['path']}")

        elif op_type == "delete_file":
            if not self.allow_delete:
                raise ValueError(f"{op_label} 被拒绝：当前未勾选允许删除")

            if not target.exists():
                raise ValueError(f"{op_label} 目标不存在：{op['path']}")

            if target.is_dir():
                raise ValueError(f"{op_label} 不允许删除目录：{op['path']}")

        elif op_type == "delete_dir":
            if not self.allow_delete:
                raise ValueError(f"{op_label} 被拒绝：当前未勾选允许删除")

            if target.resolve() == self.root:
                raise ValueError(f"{op_label} 不允许删除项目根目录")

            if not target.exists():
                raise ValueError(f"{op_label} 目标不存在：{op['path']}")

            if not target.is_dir():
                raise ValueError(f"{op_label} 目标不是目录：{op['path']}")

    def check_one_op(self, op, index):
        op_type = op.get("op", "")
        op_id = op.get("id", "")
        path = op.get("path", "")
        op_label = self.op_label(op)

        try:
            self.validate_op(op)

            target_path = normalize_rel_path(path)
            target = safe_join(self.root, target_path)

            if op_type == "replace_between":
                text, enc = read_text_auto(target)
                start_marker = op["start_marker"]
                end_marker = op["end_marker"]
                s_count = text.count(start_marker)
                e_count = text.count(end_marker)

                if s_count != 1 or e_count != 1:
                    raise ValueError(
                        f"{op_label} 锚点不唯一：start_count={s_count}, end_count={e_count}"
                    )

                if text.find(start_marker) >= text.find(end_marker):
                    raise ValueError(f"{op_label} 起始锚点在结束锚点之后")

            if op_type == "replace_exact":
                text, enc = read_text_auto(target)
                normalized_text = normalize_text_newlines(text)
                old = normalize_text_newlines(op["old"])
                expected_count = parse_required_positive_int(op.get("count"), "count")
                actual_count = normalized_text.count(old)

                if actual_count != expected_count:
                    raise ValueError(
                        f"{op_label} 命中次数不符：expected={expected_count}, actual={actual_count}"
                    )

            return PatchOpCheckResult(
                op_id=op_id,
                op_type=op_type,
                path=target_path,
                ok=True,
                message="校验通过",
                locator=make_op_locator(op),
            )

        except Exception as e:
            return PatchOpCheckResult(
                op_id=op_id,
                op_type=op_type,
                path=path,
                ok=False,
                message=str(e),
                locator=make_op_locator(op),
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

        global_errors = []

        if valid_ops:
            path_errors = collect_path_contract_errors(valid_ops)
            content_errors = collect_content_contract_errors(self.root, valid_ops)

            for item in path_errors:
                global_errors.append(f"路径互斥：{item}")

            for item in content_errors:
                global_errors.append(f"内容互斥：{item}")

            for item in global_errors:
                check_results.append(make_result_for_global_error(item))

            if global_errors:
                valid_ops = []

        success_count = len(valid_ops)
        failed_count = len([item for item in check_results if not item.ok])
        has_global_errors = bool(global_errors)

        valid_patch = dict(patch)
        valid_patch["operations"] = valid_ops

        preview_text = format_patch_preview(
            root=self.root,
            patch=patch,
            check_results=check_results,
            success_count=success_count,
            failed_count=failed_count,
            has_global_errors=has_global_errors,
            backup_enabled=self.backup_enabled,
            backup_root=self.backup_root,
        )

        return preview_text, valid_patch, failed_count > 0, success_count, failed_count, has_global_errors

    def backup_file(self, target: Path):
        """
        备份执行前路径快照。

        名称保留为 backup_file 是为了沿用现有调用链和 manifest 字段；
        实际支持文件和目录：
        - 文件：copy2；
        - 目录：copytree 完整备份目录树。
        """
        if not self.backup_enabled:
            return None

        if not target.exists():
            return None

        target = target.resolve()

        if target in self.backed_up:
            return self.backed_up[target]

        rel = target.relative_to(self.root)
        backup_path = self.backup_root / "files" / rel
        copy_path_for_backup(target, backup_path)
        self.backed_up[target] = backup_path

        self.log(f"[备份] {rel} -> {backup_path}")

        return backup_path

    def record_manifest_operation(
        self,
        op_id,
        op_type,
        rel_path,
        before_state,
        after_state,
        extra=None,
    ):
        """
        记录单路径 OP 的原子状态。

        before_state / after_state 必须由执行分支在操作前后直接采集；
        本函数只负责落 manifest，不再根据零散参数反推状态。
        """
        item = {
            "id": op_id,
            "op": op_type,
            "path": str(rel_path).replace("\\", "/"),
            "before_state": {
                "path": before_state,
            },
            "after_state": {
                "path": after_state,
            },
        }

        if extra:
            item.update(extra)

        self.manifest_operations.append(item)

    def record_dual_path_manifest_operation(
        self,
        op_id,
        op_type,
        source_rel,
        target_rel,
        source_before_state,
        target_before_state,
        source_after_state,
        target_after_state,
        extra=None,
    ):
        item = {
            "id": op_id,
            "op": op_type,
            "path": str(source_rel).replace("\\", "/"),
            "new_path": str(target_rel).replace("\\", "/"),
            "before_state": {
                "path": source_before_state,
                "new_path": target_before_state,
            },
            "after_state": {
                "path": source_after_state,
                "new_path": target_after_state,
            },
        }

        if extra:
            item.update(extra)

        self.manifest_operations.append(item)

    def write_patch_copy_and_manifest(self, patch, status="completed", error_message=""):
        if not self.backup_enabled:
            return

        patch_path = self.backup_root / "patch" / "修改包原文.txt"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(self.patch_text or "", encoding="utf-8")

        manifest = {
            "version": "2.4",
            "mode": "forward",
            "status": status,
            "project_root": str(self.root),
            "backup_root": str(self.backup_root),
            "patch_file": "patch/修改包原文.txt",
            "patch_boundary": patch.get("boundary"),
            "operations": self.manifest_operations,
        }

        if error_message:
            manifest["error_message"] = error_message

        save_json(self.backup_root / "manifest.json", manifest)

    def apply_write_file(self, op, target, rel_display):
        if_exists = op.get("if_exists", "error")
        op_label = self.op_label(op)

        if target.exists():
            if if_exists == "skip":
                before_state = make_path_state(target)
                self.log(f"[跳过] {op_label} 文件已存在且 if_exists=skip：{rel_display}")
                self.record_manifest_operation(
                    op["id"],
                    op["op"],
                    rel_display,
                    before_state,
                    make_path_state(target),
                    extra={"write_kind": "skip"},
                )
                return

            if if_exists == "error":
                raise ValueError(f"{op_label} write_file 目标已存在：{rel_display}")

            backup_path = self.backup_file(target)
            before_state = make_path_state(target, self.backup_root, backup_path)
            write_kind = "overwrite"
        else:
            before_state = make_path_state(target)
            write_kind = "create"

        write_text_utf8(target, op["content"])
        self.log(f"[完成] {op_label} 写入文件：{rel_display}")
        self.record_manifest_operation(
            op["id"],
            op["op"],
            rel_display,
            before_state,
            make_path_state(target),
            extra={"write_kind": write_kind},
        )

    def apply_append_text(self, op, target, rel_display):
        op_label = self.op_label(op)
        backup_path = self.backup_file(target)
        before_state = make_path_state(target, self.backup_root, backup_path)
        old_text, enc = read_text_auto(target)
        write_text_utf8(target, old_text + op["content"])
        self.log(f"[完成] {op_label} 追加文本：{rel_display}")
        self.record_manifest_operation(
            op["id"],
            op["op"],
            rel_display,
            before_state,
            make_path_state(target),
        )

    def apply_replace_between(self, op, target, rel_display):
        op_label = self.op_label(op)
        backup_path = self.backup_file(target)
        before_state = make_path_state(target, self.backup_root, backup_path)
        text, enc = read_text_auto(target)

        start_marker = op["start_marker"]
        end_marker = op["end_marker"]

        s_count = text.count(start_marker)
        e_count = text.count(end_marker)

        if s_count != 1 or e_count != 1:
            raise ValueError(
                f"{op_label} replace_between 锚点不唯一："
                f"start_count={s_count}, end_count={e_count}, path={rel_display}"
            )

        s_idx = text.find(start_marker)
        e_idx = text.find(end_marker)

        if s_idx >= e_idx:
            raise ValueError(f"{op_label} replace_between 起始锚点在结束锚点之后：{rel_display}")

        new_text = text[:s_idx] + op["content"] + text[e_idx + len(end_marker):]
        write_text_utf8(target, new_text)
        self.log(f"[完成] {op_label} 替换锚点区间：{rel_display}")
        self.record_manifest_operation(
            op["id"],
            op["op"],
            rel_display,
            before_state,
            make_path_state(target),
        )

    def apply_replace_exact(self, op, target, rel_display):
        op_label = self.op_label(op)
        backup_path = self.backup_file(target)
        before_state = make_path_state(target, self.backup_root, backup_path)
        text, enc = read_text_auto(target)

        normalized_text = normalize_text_newlines(text)
        old = normalize_text_newlines(op["old"])
        new = normalize_text_newlines(op["new"])
        expected_count = parse_required_positive_int(op.get("count"), "count")
        actual_count = normalized_text.count(old)

        if actual_count != expected_count:
            raise ValueError(
                f"{op_label} replace_exact 命中次数不符："
                f"expected={expected_count}, actual={actual_count}, path={rel_display}"
            )

        new_text = normalized_text.replace(old, new, expected_count)
        write_text_utf8(target, new_text)
        self.log(f"[完成] {op_label} 精确替换 {expected_count} 处：{rel_display}")
        self.record_manifest_operation(
            op["id"],
            op["op"],
            rel_display,
            before_state,
            make_path_state(target),
        )

    def apply_dual_path_op(self, op, source):
        op_type = op["op"]
        op_label = self.op_label(op)
        target = safe_join(self.root, op["new_path"])
        source_rel_display = source.relative_to(self.root)
        target_rel_display = target.relative_to(self.root)
        if_exists = op.get("if_exists", "error")

        source_backup_path = None
        target_backup_path = None

        if self.backup_enabled and source.exists():
            source_backup_path = self.backup_file(source)

        if self.backup_enabled and target.exists():
            target_backup_path = self.backup_file(target)

        source_before_state = make_path_state(
            source,
            self.backup_root,
            source_backup_path,
        )
        target_before_state = make_path_state(
            target,
            self.backup_root,
            target_backup_path,
        )

        if target.exists() and if_exists == "skip":
            self.log(f"[跳过] {op_label} 目标已存在且 if_exists=skip：{target_rel_display}")
            self.record_dual_path_manifest_operation(
                op["id"],
                op_type,
                source_rel_display,
                target_rel_display,
                source_before_state,
                target_before_state,
                make_path_state(source),
                make_path_state(target),
                extra={"write_kind": "skip"},
            )
            return

        if target.exists():
            remove_path(target)
            write_kind = "overwrite"
        else:
            write_kind = "create"

        target.parent.mkdir(parents=True, exist_ok=True)

        if op_type in ("copy_file", "copy_dir"):
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        else:
            shutil.move(str(source), str(target))

        self.log(f"[完成] {op_label}：{source_rel_display} -> {target_rel_display}")
        self.record_dual_path_manifest_operation(
            op["id"],
            op_type,
            source_rel_display,
            target_rel_display,
            source_before_state,
            target_before_state,
            make_path_state(source),
            make_path_state(target),
            extra={"write_kind": write_kind},
        )

    def apply_create_dir(self, op, target, rel_display):
        op_label = self.op_label(op)
        before_state = make_path_state(target)

        if target.exists():
            self.log(f"[跳过] {op_label} 目录已存在且 if_exists=skip：{rel_display}")
            self.record_manifest_operation(
                op["id"],
                op["op"],
                rel_display,
                before_state,
                make_path_state(target),
                extra={"write_kind": "skip"},
            )
            return

        target.mkdir(parents=True, exist_ok=True)
        self.log(f"[完成] {op_label} 创建目录：{rel_display}")
        self.record_manifest_operation(
            op["id"],
            op["op"],
            rel_display,
            before_state,
            make_path_state(target),
            extra={"write_kind": "create"},
        )

    def apply_delete_file(self, op, target, rel_display):
        op_label = self.op_label(op)

        if not self.allow_delete:
            raise ValueError(f"{op_label} 删除操作被拒绝：未允许删除")

        if target.is_dir():
            raise ValueError(f"{op_label} delete_file 不允许删除目录：{rel_display}")

        backup_path = self.backup_file(target)
        before_state = make_path_state(target, self.backup_root, backup_path)
        target.unlink()
        self.log(f"[完成] {op_label} 删除文件：{rel_display}")
        self.record_manifest_operation(
            op["id"],
            op["op"],
            rel_display,
            before_state,
            make_path_state(target),
        )

    def apply_delete_dir(self, op, target, rel_display):
        op_label = self.op_label(op)

        if not self.allow_delete:
            raise ValueError(f"{op_label} 删除目录操作被拒绝：未允许删除")

        if target.resolve() == self.root:
            raise ValueError(f"{op_label} delete_dir 不允许删除项目根目录")

        if not target.is_dir():
            raise ValueError(f"{op_label} delete_dir 目标不是目录：{rel_display}")

        backup_path = self.backup_file(target)
        before_state = make_path_state(target, self.backup_root, backup_path)
        shutil.rmtree(target)
        self.log(f"[完成] {op_label} 删除文件夹：{rel_display}")
        self.record_manifest_operation(
            op["id"],
            op["op"],
            rel_display,
            before_state,
            make_path_state(target),
        )

    def apply_one_op(self, op):
        op_type = op["op"]
        op_id = op.get("id", "")
        path = normalize_rel_path(op["path"])
        target = safe_join(self.root, path)
        rel_display = target.relative_to(self.root)

        self.log(f'---- id="{op_id}" {op_type} {rel_display} ----')

        if op_type == "write_file":
            self.apply_write_file(op, target, rel_display)
            return

        if op_type == "append_text":
            self.apply_append_text(op, target, rel_display)
            return

        if op_type == "replace_between":
            self.apply_replace_between(op, target, rel_display)
            return

        if op_type == "replace_exact":
            self.apply_replace_exact(op, target, rel_display)
            return

        if op_type in DUAL_PATH_OPS:
            self.apply_dual_path_op(op, target)
            return

        if op_type == "create_dir":
            self.apply_create_dir(op, target, rel_display)
            return

        if op_type == "delete_file":
            self.apply_delete_file(op, target, rel_display)
            return

        if op_type == "delete_dir":
            self.apply_delete_dir(op, target, rel_display)
            return

        raise ValueError(f'id="{op_id}" 未知操作类型：{op_type}')

    def write_execution_log_file(self):
        if not self.backup_enabled:
            self.log("[日志] 未启用自动备份，未写入备份目录日志文件。")
            return

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

        try:
            for op in patch["operations"]:
                self.apply_one_op(op)

            self.write_patch_copy_and_manifest(patch)

        except Exception as e:
            if self.backup_enabled and self.manifest_operations:
                self.write_patch_copy_and_manifest(
                    patch,
                    status="partial_failed",
                    error_message=str(e),
                )
                self.log("")
                self.log("[警告] 执行中途失败，已写入 partial_failed manifest，可用于还原已成功执行的部分操作。")

            raise

        self.log("")
        self.write_execution_log_file()

        return "\n".join(self.logs)