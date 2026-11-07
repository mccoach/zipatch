# -*- coding: utf-8 -*-

"""修改包完整准备、13类OP共享执行及严格version 3事实。

正文复用已有定位和组合；观察、保护及文件系统变更
直接消费正式核心。固定机制、一次资格、失败停止。
"""

import re
import stat
import sys
import uuid
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from core.constants import PATCH_PROTOCOL_DESCRIPTION, SUPPORTED_PATCH_OPS
from core.content_publication import (
    ContentPublicationError, ContentPublicationResult,
    dispose_content_artifact, prepare_content_artifact,
    prepare_copy_content_artifact, publish_content,
)
from core.file_observation import (
    FileObservation, ObjectIdentity, TreeObservation,
    observe_file, observe_tree, project_removed_file_links,
    recheck_file, recheck_tree,
)
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult,
    PreparedTreeCopy, PreparedTreeRemoval, _recheck_tree_copy_target,
    execute_filesystem_change, execute_tree_copy, execute_tree_removal,
    prepare_directory_creation, prepare_file_removal,
    prepare_tree_copy, prepare_tree_removal,
)
from core.name_moves import (
    NativeNameMoveResult, PreparedNameMove,
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


def _active_results(active):
    if active is None:
        return ()
    if isinstance(active, PreparedTreeCopy):
        return tuple(receipt.change for receipt in active.results)
    if isinstance(active, (PreparedTreeRemoval, PreparedNameMove)):
        return active.results
    raise TypeError("活动修改包组合必须是正式共享核心")


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
        super().__init__(task.failure_report(error))


class PatchExecutor:
    def __init__(
        self, output_rules, root: Path, allow_delete=False,
        allow_multi_replace_exact=False, backup_enabled=True,
        backup_dir="99_归档/AI文件修改备份",
        patch_text="", preview_text="", normalize_text_newlines=True,
    ):
        for name, value in (
            ("允许删除", allow_delete),
            ("允许多处精确替换", allow_multi_replace_exact),
            ("自动备份", backup_enabled),
            ("文本换行标准化", normalize_text_newlines),
        ):
            if type(value) is not bool:
                raise TypeError(f"{name}开关必须是布尔值")
        if normalize_text_newlines:
            validate_file_rules(output_rules)
        self.root = checked_root(root)
        self.allow_delete = allow_delete
        self.allow_multi_replace_exact = allow_multi_replace_exact
        self.backup_enabled = backup_enabled
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
        self.unit_results = {}
        self.source_snapshots = {}
        self.located_operations = []
        self._observations = {}
        self._actions = []
        self._original_files = {}
        self._finished = {}
        self._created = {}
        self._results = ()
        self._active = None
        self._active_unit = None
        self._artifact = None
        self._scene = None
        self.logs = []
        self.auxiliary_results = []
        self.fact_error = None
        self.current = None
        self.stage = "尚未执行"
        self.role = "任务准备"
        self.work_completed = False
        self.previewed = False
        self.consumed = False

    @property
    def results(self):
        return (*self._results, *_active_results(self._active))

    @property
    def active_change(self):
        return self._active or self._artifact or self._scene

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
    def created_directories(self):
        return MappingProxyType(self._created)

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
        self.unit_results.clear()
        self._original_files.clear()

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
                    self.unit_results = {
                        unit.index: PatchUnitResult(unit)
                        for unit in self.execution_units
                    }
                    phase = "保护规划或材料隔离"
                    self.projection = build_protection_projection(
                        (unit.fixed for unit in self.execution_units if unit.execute),
                        forbidden_roots=(self.root,),
                    )
                    if self.backup_enabled and self.projection.units:
                        task_id = uuid.uuid4().hex
                        self.backup_root = (
                            resolve_backup_base_dir(self.root, self.backup_dir)
                            / f"{now_stamp()}_执行修改_{task_id}"
                        )
                        self.protection = RecoveryProtectionSession(
                            self.projection, self.backup_root, task_id, "patch",
                        )
                    for action in self.plan.actions:
                        for observed in action.expectations:
                            for _, member in _files(observed):
                                key = identity(member.path)
                                previous = self._original_files.setdefault(key, member)
                                if previous != member:
                                    raise ValueError("同一文件最初观察矛盾")
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
                self.backup_enabled, self.backup_root,
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
                "\n正式保护及工作事实使用严格version 3。"
                "\n名称机制准备时固定；原生报错停止，不切换复制删除。"
                "\n正文确认、工作完成、保护和辅助输出分别表达。"
                "\n这些检查不是文件锁；失败停止，不自动重试或回滚。"
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

    def _removed_links(self):
        """只消费正常名称移除；来源名称迁移不减少来源对象链接。"""
        counts = {}
        for receipt in self.results:
            removed = None
            if (
                isinstance(receipt, FilesystemChangeResult)
                and receipt.succeeded and receipt.kind == "remove_file"
            ):
                removed = self._original_files.get(identity(receipt.target))
            elif (
                isinstance(receipt, ContentPublicationResult)
                and receipt.succeeded and receipt.mechanism == "atomic_replace"
            ):
                removed = self._original_files.get(identity(receipt.target))
            elif isinstance(receipt, NativeNameMoveResult) and receipt.succeeded:
                removed = self._original_files.get(identity(receipt.target))
            if removed is not None:
                identifier = removed.object_identity
                counts[identifier] = counts.get(identifier, 0) + 1
        return counts

    def _parents(self, observed):
        _, current = checked_chain(
            observed.path.parent, include_observation=True,
        )
        initial = dict(observed.chain[:-1])
        for path, kind in current:
            if kind == initial[path]:
                continue
            if (
                initial[path] == "absent" and kind == "directory"
                and identity(path) in self._created
            ):
                continue
            raise ValueError(f"必要父目录偏离固定计划：{path}")

    def recheck(self):
        """所属新边界比较；活动单元自身由正式子核心维护。"""
        checked_root(self.root)
        for directory in self._created.values():
            target, chain = checked_chain(
                directory.path, include_observation=True,
            )
            if chain[-1][1] != "directory":
                raise ValueError(f"已创建目录存在性或类型变化：{target}")
            attributes = target.lstat()
            if ObjectIdentity(attributes.st_dev, attributes.st_ino) != directory.object_identity:
                raise ValueError(f"已创建目录身份变化：{target}")
        counts = self._removed_links()
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
                    self._parents(observed)
                    finished = self._finished.get(key)
                    if finished is None:
                        expected = (
                            observed if isinstance(observed, SkippedPatchPath) else
                            project_removed_file_links(observed, counts)
                        )
                    else:
                        actual, baseline = finished
                        delta = {
                            identifier: number - baseline.get(identifier, 0)
                            for identifier, number in counts.items()
                            if number > baseline.get(identifier, 0)
                        }
                        expected = project_removed_file_links(actual, delta)
                    _recheck(expected)

    def _append(self, receipt):
        self._results = (*self._results, receipt)

    def _step(self, step):
        self._scene = step
        self._stage("工作目标", step.expected.path, "固定名称步骤执行")
        try:
            receipt = execute_filesystem_change(step)
        except FilesystemChangeError as error:
            self._append(error.result)
            self._stage("工作目标", error.result.target, error.result.phase)
            raise
        self._append(receipt)
        self._scene = None
        return receipt

    def _composite(self, prepared, executor, **keywords):
        self._active = prepared
        returned = False
        try:
            result = executor(prepared, **keywords)
            returned = True
            return result
        finally:
            self._results = (*self._results, *_active_results(prepared))
            self._active = None
            if not returned:
                self._scene = prepared
                self._stage("工作目标", prepared.current, prepared.phase)

    def _dispose(self, artifact):
        if artifact._lifecycle in ("completed", "disposed"):
            return
        if artifact.mechanism == "in_place" and artifact._lifecycle == "attempted":
            return
        try:
            dispose_content_artifact(artifact)
        except (OSError, ValueError) as error:
            print(
                f"[修改包自有成果清理失败] {artifact.path}：{error}",
                file=sys.stderr,
            )

    def _publish(
        self, expected, content=None, *, source=None,
        auxiliary=False, desired_digest=None,
    ):
        artifact = None
        self._stage(self.role, expected.path, "完整固定成果准备")
        try:
            artifact = (
                prepare_content_artifact(expected, content)
                if source is None else
                prepare_copy_content_artifact(source, expected)
            )
            self._artifact = artifact
            if desired_digest is not None and artifact.digest != desired_digest:
                raise ValueError("完整成果不对应固定具体预期")
            self.recheck()
            recheck_file(expected)
            if source is not None:
                recheck_file(source, check_mtime=True)
            self._stage(self.role, expected.path, "固定完整成果发布")
            try:
                receipt = publish_content(artifact)
            except ContentPublicationError as error:
                if auxiliary:
                    self.auxiliary_results.append(error.result)
                else:
                    self._append(error.result)
                self._stage(self.role, error.result.target, error.result.phase)
                raise
            if auxiliary:
                self.auxiliary_results.append(receipt)
            else:
                self._append(receipt)
            self._stage(self.role, expected.path, "正式成果身份内容及属性核验")
            actual = observe_file(
                expected.path, include_digest=True, include_mtime=True,
            )
            identifier = (
                expected.object_identity if artifact.mechanism == "in_place"
                else ObjectIdentity(*artifact.identity)
            )
            links = expected.link_count if artifact.mechanism == "in_place" else 1
            mode = artifact.options.file_mode
            if mode is None and expected.kind == "file":
                mode = stat.S_IMODE(expected.attributes.st_mode)
            if (
                actual.kind != "file"
                or actual.object_identity != identifier
                or actual.link_count != links
                or actual.digest != artifact.digest
                or artifact.options.mtime_ns is not None
                and actual.mtime_ns != artifact.options.mtime_ns
                or mode is not None
                and stat.S_IMODE(actual.attributes.st_mode) != mode
            ):
                raise OSError("正式成果身份、链接、内容或适用属性不符")
            if source is not None:
                recheck_file(source, check_mtime=True)
            return actual
        finally:
            if artifact is not None:
                self._dispose(artifact)
                if artifact._lifecycle in ("completed", "disposed"):
                    self._artifact = None

    def _create_parents(self, projected, unit):
        for target in projected.parent_directories:
            self._stage("工作目标", target, "固定必要父目录创建或正常结果复用")
            self.recheck()
            if identity(target) in self._created:
                continue
            before = next(
                effect.before for effect in unit.fixed.effects
                if dict(effect.before.chain).get(target) == "absent"
            )
            index = next(
                number for number, (path, _) in enumerate(before.chain)
                if path == target
            )
            expected = FileObservation(
                target, before.chain[:index + 1], "absent",
                None, None, None, None, None, None,
            )
            receipt = self._step(prepare_directory_creation(expected))
            self._created[identity(target)] = receipt.created_directory

    def perform_action(self, unit, prepared_bytes):
        """返回正式算法核验的最终约束，不接受后来对象作为新授权。"""
        action = unit.members[0]
        kind = action.payload["op"]
        counts = self._removed_links()
        fixed = tuple(
            project_removed_file_links(observed, counts)
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
                    self._composite(
                        prepare_tree_removal(target), execute_tree_removal,
                        before_step=self.recheck,
                    )
                source = project_removed_file_links(
                    action.expectations[0], self._removed_links(),
                )
                self._stage("工作目标", target.path, "固定独立目标树重建")
                copy = prepare_tree_copy(source, _absent(target))
                self._composite(
                    copy, execute_tree_copy, recheck_context=self.recheck,
                )
                return (_recheck_tree_copy_target(copy),)
            if kind in ("move_file", "rename_file", "move_dir", "rename_dir"):
                move = action.name_move
                self._stage("工作目标", fixed[1].path, "固定名称机制执行")
                self._composite(
                    move, execute_observed_name_move,
                    removed_link_counts=counts,
                )
                if len(move.final_observations) != 2:
                    raise RuntimeError("正常名称核心缺少完整最终两端约束")
                return move.final_observations
            if kind == "delete_file":
                self._step(prepare_file_removal(source))
                result = _absent(source)
                recheck_file(result)
                return (result,)
            if kind == "delete_dir":
                self._composite(
                    prepare_tree_removal(source), execute_tree_removal,
                    before_step=self.recheck,
                )
                result = _absent(source)
                recheck_file(result)
                return (result,)
            if kind == "create_dir":
                receipt = self._step(prepare_directory_creation(source))
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

    def _verify_unit(self, unit, actuals):
        if not isinstance(actuals, tuple) or len(actuals) != len(unit.fixed.effects):
            raise RuntimeError("正式动作没有返回完整最终对象约束")
        counts = self._removed_links()
        finished = {}
        for effect, actual in zip(unit.fixed.effects, actuals):
            self._stage("工作目标", effect.before.path, "具体effect最终内容结构核验")
            if actual.path != effect.before.path:
                raise RuntimeError("动作最终位置与固定effect不符")
            kind = patch_observation_kind(actual)
            records = (
                actual.content_records if isinstance(actual, TreeObservation) else
                (("", "file", actual.digest),) if actual.kind == "file" else ()
            )
            if kind != effect.after.kind or records != effect.after.records:
                raise OSError(f"最终内容或结构不对应固定预期：{actual.path}")
            _recheck(actual)
            finished[identity(actual.path)] = (actual, dict(counts))
        self._finished.update(finished)
        self._stage("工作目标", unit.members[0].expectations[0].path, "动作完成后全部固定范围复核")
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
            "旧任务永久失效，不自动重试、回滚或删除正式材料。",
            "可通过严格version 3正式恢复入口预检和恢复；"
            "当前开发候选仍须完成必要验证，不直接用于真实项目业务。",
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
        for unit in self.execution_units:
            self.unit_results[unit.index] = replace(
                self.unit_results[unit.index], unit=unit,
            )
        self._original_files.clear()
        self._finished.clear()
        self.projection = None

    def invalidate(self):
        if self.consumed:
            return False
        self.consumed = True
        self.release_prepared_contents()
        return True

    def apply(self):
        if self.consumed:
            raise ValueError("本次计划已经执行或失败，或已取消作废，必须重新预演")
        if self.plan is None:
            raise ValueError("没有通过正式预演的执行计划")
        self.consumed = True
        try:
            return self._execute_plan()
        finally:
            self.release_prepared_contents()

    def _execute_plan(self):
        active_unit = projected = None
        unit_start = 0
        try:
            self._stage("执行前全量关口", self.root, "执行开始前全量固定事实复核")
            self.recheck()
            self.log("【开始执行修改包】")
            self.log(f"协议版本：{PATCH_PROTOCOL_DESCRIPTION}")
            self.log(f"项目根目录：{self.root}")
            self.log(f"备份目录：{self.backup_root or '未启用或无需保护'}")
            projections = iter(self.projection.units)
            for active_unit in self.execution_units:
                unit_start = len(self._results)
                self._stage("工作目标", active_unit.members[0].expectations[0].path, "动作开始前全部固定事实复核")
                self.unit_results[active_unit.index] = replace(
                    self.unit_results[active_unit.index],
                    status="running", stage=self.stage,
                )
                self.recheck()
                if not active_unit.execute:
                    self.unit_results[active_unit.index] = replace(
                        self.unit_results[active_unit.index],
                        status="skipped", stage="固定跳过",
                    )
                    self.log(f"[跳过] {'; '.join(active_unit.labels)}")
                    active_unit = None
                    continue
                projected = next(projections)
                if projected.fixed is not active_unit.fixed:
                    raise RuntimeError("执行单元与唯一保护投影关联不一致")
                if self.protection is not None:
                    self._stage("正式保护", self.backup_root, "修改前保护及清单发布")
                    self.protection.protect(
                        projected.unit_id, removed_link_counts=self._removed_links(),
                    )
                    self._stage("工作目标", active_unit.members[0].expectations[0].path, "保护后全部固定事实复核")
                    self.recheck()
                    self._stage("正式保护", self.backup_root, "工作改变前started屏障")
                    self.protection.begin(projected.unit_id)
                self._create_parents(projected, active_unit)
                self.recheck()
                content = (
                    active_unit.text_file.content
                    if active_unit.text_file is not None else None
                )
                actuals = self.perform_action(active_unit, content)
                self._verify_unit(active_unit, actuals)
                self.unit_results[active_unit.index] = replace(
                    self.unit_results[active_unit.index],
                    status="completed", results=self._results[unit_start:],
                    stage="工作及最终核验完成", work_completed=True,
                )
                if self.protection is not None:
                    self._stage("正式保护", self.backup_root, "工作完成事实发布")
                    self.protection.complete(
                        projected.unit_id, self._results[unit_start:],
                    )
                self._stage("运行记录", active_unit.members[0].expectations[0].path, "完成日志")
                self.log(f"[完成] {'; '.join(active_unit.labels)}")
                active_unit = projected = None
            self._stage("整任务", self.root, "全部工作最终联合复核")
            self.recheck()
            self.work_completed = True
            self.write_execution_log_file()
            self._stage("整任务", self.root, "辅助输出后最终联合复核")
            self.recheck()
            self._stage("整任务", None, "completed")
            return "\n".join(self.logs)
        except (OSError, ValueError) as error:
            if active_unit is not None:
                current = self.unit_results[active_unit.index]
                self.unit_results[active_unit.index] = replace(
                    current, status="failed", stage=self.stage, error=error,
                    results=self._results[unit_start:],
                )
            if (
                projected is not None and self.protection is not None
                and not self.protection.failed
                and self.protection.document is not None
                and any(
                    unit["id"] == projected.unit_id
                    and unit["fact"]["state"] == "started"
                    for unit in self.protection.document.value["units"]
                )
            ):
                try:
                    self.protection.uncertain(
                        projected.unit_id, self._results[unit_start:],
                    )
                except (OSError, ValueError) as fact_error:
                    self.fact_error = fact_error
            raise PatchExecutionError(self, error) from error
