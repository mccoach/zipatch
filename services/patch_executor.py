# -*- coding: utf-8 -*-

import re
import shutil
from collections.abc import Mapping

from pathlib import Path

from core.constants import PATCH_PROTOCOL_DESCRIPTION, SUPPORTED_PATCH_OPS
from core.path_validation import safe_join, normalize_rel_path
from core.text_io import read_text_auto, write_text_utf8
from core.atomic_file import atomic_text_writer
from core.recovery_paths import checked_root
from services.recovery import (
    RecoveryProtection, check_isolation, plan_patch,
    recheck_action, recheck_plan, remove_entity,
)
from core.rule_lists import validate_file_rules
from core.text_locations import TextSnapshot
from core.text_newlines import normalize_newlines
from services.text_mutations import locate_replacement
from services.patch_contracts import (
    collect_content_contract_errors, collect_path_contract_errors,
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
        output_rules,
        root: Path,
        allow_delete=False,
        allow_multi_replace_exact=False,
        backup_enabled=True,
        backup_dir="99_归档/AI文件修改备份",
        patch_text="",
        preview_text="",
    ):
        validate_file_rules(output_rules)
        self.output_rules = output_rules
        self.text_newlines = {}
        self.root = checked_root(root)
        self.allow_delete = allow_delete
        self.allow_multi_replace_exact = allow_multi_replace_exact
        self.backup_enabled = backup_enabled
        self.backup_dir = backup_dir
        self.backup_root = None
        self.patch_text = patch_text
        self.preview_text = preview_text
        self.logs = []
        self.plan = None
        self.protection = None
        self.completed_actions = set()
        self.committed_text_operations = []
        self.source_snapshots = {}
        self.located_operations = []
        self.previewed = False
        self.consumed = False

    def log(self, msg):
        self.logs.append(msg)

    def op_label(self, op):
        return f'id="{op.get("id", "")}" {op.get("op", "")}'

    def validate_op(self, op):
        # 解析结果是 dict，固定计划是只读 Mapping；校验只消费参数，
        # 不要求可变性，也不把执行计划转回可变对象。
        if not isinstance(op, Mapping):
            raise ValueError("内部 OP 对象必须是参数映射")

        op_type = op.get("op")
        op_id = op.get("id", "")
        op_label = self.op_label(op)
        expected_count = None

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

        return expected_count

    def check_one_op(self, op):
        op_type = op.get("op", "")
        op_id = op.get("id", "")
        path = op.get("path", "")

        try:
            expected_count = self.validate_op(op)

            target_path = normalize_rel_path(path)
            target = safe_join(self.root, target_path)

            if op_type in ("replace_between", "replace_exact"):
                if target not in self.source_snapshots:
                    self.source_snapshots[target] = TextSnapshot.build(read_text_auto(target)[0])
                location = locate_replacement(
                    self.source_snapshots[target], op, expected_count,
                )
                self.located_operations.append((op, location))

            return PatchOpCheckResult(
                op_id=op_id,
                op_type=op_type,
                path=target_path,
                ok=True,
                message="校验通过",
                locator=make_op_locator(op),
            )

        except (OSError, ValueError) as e:
            return PatchOpCheckResult(
                op_id=op_id,
                op_type=op_type,
                path=path,
                ok=False,
                message=str(e),
                locator=make_op_locator(op),
            )

    def preview(self, patch):
        if self.previewed or self.consumed:
            raise ValueError("执行器只属于一次预演及执行，不能重新规划或复用")
        self.previewed = True
        completed = False
        try:
            if not isinstance(patch, dict):
                raise ValueError("内部修改包对象非法")

            if patch.get("version") != "3.0":
                raise ValueError("仅支持 V3 动态 boundary 修改包")

            ops = patch.get("operations")
            if not isinstance(ops, list) or not ops:
                raise ValueError("修改包 operations 必须是非空数组")

            check_results = []
            valid_ops = []
            for op in ops:
                result = self.check_one_op(op)
                check_results.append(result)
                if result.ok:
                    valid_ops.append(op)

            global_errors = []
            if valid_ops:
                path_errors = collect_path_contract_errors(valid_ops)
                content_errors = collect_content_contract_errors(self.located_operations)

                global_errors.extend(f"路径互斥：{item}" for item in path_errors)
                global_errors.extend(f"内容互斥：{item}" for item in content_errors)
                check_results.extend(
                    make_result_for_global_error(item) for item in global_errors
                )
                if global_errors:
                    valid_ops = []

            if valid_ops:
                try:
                    self.plan = plan_patch(self.root, valid_ops)
                    # 多个内容 OP 可以共享目标，但换行决定只属于该目标。
                    text_targets = dict.fromkeys(
                        action.expectations[0].path
                        for action in self.plan.actions
                        if action.execute and action.payload["op"] in (
                            "write_file", "append_text", "replace_exact", "replace_between",
                        )
                    )
                    self.text_newlines = {
                        target: (
                            "\r\n" if self.output_rules.matches(
                                target.relative_to(self.root).parts
                            ) else "\n"
                        )
                        for target in text_targets
                    }
                    self.protection = (
                        RecoveryProtection(self.plan, "patch", self.backup_dir)
                        if self.backup_enabled and self.plan.objects else None
                    )
                    self.backup_root = (
                        self.protection.task_root if self.protection else None
                    )
                except (OSError, ValueError) as error:
                    message = f"保护规划或材料隔离失败：{error}"
                    global_errors.append(message)
                    check_results.append(make_result_for_global_error(message))
                    valid_ops = []
                    self.plan = None
                    self.text_newlines.clear()
                    self.protection = None
                    self.backup_root = None

            success_count = len(valid_ops)
            failed_count = sum(not item.ok for item in check_results)
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
            result = (
                preview_text, valid_patch, failed_count > 0,
                success_count, failed_count, has_global_errors,
            )
            self.preview_text = preview_text
            completed = True
            return result
        finally:
            # 正文只服务本次预演；正常或异常退出都释放工作数据。
            self.source_snapshots.clear()
            self.located_operations.clear()
            if not completed:
                # 没有完整返回预演结果的任务，不得保留执行资格。
                # 不捕获异常，也不发布、回退或删除任何备份材料。
                self.plan = None
                self.text_newlines.clear()
                self.protection = None
                self.backup_root = None
                self.preview_text = ""

    def commit_text_operation(self, op, target, text):
        label = self.op_label(op)
        relative = target.relative_to(self.root)
        try:
            write_text_utf8(target, text)
        except (OSError, ValueError) as error:
            raise OSError(
                f"{label} 文本未提交：{relative}。\n{error}"
            ) from error
        self.committed_text_operations.append((label, str(relative)))
        try:
            self.log(f"[已提交] {label} 文本文件：{relative}")
        except (OSError, UnicodeError) as error:
            raise OSError(
                f"{label} 文本已经提交：{relative}；后续日志失败，"
                f"没有自动撤销。\n{error}"
            ) from error

    def prepare_text(self, op, target, expected_count):
        kind = op["op"]
        if kind == "write_file":
            complete = op["content"]
        else:
            source, _ = read_text_auto(target)
            if kind == "append_text":
                complete = source + op["content"]
            else:
                location = locate_replacement(
                    TextSnapshot.build(source), op, expected_count,
                )
                replacement = op["new"] if kind == "replace_exact" else op["content"]
                complete = location.compose(replacement)
        return normalize_newlines(complete, self.text_newlines[target])

    def perform_action(self, action, prepared_text):
        op = action.payload
        kind = op["op"]
        source = action.expectations[0].path
        if kind in ("write_file", "append_text", "replace_exact", "replace_between"):
            self.commit_text_operation(op, source, prepared_text)
        elif kind in DUAL_PATH_OPS:
            target = action.expectations[1].path
            remove_entity(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "copy_file":
                shutil.copy2(source, target)
            elif kind == "copy_dir":
                shutil.copytree(source, target, symlinks=False)
            else:
                shutil.move(str(source), str(target))
        elif kind == "create_dir":
            source.mkdir(parents=True, exist_ok=False)
        elif kind == "delete_file":
            source.unlink()
        elif kind == "delete_dir":
            shutil.rmtree(source)
        else:
            raise ValueError(f"{action.label} 未知正式动作")

    def write_execution_log_file(self):
        if self.protection is None or not self.protection.started:
            return
        with atomic_text_writer(
            self.backup_root / "执行日志.txt", newline="\n",
        ) as stream:
            if self.preview_text:
                stream.write(self.preview_text + "\n\n")
            stream.write("\n".join(self.logs) + "\n")
        if self.patch_text:
            with atomic_text_writer(
                self.backup_root / "修改包原文.txt", newline="",
            ) as stream:
                stream.write(self.patch_text)

    def apply(self):
        if self.consumed:
            raise ValueError("本次计划已经执行或失败，必须重新预演")
        if self.plan is None:
            raise ValueError("没有通过正式预演的执行计划")
        # 即使前置复核失败，也不允许重试旧任务对象。
        self.consumed = True
        recheck_plan(self.plan)
        check_isolation(
            self.plan, self.protection.task_root if self.protection else None,
        )
        self.logs = []
        self.log("【开始执行修改包】")
        self.log(f"协议版本：{PATCH_PROTOCOL_DESCRIPTION}")
        self.log(f"项目根目录：{self.root}")
        self.log(f"备份目录：{self.backup_root or '未启用或无需保护'}")
        self.log("规划、备份和执行期间请避免外部并发修改。")
        stage, current_index, action_started = "执行期复核", 0, False
        try:
            for current_index, action in enumerate(self.plan.actions):
                action_started = False
                stage = "固定动作状态及 V3 执行期复核"
                recheck_action(self.plan, current_index, self.completed_actions)
                expected_count = self.validate_op(action.payload)
                if not action.execute:
                    self.completed_actions.add(current_index)
                    self.log(f"[跳过] {action.label}：{action.payload['path']}")
                    continue
                stage = "文本正文准备"
                prepared_text = None
                if action.payload["op"] in (
                    "write_file", "append_text", "replace_exact", "replace_between",
                ):
                    prepared_text = self.prepare_text(
                        action.payload, action.expectations[0].path, expected_count,
                    )
                stage = "修改前保护及清单发布"
                if self.protection:
                    self.protection.protect(current_index)
                stage = "保护后固定前提复核"
                recheck_action(self.plan, current_index, self.completed_actions)
                stage = "业务动作"
                action_started = True
                self.perform_action(action, prepared_text)
                self.completed_actions.add(current_index)
                stage = "完成日志"
                self.log(f"[完成] {action.label}：{action.payload['path']}")
        except (OSError, ValueError) as error:
            action = self.plan.actions[current_index]
            completed = [
                self.plan.actions[index].label
                for index in sorted(self.completed_actions)
            ]
            message = (
                f"修改中断：{action.label}，path={action.payload['path']}，"
                f"阶段={stage}。\n"
                f"已经完成：{', '.join(completed) or '无'}。\n"
                f"当前业务动作可能部分改变目标：{'是' if action_started else '否'}。\n"
                f"已提交文本操作：{len(self.committed_text_operations)} 个"
                "（不是整包成功数量）。\n"
                + "".join(
                    f"- {label}，path={relative}\n"
                    for label, relative in self.committed_text_operations
                )
                + "已提交文件没有自动撤销。\n"
                "恢复依据不依赖成功记录，没有自动整批回滚。\n"
                f"备份目录：{self.backup_root or '未启用'}\n{error}"
            )
            if isinstance(error, OSError):
                raise OSError(message) from error
            raise ValueError(message) from error
        try:
            self.write_execution_log_file()
        except (OSError, ValueError) as error:
            raise OSError(
                "业务动作已经完成，但辅助日志或修改包副本保存失败；"
                "已提交文件没有自动撤销，恢复依据独立于这些记录。\n"
                f"备份目录：{self.backup_root or '未启用'}\n{error}"
            ) from error
        return "\n".join(self.logs)