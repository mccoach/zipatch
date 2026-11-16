# -*- coding: utf-8 -*-

"""修改包完整准备、13类OP共享执行及严格version 4恢复事实。

正文复用已有定位和组合；观察、保护及文件系统变更
直接消费正式核心。固定机制、一次资格、失败停止。
"""

import re
import uuid
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from core.constants import PATCH_PROTOCOL_DESCRIPTION, SUPPORTED_PATCH_OPS
from core.content_publication import ContentPublicationResult
from core.fixed_content_execution import FixedContentExecution
from core.file_observation import (
    FileObservation, TreeObservation,
    observe_file, observe_tree, project_removed_file_links,
    recheck_file, recheck_tree,
)
from core.filesystem_changes import (
    FilesystemChangeResult, execute_tree_copy, execute_tree_removal,
    prepare_directory_creation, prepare_file_removal,
    prepare_tree_copy, prepare_tree_removal,
)
from core.name_moves import (
    NativeNameMoveResult,
    execute_observed_name_move, prepare_observed_name_move,
)
from core.path_validation import normalize_rel_path, safe_join
from core.protection_projection import build_protection_projection
from core.recovery_paths import checked_chain, checked_root, identity, overlaps
from core.recovery_protection import RecoveryProtectionSession
from core.rule_lists import validate_file_rules
from core.text_io import decode_text_bytes
from core.text_locations import TextSnapshot
from core.time_utils import now_stamp
from core.task_observation import TaskObservationState
from core.fixed_task_execution import FixedTaskExecution, FixedTaskWork
from services.patch_backup import resolve_backup_base_dir
from services.patch_contracts import (
    collect_content_contract_errors, collect_path_contract_errors,
)
from services.patch_file_plan import (
    PatchAction, PatchPlan, PatchUnitResult, SkippedPatchPath,
    TEXT_WRITE_OPS, build_execution_units, patch_observation_kind,
)
from services.patch_models import PatchOpCheckResult
from services.patch_ops import (
    CONTENT_OPS, DUAL_PATH_OPS, OP_ALLOWED_HEAD_PARAMS,
    OP_REQUIRED_TEXT_BLOCKS, parse_required_positive_int,
)
from services.patch_reporter import format_patch_preview
from services.text_mutations import locate_replacement


def make_op_locator(op):
    return f'id="{op.get("id", "")}"'


def extract_first_real_id(text):
    match = re.search(r'id="([^"]+)"', text or "")
    return match.group(1) if match else ""


def make_result_for_global_error(message):
    identifier = extract_first_real_id(message)
    return PatchOpCheckResult(
        identifier, "global_contract", "全局修改包",
        False, message, f'id="{identifier}"',
    )


def _files(observed):
    if isinstance(observed, TreeObservation):
        return observed.files
    if isinstance(observed, FileObservation) and observed.kind == "file":
        return (("", observed),)
    return ()


def _absent(observed):
    return FileObservation(
        observed.path,
        (*observed.chain[:-1], (observed.path, "absent")),
        "absent", None, None, None, None, None, None,
    )



def _release_observation(observed):
    if isinstance(observed, FileObservation):
        return replace(observed, source_bytes=None)
    if isinstance(observed, TreeObservation):
        return replace(observed, files=tuple(
            (relative, replace(member, source_bytes=None))
            for relative, member in observed.files
        ))
    return observed


def _recheck(observed):
    if isinstance(observed, SkippedPatchPath):
        _, current = checked_chain(observed.path, include_observation=True)
        if current != observed.chain:
            raise ValueError(f"固定跳过路径前提变化：{observed.path}")
    elif isinstance(observed, TreeObservation):
        recheck_tree(observed)
    else:
        recheck_file(observed)


class PatchExecutionError(OSError):
    """任务角色和真实前缀明确，不从cause或诊断文字猜副作用。"""

    def __init__(self, task, error):
        self.task = task
        self.error = error
        self.role = task.role
        self.current = task.current
        self.stage = task.stage
        self.results = task.results
        self.unit_results = tuple(task.unit_results.values())
        self.auxiliary_results = tuple(task.auxiliary_results)
        self.fact_error = task.fact_error
        self.work_completed = task.work_completed
        self.rollback_status = "not_attempted"
        self.rollback_report = ""
        self.rollback_error = None
        super().__init__(task.failure_report(error))

    def __str__(self):
        original = super().__str__()
        return (
            original + "\n\n【失败处理结果】\n" + self.rollback_report
            if self.rollback_report else original
        )


class PatchExecutor:
    def __init__(
        self, output_rules, root: Path, allow_delete=False,
        allow_multi_replace_exact=False,
        backup_dir="99_归档/AI文件修改备份",
        patch_text="", preview_text="", normalize_text_newlines=True,
    ):
        for name, value in (
            ("允许删除", allow_delete),
            ("允许多处精确替换", allow_multi_replace_exact),

            ("文本换行标准化", normalize_text_newlines),
        ):
            if type(value) is not bool:
                raise TypeError(f"{name}开关必须是布尔值")
        if normalize_text_newlines:
            validate_file_rules(output_rules)
        self.root = checked_root(root)
        self.allow_delete = allow_delete
        self.allow_multi_replace_exact = allow_multi_replace_exact

        self.backup_dir = backup_dir
        self.normalize_text_newlines = normalize_text_newlines
        self.output_rules = output_rules if normalize_text_newlines else None
        self.patch_text = patch_text
        self.preview_text = preview_text
        self.plan = None
        self.projection = None
        self.protection = None
        self.backup_root = None
        self.execution_units = ()
        self.source_snapshots = {}
        self.located_operations = []
        self._observations = {}
        self._actions = []
        self.observation_state = TaskObservationState(())
        self._active_unit = None
        self.logs = []
        self.auxiliary_results = []
        self.fact_error = None
        self.current = None
        self.stage = "尚未执行"
        self.role = "任务准备"
        self.task_execution = FixedTaskExecution()
        self.previewed = False

    @property
    def consumed(self):
        return self.task_execution.consumed

    @property
    def work_completed(self):
        return self.task_execution.work_completed

    @property
    def results(self):
        return self.task_execution.state.results

    @property
    def active_change(self):
        return self.task_execution.state.active_change

    @property
    def unit_results(self):
        return {
            unit.index: PatchUnitResult(
                unit, self.task_execution.entry_result(unit.index),
            )
            for unit in self.execution_units
        }

    @property
    def confirmed_content_results(self):
        return tuple(
            receipt for receipt in self.results
            if isinstance(receipt, ContentPublicationResult)
            and receipt.content_completed
        )

    @property
    def completed_actions(self):
        return {
            index for result in self.unit_results.values()
            if result.work_completed or result.status == "skipped"
            for index in result.unit.indices
        }

    @property
    def backup_completed(self):
        return self.protection is not None and self.protection.backup_completed

    @property
    def created_directories(self):
        return self.task_execution.state.created_directories

    def _stage(self, role, target, stage):
        self.role, self.current, self.stage = role, target, stage

    def log(self, message):
        self.logs.append(message)

    def op_label(self, op):
        return f'id="{op.get("id", "")}" {op.get("op", "")}'

    def validate_op(self, op):
        """业务参数及原路径校验；不读取正文或建立保护资格。"""
        if not isinstance(op, Mapping):
            raise ValueError("内部 OP 对象必须是参数映射")
        kind = op.get("op")
        identifier = op.get("id")
        if type(kind) is not str or kind not in SUPPORTED_PATCH_OPS:
            raise ValueError("内部 OP 操作类型不支持")
        if (
            type(identifier) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]+", identifier) is None
        ):
            raise ValueError("内部 OP id 必须是有效非空字符串")
        allowed = {
            "op", "_line_no", *OP_ALLOWED_HEAD_PARAMS[kind],
            *OP_REQUIRED_TEXT_BLOCKS[kind],
        }
        if set(op) - allowed:
            raise ValueError(f"{self.op_label(op)} 包含不允许的参数")
        for field in OP_REQUIRED_TEXT_BLOCKS[kind]:
            if field not in op or type(op[field]) is not str:
                raise ValueError(f"{self.op_label(op)} 缺少有效字符串 {field}")
            if field in ("path", "new_path", "old", "start_marker", "end_marker"):
                if not op[field]:
                    raise ValueError(f"{self.op_label(op)} {field} 不能为空")
        count = None
        if kind == "replace_exact":
            count = parse_required_positive_int(op.get("count"), "count")
            if count > 1 and not self.allow_multi_replace_exact:
                raise ValueError(
                    f"{self.op_label(op)} 要求替换 {count} 处，"
                    "但当前未勾选“允许多处精确替换”。"
                )
        if kind == "create_dir":
            if op.get("if_exists", "skip") not in ("error", "skip"):
                raise ValueError(f"{self.op_label(op)}.if_exists 非法")
        elif kind == "write_file" or kind in DUAL_PATH_OPS:
            if op.get("if_exists", "error") not in ("error", "overwrite", "skip"):
                raise ValueError(f"{self.op_label(op)}.if_exists 非法")
        if kind in ("delete_file", "delete_dir") and not self.allow_delete:
            raise ValueError("当前未勾选允许删除")
        return safe_join(self.root, op["path"]), count

    def _observe(self, path, kind):
        key = identity(path)
        if key in self._observations:
            previous = self._observations[key]
            if isinstance(previous, (OSError, ValueError)):
                raise previous
            if patch_observation_kind(previous) != kind:
                raise ValueError(f"准备期间对象类型变化：{path}")
            return previous
        try:
            observed = (
                observe_tree(path, include_bytes=True, include_mtime=True)
                if kind == "directory" else
                observe_file(
                    path, include_bytes=True, include_digest=True,
                    include_mtime=True,
                )
            )
            if patch_observation_kind(observed) != kind:
                raise ValueError(f"准备期间对象类型变化：{path}")
        except (OSError, ValueError) as error:
            self._observations[key] = error
            raise
        self._observations[key] = observed
        return observed

    def _prepare_action(self, op, primary):
        kind = op["op"]
        source, source_chain = checked_chain(primary, include_observation=True)
        source_kind = source_chain[-1][1]
        paths = [(source, source_chain, source_kind)]
        if kind in DUAL_PATH_OPS:
            target, target_chain = checked_chain(
                safe_join(self.root, op["new_path"]), include_observation=True,
            )
            target_kind = target_chain[-1][1]
            paths.append((target, target_chain, target_kind))
            required = "directory" if kind.endswith("_dir") else "file"
            if source_kind != required:
                raise ValueError(f"{self.op_label(op)} 源路径不存在或类型不匹配")
            if target_kind not in ("absent", required):
                raise ValueError(f"{self.op_label(op)} 目标类型不匹配")
            if overlaps(source, target):
                raise ValueError(f"{self.op_label(op)} 来源目标路径冲突")
            if kind in ("rename_file", "rename_dir") and source.parent != target.parent:
                raise ValueError(f"{self.op_label(op)} 只能在同一父目录内改名")
            policy = op.get("if_exists", "error")
            if target_kind != "absent" and policy == "error":
                raise ValueError(f"{self.op_label(op)} 目标已存在")
            execute = target_kind == "absent" or policy != "skip"
        elif kind == "write_file":
            if source_kind == "directory":
                raise ValueError(f"{self.op_label(op)} 目标是目录")
            policy = op.get("if_exists", "error")
            if source_kind != "absent" and policy == "error":
                raise ValueError(f"{self.op_label(op)} 目标已存在")
            execute = source_kind == "absent" or policy != "skip"
        elif kind == "create_dir":
            if source_kind not in ("absent", "directory"):
                raise ValueError(f"{self.op_label(op)} 目标已存在但不是目录")
            if source_kind == "directory" and op.get("if_exists", "skip") == "error":
                raise ValueError(f"{self.op_label(op)} 目标目录已存在")
            execute = source_kind == "absent"
        else:
            required = "directory" if kind == "delete_dir" else "file"
            if source_kind != required:
                raise ValueError(f"{self.op_label(op)} 目标文件不存在或类型不匹配")
            execute = True
        expectations = tuple(
            self._observe(path, observed_kind)
            if execute else SkippedPatchPath(path, chain, observed_kind)
            for path, chain, observed_kind in paths
        )
        move = None
        if execute and kind in (
            "move_file", "rename_file", "move_dir", "rename_dir",
        ):
            move = prepare_observed_name_move(*expectations)
        if execute and kind == "copy_file":
            before, target = expectations
            if (
                target.kind == "file"
                and before.object_identity == target.object_identity
            ):
                raise ValueError("复制来源和目标实际为同一对象")
        return PatchAction(
            self.op_label(op), MappingProxyType(dict(op)),
            execute, expectations, move,
        )

    def check_one_op(self, op):
        mapping = isinstance(op, Mapping)
        identifier = op.get("id", "") if mapping else ""
        kind = op.get("op", "") if mapping else ""
        path = op.get("path", "") if mapping else ""
        try:
            primary, count = self.validate_op(op)
            action = self._prepare_action(op, primary)
            if action.execute and kind in CONTENT_OPS:
                target = action.expectations[0].path
                if target not in self.source_snapshots:
                    try:
                        source, _ = decode_text_bytes(
                            action.expectations[0].source_bytes,
                            source_description="修改包目标文件", path=target,
                        )
                        self.source_snapshots[target] = TextSnapshot.build(source)
                    except ValueError as error:
                        self.source_snapshots[target] = error
                snapshot = self.source_snapshots[target]
                if isinstance(snapshot, ValueError):
                    raise snapshot
                if kind != "append_text":
                    self.located_operations.append((
                        op, locate_replacement(snapshot, op, count),
                    ))
            self._actions.append(action)
            return PatchOpCheckResult(
                identifier, kind, normalize_rel_path(path),
                True, "校验通过", make_op_locator(op),
            )
        except (OSError, ValueError) as error:
            return PatchOpCheckResult(
                identifier, kind, path, False, str(error),
                make_op_locator(op) if mapping else "",
            )

    def _clear_plan(self):
        self.plan = None
        self.projection = None
        self.protection = None
        self.backup_root = None
        self.execution_units = ()

        self.observation_state.release_observations()

    def preview(self, patch):
        if self.previewed or self.consumed:
            raise ValueError("执行器只属于一次预演，不能重新规划或复用")
        self.previewed = True
        returned = False
        try:
            if (
                not isinstance(patch, dict) or patch.get("version") != "3.0"
                or not isinstance(patch.get("operations"), list)
                or not patch["operations"]
            ):
                raise ValueError("仅支持具有非空operations的V3修改包")
            operations = patch["operations"]
            checks = [self.check_one_op(op) for op in operations]
            valid = [op for op, check in zip(operations, checks) if check.ok]
            errors = [
                "路径互斥：" + message
                for message in collect_path_contract_errors(valid)
            ]
            errors.extend(
                "内容互斥：" + message
                for message in collect_content_contract_errors(self.located_operations)
            )
            identifiers = [op["id"] for op in valid]
            if len(set(identifiers)) != len(identifiers):
                errors.append("整包OP id重复")
            checks.extend(make_result_for_global_error(message) for message in errors)
            if not any(not check.ok for check in checks):
                phase = "全部文件成果准备"
                try:
                    self.plan = PatchPlan(self.root, tuple(self._actions))
                    newlines = None
                    if self.normalize_text_newlines:
                        targets = dict.fromkeys(
                            action.expectations[0].path
                            for action in self.plan.actions
                            if action.execute and action.payload["op"] in TEXT_WRITE_OPS
                        )
                        newlines = {
                            path: "\r\n" if self.output_rules.matches(
                                path.relative_to(self.root).parts,
                            ) else "\n"
                            for path in targets
                        }
                    self.execution_units = build_execution_units(
                        self.plan, self.source_snapshots,
                        self.located_operations, text_newlines=newlines,
                    )

                    phase = "保护规划或材料隔离"
                    self.projection = build_protection_projection(
                        (unit.fixed for unit in self.execution_units if unit.execute),
                        forbidden_roots=(self.root,),
                    )
                    self._validate_shared_outcomes()
                    if self.projection.units:
                        task_id = uuid.uuid4().hex
                        self.backup_root = (
                            resolve_backup_base_dir(self.root, self.backup_dir)
                            / f"{now_stamp()}_执行修改_{task_id}"
                        )
                        self.protection = RecoveryProtectionSession(
                            self.projection, self.backup_root, task_id, "patch",
                            material_base=self.root,
                        )
                    self.observation_state = TaskObservationState(
                        observed
                        for action in self.plan.actions
                        for observed in action.expectations
                    )
                except (OSError, ValueError) as error:
                    message = f"{phase}失败：{error}"
                    errors.append(message)
                    checks.append(make_result_for_global_error(message))
                    self._clear_plan()
            failed = sum(not check.ok for check in checks)
            success = len(operations) if not failed else 0
            if failed:
                self._clear_plan()
            report = format_patch_preview(
                self.root, patch, checks, success, failed, bool(errors),
                self.backup_root,
            )
            report += (
                "\n\n文本换行：标准化已开启；完整成果按固定名单使用LF或CRLF。"
                if self.normalize_text_newlines else
                "\n\n文本换行：标准化已关闭；保留最终正文原换行字符。"
            )
            report += (
                "\n同文件全部文本成员共用固定原文和原文区间；"
                "全部替换一次组合，全部追加按声明顺序连接。"
                "\n非连续成员在首次OP位置共同发布，不产生逐成员中间成果。"
                "\n全部正文在预演阶段组合、转换和编码；执行只复核、保护及发布。"
                "\n正式保护及工作事实使用严格version 4。"
                "\n备份材料保留原文件名及项目相对目录；"
                "清单分别记录绝对目标和材料相对位置。"
                "\n名称机制准备时固定；原生报错停止，不切换复制删除。"
                "\n正文确认、工作完成、保护和辅助输出分别表达。"
                "\n全部必要原备份成功后才开始工作修改。"
                "\n备份失败清理本次任务目录，清理失败提示人工处理。"
                "\n工作失败自动恢复原备份，不生成二次备份或自动续执行。"
                "\n人类执行强制备份；自动回滚不另备份，失败保留原备份并提示。"
                "\n这些检查不是文件锁，期间请勿外部修改。"
            )
            if self.plan is not None:
                report += f"\n固定执行单元：{len(self.execution_units)} 个。\n【文件级共同成员】"
                for unit in self.execution_units:
                    if unit.text_file is not None:
                        report += (
                            f"\n- {unit.text_file.target.relative_to(self.root)}："
                            + "；".join(unit.labels)
                        )
            self.preview_text = report
            returned = True
            return (
                report, patch, bool(failed), success, failed,
                bool(errors), tuple(checks),
            )
        finally:
            self.source_snapshots.clear()
            self.located_operations.clear()
            self._observations.clear()
            self._actions.clear()
            if not returned:
                self._clear_plan()
                self.preview_text = ""

    def _validate_shared_outcomes(self):
        """提前拒绝共享对象互相矛盾的内容和名称承诺，不搜索其他名称。"""
        writes = {}
        for unit in self.execution_units:
            if not unit.execute:
                continue
            content_unit = (
                unit.text_file is not None
                or unit.members[0].payload["op"] == "copy_file"
            )
            if not content_unit:
                continue
            for effect in unit.fixed.effects:
                before = effect.before
                if (
                    isinstance(before, FileObservation)
                    and before.kind == "file" and before.link_count > 1
                    and effect.after.kind == "file"
                ):
                    digest = effect.after.records[0][2]
                    previous = writes.setdefault(before.object_identity, digest)
                    if previous != digest:
                        raise ValueError(
                            "关联硬链接名称要求不同最终内容，无法同时满足；"
                            f"请通过同一名称合并修改：{before.path}"
                        )
        for unit in self.execution_units:
            if not unit.execute:
                continue
            content_unit = (
                unit.text_file is not None
                or unit.members[0].payload["op"] == "copy_file"
            )
            if not content_unit:
                for effect in unit.fixed.effects:
                    if any(
                        file.object_identity in writes
                        for _, file in _files(effect.before)
                    ):
                        raise ValueError(
                            "同一硬链接对象的内容修改与名称或目录操作不能混用；"
                            "请分包执行。"
                        )
            for source in unit.fixed.reads:
                if any(file.object_identity in writes for _, file in _files(source)):
                    raise ValueError(
                        "复制来源与本包内容修改目标共享硬链接对象；请分包执行。"
                    )

    def recheck(self):
        """联合业务范围比较；共同状态负责创建目录身份和父链。"""
        checked_root(self.root)
        self.task_execution.state.recheck_created_directories()
        results = self.results
        self.observation_state.consume_results(results)
        seen = set()
        for unit in self.execution_units:
            if unit is self._active_unit:
                continue
            for action in unit.members:
                for observed in action.expectations:
                    key = identity(observed.path)
                    if key in seen:
                        continue
                    seen.add(key)
                    self.task_execution.state.recheck_parents(observed)
                    expected = (
                        observed if isinstance(observed, SkippedPatchPath) else
                        self.observation_state.expected(observed, results)
                    )
                    _recheck(expected)

    def _publish(
        self, expected, content=None, *, source=None,
        auxiliary=False, desired_digest=None,
    ):
        """输入和辅助归属归业务；工作现场及步骤由共同状态拥有。"""
        execution = FixedContentExecution(
            expected, content, source=source, desired_digest=desired_digest,
        )
        stage = lambda target, phase: self._stage(self.role, target, phase)
        if auxiliary:
            return execution.execute(
                recheck_context=self.recheck,
                record_step=self.auxiliary_results.append, stage=stage,
            )
        state = self.task_execution.state
        return state.execute_active(
            execution,
            lambda prepared: prepared.execute(
                recheck_context=self.recheck,
                record_step=state.record_step, stage=stage,
            ),
            stage=stage,
        )

    def perform_action(self, unit, prepared_bytes):
        """返回正式算法核验的最终约束，不接受后来对象作为新授权。"""
        action = unit.members[0]
        kind = action.payload["op"]
        results = self.results
        counts = self.observation_state.removed_counts(results)
        fixed = tuple(
            self.observation_state.project(observed, results)
            for observed in action.expectations
        )
        source = fixed[0]
        self._active_unit = unit
        try:
            if unit.text_file is not None:
                if not isinstance(prepared_bytes, bytes):
                    raise RuntimeError("活动文本任务缺少完整不可变成果")
                self._stage("工作目标", source.path, "文本文件发布")
                return (self._publish(
                    source, prepared_bytes,
                    desired_digest=unit.fixed.effects[0].after.records[0][2],
                ),)
            if kind == "copy_file":
                self._stage("工作目标", fixed[1].path, "固定来源内容复制")
                return (self._publish(
                    fixed[1], source=source,
                    desired_digest=unit.fixed.effects[0].after.records[0][2],
                ),)
            if kind == "copy_dir":
                target = fixed[1]
                if isinstance(target, TreeObservation):
                    self.task_execution.state.execute_active(
                        prepare_tree_removal(target), execute_tree_removal,
                        stage=lambda target, phase:
                        self._stage("工作目标", target, phase),
                        before_step=self.recheck,
                    )
                source = project_removed_file_links(
                    action.expectations[0],
                    self.observation_state.removed_counts(self.results),
                )
                self._stage("工作目标", target.path, "固定独立目标树重建")
                copy = prepare_tree_copy(source, _absent(target))
                self.task_execution.state.execute_active(
                    copy, execute_tree_copy,
                    stage=lambda target, phase:
                    self._stage("工作目标", target, phase),
                    recheck_context=self.recheck,
                )
                actual = copy.final_observation
                if not isinstance(actual, TreeObservation):
                    raise RuntimeError("正常树复制缺少最终成果观察")
                return (actual,)
            if kind in ("move_file", "rename_file", "move_dir", "rename_dir"):
                move = action.name_move
                self._stage("工作目标", fixed[1].path, "固定名称机制执行")
                self.task_execution.state.execute_active(
                    move, execute_observed_name_move,
                    stage=lambda target, phase:
                    self._stage("工作目标", target, phase),
                    removed_link_counts=counts,
                )
                if len(move.final_observations) != 2:
                    raise RuntimeError("正常名称核心缺少完整最终两端约束")
                return move.final_observations
            if kind == "delete_file":
                self.task_execution.state.execute_step(
                    prepare_file_removal(source),
                    stage=lambda target, phase:
                    self._stage("工作目标", target, phase),
                )
                result = _absent(source)
                recheck_file(result)
                return (result,)
            if kind == "delete_dir":
                self.task_execution.state.execute_active(
                    prepare_tree_removal(source), execute_tree_removal,
                    stage=lambda target, phase:
                    self._stage("工作目标", target, phase),
                    before_step=self.recheck,
                )
                result = _absent(source)
                recheck_file(result)
                return (result,)
            if kind == "create_dir":
                receipt = self.task_execution.state.execute_step(
                    prepare_directory_creation(source),
                    stage=lambda target, phase:
                    self._stage("工作目标", target, phase),
                )
                self.task_execution.state.accept_created_directory(receipt)
                actual = observe_tree(source.path)
                if (
                    actual.content_records != (("", "directory", ""),)
                    or actual.directories[0][1].object_identity
                    != receipt.created_directory.object_identity
                ):
                    raise OSError("创建目录最终对象与真实创建结果不符")
                return (actual,)
            raise RuntimeError("活动单元包含未知正式动作")
        finally:
            self._active_unit = None

    def _accept_unit_results(self, unit, actuals):
        """只登记共用内核已核验的成果及正常共享状态。"""
        self.observation_state.accept(actuals, self.results)
        self._stage(
            "工作目标", unit.members[0].expectations[0].path,
            "动作完成后全部固定范围复核",
        )
        self.recheck()

    def write_execution_log_file(self):
        if self.protection is None or not self.protection.directory_results:
            return
        outputs = [(
            self.backup_root / "执行日志.txt",
            (self.preview_text + "\n\n" + "\n".join(self.logs) + "\n").encode("utf-8"),
        )]
        if self.patch_text:
            outputs.append((
                self.backup_root / "修改包原文.txt", self.patch_text.encode("utf-8"),
            ))
        for target, content in outputs:
            self._stage("辅助输出", target, "辅助目标固定观察")
            expected = observe_file(target, include_digest=True)
            if expected.kind != "absent":
                raise ValueError("本任务辅助输出目标不再不存在")
            self._publish(expected, content, auxiliary=True)

    def failure_report(self, error):
        lines = [
            f"修改包执行未完整结束：角色={self.role}；位置={self.current}；阶段={self.stage}。",
            f"全部工作及最终核验完成：{self.work_completed}。",
            f"已确认工作正文：{len(self.confirmed_content_results)} 个。",
            "【单元及共同成员】",
        ]
        for result in self.unit_results.values():
            lines.append(
                f"- {'; '.join(result.unit.labels)}；状态={result.status}；"
                f"工作完成={result.work_completed}；阶段={result.stage}"
            )
        lines.append("【实际工作步骤】")
        for receipt in self.results:
            if isinstance(receipt, ContentPublicationResult):
                lines.extend((
                    f"- {receipt.target}；机制={receipt.mechanism}；阶段={receipt.phase}；"
                    f"改变发起={receipt.mutation_attempted}；替换={receipt.replacement}",
                    f"  正文确认={receipt.content_completed}；必要确认={receipt.confirmation_completed}；"
                    f"应急={receipt.emergency_state}；位置={receipt.emergency_path}",
                ))
            else:
                lines.append(
                    f"- {receipt.target}；改变发起={receipt.attempted}；"
                    f"正常完成={receipt.succeeded}；阶段={receipt.phase}"
                )
                if isinstance(receipt, NativeNameMoveResult):
                    lines.append(f"  来源：{receipt.source}")
        if not self.results:
            lines.append("没有正式工作步骤记录；不能据此证明整个磁盘未变。")
        lines.append("【独立辅助输出】")
        lines.extend(
            f"- {receipt.target}；正文确认={receipt.content_completed}；"
            f"替换={receipt.replacement}；正常完成={receipt.succeeded}"
            for receipt in self.auxiliary_results
        )
        if self.fact_error is not None:
            lines.append(f"不确定事实补充也未保存：{self.fact_error}")
        lines.extend((
            f"备份目录：{self.backup_root or '未启用或无需保护'}",
            "保护材料、清单、工作目标及辅助输出分别判断。",
            "正文确认不等于整体工作正常完成；没有替换不代表没有原地改变。",
            "unknown不认证正文；前序真实步骤不自动撤销。",
            "旧任务永久失效；备份清理和自动回滚结论单独记录，不自动续执行。",
            "完整原备份可以通过严格version 4主动恢复入口再次检查和恢复。",
            str(error),
        ))
        return "\n".join(lines)

    def release_prepared_contents(self):
        """释放任务持有的正文；不承诺擦除保护、调用方或traceback副本。"""
        actions = {}
        if self.plan is not None:
            for action in self.plan.actions:
                actions[id(action)] = replace(
                    action,
                    expectations=tuple(
                        _release_observation(member) for member in action.expectations
                    ),
                    name_move=None,
                )
            self.plan = replace(
                self.plan,
                actions=tuple(actions[id(action)] for action in self.plan.actions),
            )
        self.execution_units = tuple(
            replace(
                unit,
                members=tuple(actions.get(id(member), member) for member in unit.members),
                text_file=(
                    replace(unit.text_file, content=None)
                    if unit.text_file is not None else None
                ),
                fixed=None,
            ) for unit in self.execution_units
        )

        self.observation_state.release_observations()
        self.projection = None

    def invalidate(self):
        if not self.task_execution.invalidate():
            return False
        self.release_prepared_contents()
        return True

    def apply(self):
        if self.consumed:
            raise ValueError("本次计划已经执行或失败，或已取消作废，必须重新预演")
        if self.plan is None:
            raise ValueError("没有通过正式预演的执行计划")
        try:
            return self._execute_plan()
        except PatchExecutionError as failure:
            self._handle_failure(failure)
            raise
        finally:
            self.release_prepared_contents()


    def _handle_failure(self, failure):
        if self.work_completed:
            return
        attempted = any(
            receipt.mutation_attempted
            if isinstance(receipt, ContentPublicationResult)
            else receipt.attempted
            for receipt in failure.results
        )
        if self.protection is not None and not self.backup_completed:
            if attempted:
                raise RuntimeError("全部备份屏障之前出现工作改变")
            failure.rollback_status = (
                "backup_failed"
                if self.protection.backup_cleanup_report else "not_required"
            )
            failure.rollback_error = self.protection.backup_cleanup_error
            failure.rollback_report = (
                self.protection.backup_cleanup_report
                or "执行开始前检查失败，尚未启动备份或工作修改，不需要回滚。"
            )
            return
        if not attempted:
            failure.rollback_status = "not_required"
            failure.rollback_report = (
                "未发起工作目标改变，不需要回滚；完整原备份保留。"
            )
            return
        if self.protection is None:
            raise RuntimeError("工作改变缺少强制备份协调，不能启动回滚")
        from core.recovery_execution import RecoveryExecution
        from core.recovery_protection import read_protection_evidence
        from services.recovery import plan_restore

        try:
            # 自动恢复建立新观察，消费同一个恢复计划及核心；
            # 不进入人类主动恢复的强制二次备份服务。
            evidence = read_protection_evidence(self.protection)
            restore_plan = plan_restore(evidence)
            from core.automatic_rollback_scope import validate_automatic_rollback_scope
            validate_automatic_rollback_scope(
                self.protection.projection, restore_plan.actions,
                results=failure.results,
            )
            recovery = RecoveryExecution(
                evidence, restore_plan.actions,
            )
            result = recovery.execute()
        except (OSError, ValueError) as rollback_error:
            failure.rollback_status = "failed"
            failure.rollback_error = rollback_error
            failure.rollback_report = (
                "修改失败，回滚未完整完成，不能认定项目已经恢复。\n"
                "原备份保留；请人工处理故障后主动恢复，不自动重试。\n"
                f"原备份：{self.backup_root}\n\n{rollback_error}"
            )
        else:
            failure.rollback_status = "completed"
            failure.rollback_report = (
                "修改失败，已回滚，全部原状态已通过最终内容检查。\n"
                "没有生成二次备份，没有自动再次执行修改包。\n"
                f"实际恢复对象：{len(result.completed_units)}；"
                f"跳过未变对象：{len(result.skipped_paths)}。\n"
                f"原备份保留：{self.backup_root}"
            )
            if result.rebuilt_files:
                failure.rollback_report += (
                    "\n以下名称已独立重建，不重建原硬链接关系：\n"
                    + "\n".join(map(str, result.rebuilt_files))
                )
        self.log(failure.rollback_report)

    def _execute_plan(self):
        projected_units = {
            id(unit.fixed): unit for unit in self.projection.units
        }

        def prepare_entry(unit):
            target = unit.members[0].expectations[0].path
            self._stage("工作目标", target, "动作开始前全部固定事实复核")
            self.recheck()
            if not unit.execute:
                return None
            projected = projected_units.get(id(unit.fixed))
            if projected is None or projected.fixed is not unit.fixed:
                raise RuntimeError("执行单元与唯一保护投影关联不一致")
            content = unit.text_file.content if unit.text_file is not None else None

            def accept_completed(actuals):
                self._accept_unit_results(unit, actuals)


            return FixedTaskWork(
                projected,
                lambda: self.perform_action(unit, content),
                accept_completed, self.recheck,
            )

        def entry_completed(unit, executed):
            self._stage(
                "运行记录", unit.members[0].expectations[0].path,
                "完成日志" if executed else "固定跳过日志",
            )
            self.log(
                f"[{'完成' if executed else '跳过'}] {'; '.join(unit.labels)}"
            )


        try:
            self.log("【开始执行修改包】")
            self.log(f"协议版本：{PATCH_PROTOCOL_DESCRIPTION}")
            self.log(f"项目根目录：{self.root}")
            self.log(f"备份目录：{self.backup_root or '未启用或无需保护'}")
            self.task_execution.execute(
                self.execution_units, protection=self.protection,
                initial_recheck=self.recheck, prepare_entry=prepare_entry,
                stage=self._stage, entry_key=lambda unit: unit.index,
                entry_completed=entry_completed,
                final_check=self.recheck, auxiliary=self.write_execution_log_file,
                after_auxiliary_check=self.recheck, task_target=self.root,
            )
            return "\n".join(self.logs)
        except (OSError, ValueError) as error:
            self.fact_error = self.task_execution.fact_error
            raise PatchExecutionError(self, error) from error
