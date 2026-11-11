# -*- coding: utf-8 -*-

"""拆分的完整准备、固定跳过、一次确认及共享version 4保护。

路径解释、占位分类和正文编码归本业务。
观察、必要目录、完整成果、发布和正式保护直接消费共享核心。
失败停止，不重新解释任务，不自动重试或回滚。
"""

import os
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from core.content_publication import (
    ContentPublicationError, ContentPublicationResult,
    dispose_content_artifact, prepare_content_artifact, publish_content,
)
from core.constants import (
    MESSAGE_FILE_EXCLUDED, MESSAGE_FILE_FAILED, SKIPPED_LOG_FILENAME,
)
from core.file_observation import (
    DirectoryObservation, FileObservation, ObjectIdentity,
    observe_file, recheck_file,
)
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult,
    execute_filesystem_change, prepare_directory_creation,
)
from core.merged_format import (
    classify_placeholder, parse_file_blocks, validate_markers,
    validate_placeholders,
)
from core.path_validation import strip_outer_quotes

from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)
from core.recovery_paths import (
    UnsafePathError, absolute_path, checked_chain, contains, identity, overlaps,
)
from core.recovery_protection import RecoveryProtectionSession
from core.rule_lists import validate_file_rules

from core.text_newlines import normalize_newlines
from core.time_utils import now_stamp


class RestoreScopeError(ValueError):
    """范围、穿越或缺少明确基准：整份任务拒绝。"""


class UnsupportedRestorePath(ValueError):
    """明确识别的目标路径语法不受本次拆分支持。"""


@dataclass(frozen=True)
class RestoreFile:
    source_path: str
    expected: FileObservation
    content: bytes | None

    @property
    def target(self):
        return self.expected.path

    @property
    def kind(self):
        return self.expected.kind


@dataclass(frozen=True)
class SkippedTarget:
    """固定正常原链，或到达实际安全阻塞位置的原链。"""

    target: Path
    observation: tuple
    blocked: bool = False


@dataclass(frozen=True)
class RestorePlan:
    source_description: str
    mode: str
    base: Path | None
    files: tuple
    skipped: tuple
    skip_targets: tuple
    ignored_count: int
    source_failure_count: int
    log_expected: FileObservation | None
    log_content: bytes | None
    backup_enabled: bool
    backup_dir: str
    backup_root: Path | None
    projection: object


    @property
    def missing_directories(self):
        directories = {}
        for unit in self.projection.units:
            for path in unit.parent_directories:
                directories.setdefault(identity(path), path)
        return tuple(directories.values())

    @property
    def log_path(self):
        return None if self.log_expected is None else self.log_expected.path


@dataclass(frozen=True)
class RestoreResult:
    target_folder: str
    restored_count: int
    skipped_count: int
    log_path: str | None
    failed_count: int
    ignored_count: int = 0
    source_failure_count: int = 0
    cancelled: bool = False
    created_directories: tuple = ()
    backup_enabled: bool = False
    backup_root: str = ""
    results: tuple = ()
    completed_files: tuple = ()
    completed: bool = False
    current: Path | None = None
    phase: str = ""
    fact_error: OSError | ValueError | None = None
    error: OSError | ValueError | None = None
    log_result: ContentPublicationResult | None = None


class RestoreExecutionError(OSError):

    """所属工作和附属事实分别持有；发布字符串不是完成判据。"""

    def __init__(self, result, task):
        self.result = result
        self.task = task
        lines = [
            f"拆分还原在{result.phase}阶段中断：{result.current}",
            f"已有 {result.restored_count} 个还原文件正文确认。",
            f"工作及必要最终核验正常完成文件：{len(result.completed_files)} 个。",
            "已确认正文：",
        ]
        lines.extend(str(receipt.target) for receipt in task.confirmed_content_results)
        if not task.confirmed_content_results:
            lines.append("无")
        lines.append("实际工作步骤：")
        for receipt in result.results:
            if isinstance(receipt, ContentPublicationResult):
                lines.extend((
                    f"- {receipt.target}；机制={receipt.mechanism}；"
                    f"阶段={receipt.phase}；改变已发起={receipt.mutation_attempted}；"
                    f"名称替换={receipt.replacement}",
                    f"  正文确认={receipt.content_completed}；"
                    f"必要确认={receipt.confirmation_completed}；"
                    f"应急={receipt.emergency_state}；"
                    f"位置={receipt.emergency_path or '不适用'}",
                ))
            else:
                lines.append(
                    f"- {receipt.target}；动作={receipt.kind}；"
                    f"改变已发起={receipt.attempted}；"
                    f"正常完成={receipt.succeeded}；阶段={receipt.phase}"
                )
        if not result.results:
            lines.append("无正式工作步骤记录；不据此证明磁盘没有其他改变。")
        auxiliary = result.log_result
        if auxiliary is not None:
            lines.extend((
                "辅助跳过日志的独立发布事实（不计入工作文件步骤）：",
                f"日志目标：{auxiliary.target}",
                f"发布机制：{auxiliary.mechanism}；阶段：{auxiliary.phase}",
                f"日志目标改变已发起：{auxiliary.mutation_attempted}；"
                f"名称替换：{auxiliary.replacement}",
                f"日志正文已确认：{auxiliary.content_completed}；"
                f"必要确认已完成：{auxiliary.confirmation_completed}",
                f"日志应急状态：{auxiliary.emergency_state}；"
                f"应急位置：{auxiliary.emergency_path or '不适用'}",
                "日志发布失败不表示工作文件发布失败；"
                "日志正常发布也不表示整个拆分任务已经正常结束。",
            ))
        lines.append("尚未正常完成的文件：")

        pending = [
            member.target for member in task.plan.files
            if member.target not in result.completed_files
        ]
        lines.extend(map(str, pending))
        if not pending:
            lines.append("无")
        lines.extend((
            f"主动忽略 {result.ignored_count} 个；"
            f"来源被动失败 {result.source_failure_count} 个；"
            f"全部跳过 {result.skipped_count} 个。",
            f"备份目录：{result.backup_root or '未启动或未启用'}",
            "工作目标、保护材料、恢复清单和辅助日志分别判断。",
            "没有名称替换不代表没有原地改变；unknown不认证正文完成。",
            "前序文件和已创建目录不自动撤销；旧任务永久失效。",
            "只有严格预检通过的version 4清单及引用材料提供恢复依据。",
            "不自动重试、回滚、删除正式材料或重新拆分。",
            str(result.error),
        ))
        if result.fact_error is not None:
            lines.append(f"不确定事实补充也未保存：{result.fact_error}")
        super().__init__("\n".join(lines))


def _native_path(expression):
    """不猜盘符、网络路径或当前目录；语法拒绝与系统故障分开。"""
    if not isinstance(expression, str) or not expression:
        raise UnsupportedRestorePath("路径必须是非空字符串")
    if any(ord(character) < 32 for character in expression):
        raise UnsupportedRestorePath(f"路径包含控制字符：{expression!r}")
    normalized = expression.replace("\\", "/")
    if normalized.startswith("//"):
        raise UnsupportedRestorePath(f"不支持网络或设备路径：{expression}")
    if os.name == "nt" and normalized.startswith("/"):
        raise UnsupportedRestorePath(f"路径不是完整盘符绝对路径：{expression}")
    if len(normalized) >= 2 and normalized[1] == ":":
        if (
            normalized[0] not in
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            or len(normalized) < 3 or normalized[2] != "/"
            or os.name != "nt"
        ):
            raise UnsupportedRestorePath(
                f"不完整或当前平台不可定位的盘符路径：{expression}"
            )
    elif ":" in normalized:
        raise UnsupportedRestorePath(f"不支持的冒号或备用数据流：{expression}")
    if ".." in normalized.split("/"):
        raise RestoreScopeError(
            f"路径穿越拒绝：{expression}；整份任务未开始写入"
        )
    return Path(normalized.replace("/", os.sep))


def _target_path(expression, mode, base):
    """指定目录按盘符归档，原位保留绝对位置；只解释一次目标。"""
    path = _native_path(expression)
    if mode == "directory":
        if base is None:
            raise RestoreScopeError("指定目录模式缺少明确绝对指定目录")
        if path.is_absolute():
            drive = path.drive
            if (
                len(drive) != 2
                or drive[1] != ":"
                or drive[0] not in
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            ):
                raise UnsupportedRestorePath(
                    f"指定目录模式只支持完整盘符绝对路径：{expression}"
                )
            # 盘符根由一级字母目录承载；原盘符下的层级原样保留。
            path = base / drive[0].upper() / Path(*path.parts[1:])
        else:
            path = base / path
        if not contains(base, path) or identity(base) == identity(path):
            raise RestoreScopeError(
                f"指定目录范围拒绝：{expression}\n"
                f"实际目标：{absolute_path(path)}\n指定目录：{base}\n"
                "整份任务未开始写入。"
            )
    elif not path.is_absolute():
        if base is None:
            raise RestoreScopeError(
                f"相对路径缺少明确绝对指定目录：{expression}；"
                "整份任务未开始写入"
            )
        path = base / path
    return path


def _check_conflicts(files, log_path):
    for index, member in enumerate(files):
        for previous in files[:index]:
            if overlaps(previous.target, member.target):
                raise ValueError(
                    f"待写目标重复或父子冲突：{previous.target} / "
                    f"{member.target}；整份任务未开始写入"
                )
        if log_path is not None and overlaps(member.target, log_path):
            raise ValueError(
                f"待写目标与辅助日志冲突：{member.target} / "
                f"{log_path}；整份任务未开始写入"
            )


def _skipped_log_bytes(source_description, skipped_files_log):
    text = (
        f"在 {source_description} 的还原过程中，以下文件因内容被忽略、已存在或还原失败而被跳过：\n"
        + "-" * 60 + "\n"
        + "".join(entry + "\n" for entry in skipped_files_log)
    )
    return text.replace("\n", os.linesep).encode("utf-8")


def prepare_restore(
    output_rules, target_root_folder,
    code_header_line, code_footer_line, existing_file_policy, *,
    source_text, target_mode="directory", normalize_text_newlines=False,
    ignore_text=MESSAGE_FILE_EXCLUDED, failure_text=MESSAGE_FILE_FAILED,
    backup_enabled=True, backup_dir="",
):
    """一次解释输入框快照并固定全部文件成果；不读取导入文件。

    source_text由页面接受和保存后传入。任务只持有各文件成果，
    不持有供执行重新解析的全文，也不建立磁盘源码来源分支。
    """
    if type(source_text) is not str:
        raise TypeError("拆分正文必须是输入框取得的字符串")
    validate_markers(code_header_line, code_footer_line)
    validate_placeholders(ignore_text, failure_text)
    if type(target_mode) is not str or target_mode not in ("directory", "in_place"):
        raise ValueError("目标模式必须是 directory 或 in_place")
    if (
        type(existing_file_policy) is not str
        or existing_file_policy not in ("overwrite", "skip")
    ):
        raise ValueError("已有文件处理方式必须是 overwrite 或 skip")
    if type(normalize_text_newlines) is not bool:
        raise TypeError("正文标准化开关必须是布尔值")
    if normalize_text_newlines:
        validate_file_rules(output_rules)
    if type(backup_enabled) is not bool:
        raise TypeError("拆分自动备份开关必须是布尔值")
    if type(backup_dir) is not str:
        raise TypeError("拆分备份目录必须是字符串")

    selected_backup = backup_dir
    if backup_enabled:
        selected = strip_outer_quotes(backup_dir)
        if not selected:
            raise ValueError("已开启拆分自动备份，请填写有效的绝对备份目录")
        backup_path = _native_path(selected)
        if not backup_path.is_absolute():
            raise ValueError(
                "拆分备份目录必须是明确绝对路径；"
                "不会猜测指定目录、程序目录或当前工作目录基准"
            )
        backup_path, backup_chain = checked_chain(
            backup_path, include_observation=True,
        )
        if backup_chain[-1][1] not in ("absent", "directory"):
            raise ValueError("拆分备份位置必须是普通目录或可创建的缺失目录")
        selected_backup = str(backup_path)

    base = None
    if target_mode == "directory" or target_root_folder:
        selected = _native_path(os.fspath(target_root_folder))
        if not selected.is_absolute():
            raise ValueError("指定目录必须是明确的绝对路径")
        base, base_chain = checked_chain(selected, include_observation=True)
        if base_chain[-1][1] not in ("directory", "absent"):
            raise ValueError(f"指定目录不是普通目录或可创建的缺失目录：{base}")

    blocks = parse_file_blocks(source_text, code_header_line, code_footer_line)
    if not blocks:
        raise ValueError("未找到有效代码块，请检查开始和结束标记")

    files, skipped, skip_targets = [], [], []
    ignored_count = source_failure_count = 0
    for expression, body in blocks:
        placeholder = classify_placeholder(body, ignore_text, failure_text)
        if placeholder is not None:
            if placeholder == "ignored":
                ignored_count += 1
                reason = "主动忽略标识，未提供真实正文"
            else:
                source_failure_count += 1
                reason = "被动失败标识，未提供真实正文"
            skipped.append((expression, reason))
            continue

        try:
            path = _target_path(expression, target_mode, base)
        except RestoreScopeError:
            raise
        except UnsupportedRestorePath as error:
            skipped.append((expression, f"不支持的目标路径：{error}"))
            continue

        try:
            target, chain = checked_chain(path, include_observation=True)
        except UnsafePathError as error:
            if not error.observation:
                raise
            skipped.append((expression, f"目标不安全或父链阻塞：{error}"))
            skip_targets.append(SkippedTarget(
                path, error.observation, blocked=True,
            ))
            continue

        kind = chain[-1][1]
        if kind == "directory":
            skipped.append((expression, f"目标是目录，不删除覆盖：{target}"))
            skip_targets.append(SkippedTarget(target, chain))
            continue
        if kind == "file" and existing_file_policy == "skip":
            skipped.append((expression, f"目标已存在，已跳过：{target}"))
            skip_targets.append(SkippedTarget(target, chain))
            continue

        expected = observe_file(
            target, include_bytes=True, include_digest=True, include_mtime=True,
        )
        if expected.kind != kind or expected.chain != chain:
            raise ValueError(f"拆分目标准备期间前提变化：{target}")
        complete = body
        if normalize_text_newlines:
            matched = (
                output_rules.matches(target.relative_to(base).parts)
                if target_mode == "directory" else
                output_rules.matches(target.parts[1:], ignore_root=True)
            )
            complete = normalize_newlines(body, "\r\n" if matched else "\n")
        files.append(RestoreFile(expression, expected, complete.encode("utf-8")))

    files = tuple(files)
    source_description = "本次输入框正文快照（粘贴或导入）"
    backup_root = None
    if backup_enabled and files:
        task_id = uuid.uuid4().hex
        backup_root = (
            Path(selected_backup)
            / f"{now_stamp()}_代码拆分_{task_id}"
        )

    log_expected = log_content = None
    if backup_root is not None and skipped:
        # 本次任务位置先固定；目录仍由正式保护流程按计划创建。
        # 预演只观察日志不存在前提，不为日志创建任何目录。
        log_expected = observe_file(
            backup_root / SKIPPED_LOG_FILENAME, include_digest=True,
        )
        if log_expected.kind != "absent":
            raise ValueError("本次拆分任务的跳过日志目标必须不存在")
        log_content = _skipped_log_bytes(
            source_description,
            [f"{path} ({reason})" for path, reason in skipped],
        )
    log_path = None if log_expected is None else log_expected.path
    _check_conflicts(files, log_path)

    if log_expected is not None and log_expected.kind == "file":
        for member in files:
            if (
                member.expected.kind == "file"
                and log_expected.object_identity
                == member.expected.object_identity
            ):
                raise ValueError(
                    f"跳过日志与待写目标实际为同一对象："
                    f"{log_expected.path} / {member.target}；"
                    "整份任务未开始写入"
                )

    units = tuple(
        FixedUnit(
            (f"split{index:06d}",),
            (FixedEffect(member.expected, state_from_bytes(member.content)),),
        )
        for index, member in enumerate(files, 1)
    )
    projection = build_protection_projection(units, forbidden_roots=())
    return RestorePlan(
        source_description, target_mode, base,
        files, tuple(skipped), tuple(skip_targets),
        ignored_count, source_failure_count, log_expected, log_content,
        backup_enabled, selected_backup, backup_root, projection,
    )


class RestoreTask:
    """固定计划及有序真实结果；保护直接消费唯一投影。"""

    def __init__(self, plan):
        if not isinstance(plan, RestorePlan):
            raise TypeError("拆分任务必须消费RestorePlan")
        self.plan = plan
        self.consumed = False
        self.results = ()
        self.completed_files = ()
        self._finished = {}
        self._created = {}
        self._artifact = None
        self.current = None
        self.phase = "prepared"
        self.fact_error = None
        self.log_result = None
        self.protection = None
        if plan.backup_root is not None:
            if not plan.backup_enabled or not plan.files:
                raise ValueError("无工作或关闭备份的拆分计划不得具有备份任务目录")
            task_id = plan.backup_root.name.rsplit("_", 1)[-1]
            self.protection = RecoveryProtectionSession(
                plan.projection, plan.backup_root, task_id, "split",
                material_base=plan.base if plan.mode == "directory" else None,
            )
        elif plan.backup_enabled and plan.files:
            raise ValueError("开启备份的待写拆分计划缺少固定任务目录")
        self.check_backup_isolation()

    @property
    def backup_root(self):
        """任务及日志共用计划中的固定位置，不另行分配目录。"""
        return self.plan.backup_root

    @property
    def confirmed_content_results(self):
        return tuple(
            receipt for receipt in self.results
            if isinstance(receipt, ContentPublicationResult)
            and receipt.content_completed
        )

    def _stage(self, current, phase):
        self.current, self.phase = current, phase

    def release_contents(self):
        # 终止时释放本任务持有的正文和观察引用；正式结果仍独立保留。
        # 调用方、traceback或保护投影持有的副本不承诺擦除。
        self._finished.clear()
        self.plan = replace(
            self.plan,

            log_content=None,
            files=tuple(
                replace(
                    member, content=None,
                    expected=replace(member.expected, source_bytes=None),
                )
                for member in self.plan.files
            ),
            projection=None,
        )

    def invalidate(self):
        if self.consumed:
            return False
        self.consumed = True
        self.release_contents()
        self._stage(None, "cancelled")
        return True

    def check_backup_isolation(self):
        if self.protection is None:
            return
        missing = tuple(
            path for path, kind in self.protection._task_expected.chain
            if kind == "absent"
        )
        internal = missing[0] if missing else self.backup_root
        # 跳过日志是本次保护任务内的附属输出，不是需要与其隔离的来源。
        # 固定跳过工作目标仍不得与内部创建范围重叠。
        resources = tuple(
            member.target for member in self.plan.skip_targets
        )
        for resource in resources:
            if resource is not None and overlaps(resource, internal):
                raise ValueError(
                    f"拆分备份任务或必要缺失祖先与来源、日志或固定跳过目标重叠："
                    f"{resource}"
                )

    def _recheck_chain(self, observation, *, allow_created=False):
        if not observation:
            return
        _, current = checked_chain(
            observation[-1][0], include_observation=True,
        )
        if len(current) != len(observation):
            raise ValueError("规划后路径链长度变化；必须重新预演及确认")
        for (path, expected), (actual_path, actual) in zip(observation, current):
            if path != actual_path:
                raise ValueError(f"规划后路径链变化：{path}")
            if actual == expected:
                continue
            if (
                allow_created and expected == "absent" and actual == "directory"
                and identity(path) in self._created
            ):
                continue
            raise ValueError(
                f"规划后对象或必要父目录变化：{path}；"
                f"expected={expected}, actual={actual}；必须重新预演及确认"
            )

    def recheck_skips(self):
        for skipped in self.plan.skip_targets:
            if not skipped.blocked:
                self._recheck_chain(skipped.observation)
                continue
            try:
                checked_chain(skipped.target)
            except UnsafePathError as error:
                if error.observation == skipped.observation:
                    continue
                raise ValueError(
                    f"预演时受阻的跳过目标已变化：{skipped.target}；"
                    "原路径前提、阻塞位置或类别已偏离，必须重新预演及确认"
                ) from error
            raise ValueError(
                f"预演时受阻的跳过目标已变化：{skipped.target}；"
                "必须重新预演及确认"
            )

    def recheck_log(self):
        if self.plan.log_expected is not None and self.log_result is None:
            recheck_file(self.plan.log_expected)

    def _recheck_created(self, member=None):
        """只检查本次实际使用的自有父目录；全量仅用于开始关口。"""
        positions = (
            None if member is None else
            {identity(path) for path, _ in member.expected.chain[:-1]}
        )
        for key, expected in self._created.items():
            if positions is not None and key not in positions:
                continue
            target, chain = checked_chain(
                expected.path, include_observation=True,
            )
            if chain[-1][1] != "directory":
                raise ValueError(f"本次已创建目录存在性或类型变化：{target}")
            attributes = target.lstat()
            if ObjectIdentity(attributes.st_dev, attributes.st_ino) != expected.object_identity:
                raise ValueError(f"本次已创建目录身份变化：{target}")

    def recheck(self, member=None):
        """开始时全量一次；动作边界只核对当前目标，不重读其他文件。

        本检查不解析源码、不重新构建成果。当前目标内容、身份及
        必要父目录是写入安全前提，不是输入框正文的第二权威。
        """
        self._recheck_created(member)
        if member is None:
            self.check_backup_isolation()
            self.recheck_skips()
            self.recheck_log()
            members = self.plan.files
        else:
            members = (member,)
        for current in members:
            self._recheck_chain(
                current.expected.chain[:-1], allow_created=True,
            )
            finished = self._finished.get(identity(current.target))
            recheck_file(current.expected if finished is None else finished)

    def _append(self, receipt):
        self.results = (*self.results, receipt)

    def _create_parents(self, unit, member):
        for directory in unit.parent_directories:
            self._stage(directory, "固定必要目录创建或正常结果复用")
            # 单目录核心在mkdir前核对自己的不存在及父链前提；
            # 不为父目录创建重新读取全部文件正文。
            self._recheck_created(member)
            self._recheck_chain(
                member.expected.chain[:-1], allow_created=True,
            )
            if identity(directory) in self._created:
                continue
            index = next(
                number for number, (path, _) in enumerate(member.expected.chain)
                if path == directory
            )
            absent = FileObservation(
                directory, member.expected.chain[:index + 1], "absent",
                None, None, None, None, None, None,
            )
            step = prepare_directory_creation(absent)
            self._stage(directory, "固定必要目录步骤执行")
            try:
                receipt = execute_filesystem_change(step)
            except FilesystemChangeError as error:
                self._append(error.result)
                self._stage(error.result.target, error.result.phase)
                raise
            self._append(receipt)
            created = receipt.created_directory
            if not isinstance(created, DirectoryObservation):
                raise RuntimeError("正常目录创建结果缺少正式DirectoryObservation")
            self._created[identity(directory)] = created

    def _dispose(self, artifact):
        if artifact._lifecycle in ("completed", "disposed"):
            return
        if artifact.mechanism == "in_place" and artifact._lifecycle == "attempted":
            return
        try:
            dispose_content_artifact(artifact)
        except (OSError, ValueError) as cleanup_error:
            print(
                f"[拆分自有成果清理未完成] {artifact.path}：{cleanup_error}",
                file=sys.stderr,
            )

    def _publish_member(self, member, effect):
        if not isinstance(member.content, bytes):
            raise RuntimeError("活动拆分任务缺少完整不可变成果bytes")
        self._stage(member.target, "完整拆分文件成果准备")
        artifact = prepare_content_artifact(member.expected, member.content)
        self._artifact = artifact
        completed = False
        try:
            if (
                artifact.digest != effect.after.records[0][2]
                or artifact.content is not member.content
            ):
                raise ValueError("拆分成果不对应固定具体预期")
            self._stage(member.target, "完整成果准备后当前目标复核")
            self.recheck(member)
            self._stage(member.target, "固定拆分文件成果发布")
            try:
                receipt = publish_content(artifact)
            except ContentPublicationError as error:
                self._append(error.result)
                self._stage(error.result.target, error.result.phase)
                raise
            self._append(receipt)

            self._stage(member.target, "拆分正式成果身份内容最终核验")
            actual = observe_file(member.target, include_digest=True)
            expected_identity = (
                member.expected.object_identity
                if artifact.mechanism == "in_place"
                else ObjectIdentity(*artifact.identity)
            )
            links = (
                member.expected.link_count
                if artifact.mechanism == "in_place" else 1
            )
            if (
                actual.kind != "file"
                or actual.object_identity != expected_identity
                or actual.link_count != links
                or actual.digest != effect.after.records[0][2]
            ):
                raise OSError("拆分正式成果身份、链接或内容不符")
            self._stage(member.target, "当前文件必要父目录最终核验")
            self._recheck_created(member)
            self._recheck_chain(
                member.expected.chain[:-1], allow_created=True,
            )
            self._finished[identity(member.target)] = actual
            self.completed_files = (*self.completed_files, member.target)
            completed = True
        finally:
            self._dispose(artifact)
            if completed or artifact._lifecycle == "disposed":
                self._artifact = None

    def _publish_log(self):
        if self.plan.log_expected is None:
            return None
        self._stage(self.plan.log_path, "辅助跳过日志固定前提复核")
        self.recheck_log()
        self._stage(self.plan.log_path, "完整辅助跳过日志成果准备")
        artifact = prepare_content_artifact(
            self.plan.log_expected, self.plan.log_content,
        )
        self._artifact = artifact
        try:
            self._stage(self.plan.log_path, "辅助成果准备后日志前提复核")
            self.recheck_log()
            self._stage(self.plan.log_path, "辅助跳过日志成果发布")
            try:
                self.log_result = publish_content(artifact)
            except ContentPublicationError as error:
                self.log_result = error.result
                self._stage(error.result.target, error.result.phase)
                raise
            return str(self.plan.log_path)
        finally:
            self._dispose(artifact)
            if artifact._lifecycle in ("completed", "disposed"):
                self._artifact = None

    def result(self, *, cancelled=False, log_path=None, completed=False, error=None):
        started = (
            self.protection is not None
            and bool(self.protection.directory_results)
        )
        return RestoreResult(
            str(self.plan.base) if self.plan.base is not None else "",
            len(self.confirmed_content_results), len(self.plan.skipped),
            log_path, int(error is not None),
            self.plan.ignored_count, self.plan.source_failure_count,
            cancelled, tuple(str(member.path) for member in self._created.values()),
            self.plan.backup_enabled,
            str(self.backup_root) if started else "",
            self.results, self.completed_files, completed,
            self.current, self.phase, self.fact_error, error,
            log_result=self.log_result,
        )

    def execute(self, log_func=None):
        if self.consumed:
            raise ValueError("本次拆分任务已执行、失败或取消；必须重新预演及确认")
        self.consumed = True
        active_unit = None
        unit_start = 0
        try:
            try:
                self._stage(self.plan.base, "执行开始前一次全量固定目标复核")
                self.recheck()
                for member, active_unit in zip(
                    self.plan.files, self.plan.projection.units,
                ):
                    unit_start = len(self.results)
                    self._stage(member.target, "还原进度记录")
                    if log_func:
                        log_func(
                            f"正在还原第 {len(self.completed_files) + 1} 个文件："
                            f"{member.source_path}"
                        )
                    self._stage(member.target, "当前文件动作前复核")
                    self.recheck(member)
                    if self.protection is not None:
                        self._stage(
                            self.backup_root, "拆分修改前保护及正式清单发布",
                        )
                        self.protection.protect(active_unit.unit_id)
                        self._stage(member.target, "保护后当前文件复核")
                        self.recheck(member)
                        self._stage(
                            self.backup_root, "工作改变前started屏障",
                        )
                        self.protection.begin(active_unit.unit_id)
                    self._create_parents(active_unit, member)
                    self._publish_member(member, active_unit.effects[0])
                    if self.protection is not None:
                        self._stage(
                            self.backup_root, "拆分工作完成事实发布",
                        )
                        self.protection.complete(
                            active_unit.unit_id, self.results[unit_start:],
                        )
                    active_unit = None

                # 各文件在自己的发布边界已核验并登记完成；
                # 不为循环结束再次读取全部已完成成果。
                log_path = self._publish_log()
                self._stage(self.plan.base, "最终运行记录")
                if log_func:
                    if log_path:
                        log_func(f"跳过日志：{log_path}")
                    if self.protection is not None and self.protection.directory_results:
                        log_func(f"拆分备份目录：{self.backup_root}")
                    log_func(
                        f"代码拆分还原完成：成功 {len(self.completed_files)} 个，"
                        f"跳过 {len(self.plan.skipped)} 个"
                    )
                self._stage(None, "completed")
                return self.result(log_path=log_path, completed=True)
            except (OSError, ValueError) as error:
                if (
                    active_unit is not None and self.protection is not None
                    and not self.protection.failed
                    and self.protection.document is not None
                    and any(
                        unit["id"] == active_unit.unit_id
                        and unit["fact"]["state"] == "started"
                        for unit in self.protection.document.value["units"]
                    )
                ):
                    try:
                        self.protection.uncertain(
                            active_unit.unit_id, self.results[unit_start:],
                        )
                    except (OSError, ValueError) as fact_error:
                        self.fact_error = fact_error
                raise RestoreExecutionError(
                    self.result(error=error), self,
                ) from error
        finally:
            self.release_contents()


def split_and_restore(
    confirm_func, output_rules, target_root_folder,
    code_header_line, code_footer_line, existing_file_policy, log_func=None, *,
    source_text, target_mode="directory", normalize_text_newlines=False,
    ignore_text=MESSAGE_FILE_EXCLUDED, failure_text=MESSAGE_FILE_FAILED,
    backup_enabled=True, backup_dir="",
):
    """正式组合入口：固定输入正文、完整准备、一次确认、逐文件执行。"""
    if not callable(confirm_func):
        raise TypeError("实际拆分必须提供当次确认函数")
    plan = prepare_restore(
        output_rules, target_root_folder,
        code_header_line, code_footer_line, existing_file_policy,
        source_text=source_text, target_mode=target_mode,
        normalize_text_newlines=normalize_text_newlines,
        ignore_text=ignore_text, failure_text=failure_text,
        backup_enabled=backup_enabled, backup_dir=backup_dir,
    )
    task = RestoreTask(plan)
    try:
        if log_func:
            for path, reason in plan.skipped:
                log_func(f"预演跳过：{path}；{reason}")
        if not plan.files:
            task.invalidate()
            return task.result()
        if confirm_func(plan) is not True:
            task.invalidate()
            return task.result(cancelled=True)
        return task.execute(log_func)
    finally:
        task.invalidate()
