# -*- coding: utf-8 -*-

"""拆分容器的一次解析、固定范围、强制保护及正式执行。

目录记录只要求普通目录存在，不授予清空目录或删除额外成员资格。
只创建普通文件和目录；授权后沿用现场已有链接，不创建或消灭链接。
来源文件链接定义不含正文时明确跳过；失败停止，不自动回滚。
"""

import os
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from core.constants import SKIPPED_LOG_FILENAME
from core.content_publication import ContentPublicationResult
from core.file_observation import (
    FileObservation, ObjectIdentity, observe_accessed_file,
    observe_file, observe_tree, recheck_file,
)
from core.filesystem_changes import prepare_directory_creation
from core.fixed_content_execution import FixedContentExecution
from core.fixed_task_execution import FixedTaskExecution, FixedTaskWork
from core.link_paths import (
    LinkPathObservation, inspect_link_path, recheck_link_path,
    recheck_path_dependencies,
)
from core.merged_format import LINK_ENTRY_KINDS, parse_merge_blocks
from core.path_validation import strip_outer_quotes
from core.protection_projection import (
    ContentState, FixedEffect, FixedUnit, build_protection_projection,
    state_from_bytes,
)
from core.recovery_content import content_integrity
from core.recovery_paths import (
    UnsafePathError, absolute_path, checked_chain, contains, identity, overlaps,
)
from core.recovery_protection import RecoveryProtectionSession
from core.rule_lists import validate_file_rules
from core.task_observation import (
    TaskObservationState, validate_shared_file_contents,
)
from core.text_newlines import normalize_newlines
from core.time_utils import now_stamp



class RestoreScopeError(ValueError):
    """范围或穿越错误，整份任务拒绝。"""


class UnsupportedRestorePath(ValueError):
    """明确识别的目标语法不受本次拆分支持。"""


@dataclass(frozen=True)
class RestoreEntry:
    source_path: str
    expected: FileObservation
    entry_kind: str
    content: bytes | None = None

    @property
    def target(self):
        return self.expected.path

    @property
    def kind(self):
        return self.expected.kind


@dataclass(frozen=True)
class RestoreDirectoryRequirement:
    source_path: str
    access: LinkPathObservation

    @property
    def target(self):
        return self.access.actual_path


@dataclass(frozen=True)
class SkippedTarget:
    target: Path
    observation: tuple = ()
    blocked: bool = False
    access: LinkPathObservation | None = None


@dataclass(frozen=True)
class RestorePlan:
    source_description: str
    mode: str
    base: Path | None
    entries: tuple
    directories: tuple
    accesses: tuple
    skipped: tuple
    skip_targets: tuple
    block_count: int
    log_expected: FileObservation | None
    log_content: bytes | None
    backup_dir: str
    backup_root: Path | None
    projection: object

    @property
    def missing_directories(self):
        directories = {}
        for unit in self.projection.units:
            for path in (*unit.parent_directories, *unit.tree_directories):
                directories.setdefault(identity(path), path)
        return tuple(directories.values())

    @property
    def log_path(self):
        return None if self.log_expected is None else self.log_expected.path

    @property
    def file_count(self):
        return sum(member.entry_kind == "file" for member in self.entries)

    @property
    def linked_access_count(self):
        return sum(access.has_links for access in self.accesses)


@dataclass(frozen=True)
class RestoreResult:
    target_folder: str
    restored_count: int
    skipped_count: int
    log_path: str | None
    failed_count: int
    cancelled: bool = False
    created_directories: tuple = ()
    backup_root: str = ""
    results: tuple = ()
    completed_files: tuple = ()
    completed: bool = False
    current: Path | None = None
    phase: str = ""
    fact_error: OSError | ValueError | None = None
    error: OSError | ValueError | None = None
    log_result: ContentPublicationResult | None = None
    completed_entries: tuple = ()

    required_directory_count: int = 0
    work_completed: bool = False


class RestoreExecutionError(OSError):
    """报告只消费正式步骤与所属任务事实，不猜测副作用。"""

    def __init__(self, result, task):
        self.result = result
        self.task = task
        lines = [
            f"拆分在{result.phase}阶段中断：{result.current}",
            f"正常完成工作入口：{len(result.completed_entries)} 个；"
            f"普通文件：{len(result.completed_files)} 个。",
            f"全部工作结束核验完成：{result.work_completed}。",
            "【真实工作步骤】",
        ]
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
            lines.append("无正式工作步骤；不能据此证明整个磁盘未变。")
        if result.log_result is not None:
            receipt = result.log_result
            lines.extend((
                "【独立辅助日志】",
                f"{receipt.target}；机制={receipt.mechanism}；"
                f"阶段={receipt.phase}；改变已发起={receipt.mutation_attempted}；"
                f"正常完成={receipt.succeeded}；应急={receipt.emergency_path}",
                "辅助日志失败不表示前序工作未执行。",
            ))
        pending = tuple(
            member.target for member in task.plan.entries
            if member.target not in result.completed_entries
        )
        lines.extend(("", "【未正常完成的固定工作入口】"))
        lines.extend(map(str, pending))
        if not pending:
            lines.append("无；后续流程失败仍不算整任务成功。")
        lines.extend((
            f"固定跳过：{result.skipped_count}。",
            f"本次备份路径：{result.backup_root or '尚未完成备份'}",
            "文件夹记录不授权清空成员；只在已授权实际位置工作，不创建或移除链接。",
            "前序成果、文件夹及正式材料保留；任务永久失效。",
            "停止后续，不自动重试、回滚或重新拆分。",
            "现存硬链接正文写回保留对象关系；新名称不重建硬链接拓扑。",
            str(result.error),
        ))
        if task.protection is not None and task.protection.backup_cleanup_report:
            lines.append(task.protection.backup_cleanup_report)
        if result.fact_error is not None:
            lines.append(f"不确定事实保存也失败：{result.fact_error}")
        super().__init__("\n".join(lines))


def _native_path(expression):
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
            raise UnsupportedRestorePath(f"不完整盘符路径：{expression}")
    elif ":" in normalized:
        raise UnsupportedRestorePath(f"不支持冒号或备用数据流：{expression}")
    if ".." in normalized.split("/"):
        raise RestoreScopeError(f"路径穿越拒绝：{expression}")
    return Path(normalized.replace("/", os.sep))


def _target_path(expression, mode, base):
    path = _native_path(expression)
    if mode == "directory":
        if base is None:
            raise RestoreScopeError("“指定文件夹”模式缺少明确的目标文件夹绝对路径")
        if path.is_absolute():
            drive = path.drive
            if len(drive) != 2 or drive[1] != ":":
                raise UnsupportedRestorePath(
                    f"“指定文件夹”模式的绝对来源只支持完整盘符路径：{expression}"
                )
            path = base / drive[0].upper() / Path(*path.parts[1:])
        else:
            path = base / path
        if not contains(base, path) or identity(base) == identity(path):
            raise RestoreScopeError(f"映射路径越出目标文件夹或作用于目标文件夹本身，已拒绝：{expression}")
    elif not path.is_absolute():
        if base is None:
            raise RestoreScopeError(f"相对路径缺少绝对基准：{expression}")
        path = base / path
    return absolute_path(path)


def _check_positions(records):
    """目录可以有后代；文件及链接永远是结构叶子。"""
    for number, (path, kind) in enumerate(records):
        for previous, previous_kind in records[:number]:
            if identity(previous) == identity(path):
                raise ValueError(f"容器映射后位置重复：{previous} / {path}")
            if contains(previous, path) and previous_kind != "directory":
                raise ValueError(f"文件或链接不得具有后代：{previous} / {path}")
            if contains(path, previous) and kind != "directory":
                raise ValueError(f"文件或链接不得具有后代：{path} / {previous}")


def _describe_access(access):
    lines = [
        f"名义位置：{access.nominal_path}",
        f"实际位置：{access.actual_path}",
        f"固定现场类型：{access.kind}",
    ]
    for definition in (
        *access.followed_links,
        *((access.entry_link,) if access.entry_link is not None else ()),
    ):
        lines.extend((
            f"链接依赖／入口：{definition.entry_path}",
            f"类型：{definition.kind}",
            f"实际原定义：{definition.raw_target!r}",
            f"直接引用位置：{definition.direct_target}",
        ))
    return "\n".join(lines)


def _authorize_restore(candidates, authorize):
    """旧正文获取前授权既有访问路线；来源定义不授予创建链接资格。"""
    descriptions = []
    for block, access, _ in candidates:
        if not access.has_links and block.kind not in LINK_ENTRY_KINDS:
            continue
        lines = [_describe_access(access), f"容器类型：{block.kind}"]
        if block.specification is not None:
            lines.extend((
                f"来源保存原定义：{block.specification.raw_target!r}",
                "保存定义只说明来源，不创建链接、不改变目标已有定义。",
            ))
        if block.kind == "file_symlink":
            lines.append(
                "本块没有普通文件正文，不读取保存定义指向的对象；"
                "不创建空文件，记录为仅有定义、无正文可写。"
            )
        elif block.kind in ("directory", "directory_symlink", "junction"):
            lines.append(
                "只要求名义文件夹对应的实际实体文件夹存在；"
                "缺失时创建普通文件夹，已有文件夹链接保持并沿用，不清空成员。"
            )
        else:
            lines.append(
                "读取、保护并更新所列实际普通文件正文；"
                "根、父链及末级已有链接保持不变。"
            )
        descriptions.append("\n".join(lines))
    if not descriptions:
        return
    description = (
        "\n\n".join(descriptions)
        + "\n\n授权只覆盖以上本次固定范围。"
        "\n不主动创建、移除或改指向链接。"
        "\n访问依赖不成为备份或恢复对象。"
        "\n取消时不读取旧正文、不创建目录、不发布成果。"
    )
    if authorize is None:
        raise ValueError("拆分链接范围尚未取得本次授权。\n" + description)
    decision = authorize("拆分固定链接与实际工作范围授权", description)
    if type(decision) is not bool:
        raise TypeError("拆分范围授权必须返回明确布尔值")
    if not decision:
        raise ValueError("拆分范围授权已取消，任务未开始工作")


def _absent_actual(access):
    """实际普通父链的缺失观察，不把名义链接链伪装成普通链。"""
    observed = observe_file(access.actual_path)
    if observed.kind != "absent":
        raise ValueError(f"固定缺失入口已经出现：{access.actual_path}")
    return observed


def _directory_state():
    records = (("", "directory", ""),)
    return ContentState(
        "directory", records, content_integrity("directory", records),
    )


def _skipped_log_bytes(source_description, skipped):
    text = (
        f"在 {source_description} 的拆分过程中，以下入口没有执行：\n"
        + "-" * 60 + "\n"
        + "".join(f"{path} ({reason})\n" for path, reason in skipped)
    )
    return text.replace("\n", os.linesep).encode("utf-8")


def prepare_restore(
    output_rules, target_root_folder, existing_file_policy, *,
    source_text, target_mode="directory", normalize_text_newlines=False,
    backup_dir="", authorize_restore_scope=None, select_split_scope=None,
):
    """一次解析与编码，元信息授权先于旧正文读取，纯准备不改变目标。"""
    if type(source_text) is not str:
        raise TypeError("拆分输入必须是固定字符串正文")
    if target_mode not in ("directory", "in_place"):
        raise ValueError("目标模式必须是 directory 或 in_place")
    if existing_file_policy not in ("overwrite", "skip"):
        raise ValueError("已有文件政策必须是 overwrite 或 skip")
    if type(normalize_text_newlines) is not bool:
        raise TypeError("正文标准化开关必须是布尔值")
    if authorize_restore_scope is not None and not callable(authorize_restore_scope):
        raise TypeError("拆分范围授权必须可调用")
    if select_split_scope is not None and not callable(select_split_scope):
        raise TypeError("拆分固定记录选择必须可调用")
    if normalize_text_newlines:
        validate_file_rules(output_rules)
    if type(backup_dir) is not str:
        raise TypeError("备份保存位置必须是字符串")
    selected = strip_outer_quotes(backup_dir)
    if not selected:
        raise ValueError("拆分强制备份，请填写备份保存位置的明确绝对路径")
    backup_path = _native_path(selected)
    if not backup_path.is_absolute():
        raise ValueError("备份保存位置必须是明确的绝对路径")
    backup_path, backup_chain = checked_chain(backup_path, include_observation=True)
    if backup_chain[-1][1] not in ("absent", "directory"):
        raise ValueError("备份保存位置必须是普通文件夹或缺失位置")

    base = None
    if target_mode == "directory" or target_root_folder:
        selected_base = _native_path(os.fspath(target_root_folder))
        if not selected_base.is_absolute():
            raise ValueError("目标文件夹 / 相对路径基准必须是明确的绝对路径")
        base_access = inspect_link_path(selected_base, follow_final_link=True)
        if base_access.kind not in ("directory", "absent"):
            raise ValueError("目标文件夹 / 相对路径基准必须定位到普通文件夹或缺失位置")
        # base保留名义路径，逐目标再固定实际位置及访问依赖。
        base = base_access.nominal_path

    blocks = tuple(parse_merge_blocks(source_text))
    if not blocks:
        raise ValueError("声明式容器没有文件块，未生成拆分任务")

    skipped, skip_targets, candidates = [], [], []
    mapped_positions, nominal_positions = [], []
    for block in blocks:
        try:
            nominal = _target_path(block.path, target_mode, base)
        except RestoreScopeError:
            raise
        except UnsupportedRestorePath as error:
            if block.kind != "file":
                raise ValueError(f"结构入口路径不受支持：{error}") from error
            skipped.append((block.path, f"不支持的目标路径：{error}"))
            continue

        output_kind = (
            "directory" if block.kind in (
                "directory", "directory_symlink", "junction",
            ) else block.kind
        )
        nominal_positions.append((nominal, output_kind))
        try:
            entry_access = inspect_link_path(nominal, follow_final_link=False)
        except UnsafePathError as error:
            if block.kind != "file" or not error.observation:
                raise
            skipped.append((block.path, f"目标不安全或父链阻塞：{error}"))
            skip_targets.append(SkippedTarget(
                nominal, error.observation, blocked=True,
            ))
            continue

        access = entry_access
        if block.kind == "file":
            if entry_access.kind in LINK_ENTRY_KINDS:
                if entry_access.kind != "file_symlink":
                    raise ValueError(f"普通文件正文的目标是文件夹链接入口：{nominal}")
                if existing_file_policy == "skip":
                    skipped.append((block.path, f"既有文件链接按跳过政策保留：{nominal}"))
                    skip_targets.append(SkippedTarget(nominal, access=entry_access))
                    continue
                access = inspect_link_path(nominal, follow_final_link=True)
                if access.kind != "file":
                    raise ValueError("悬空文件链接不取得创建引用目标的资格")
            if access.kind == "directory":
                skipped.append((block.path, f"目标是文件夹，不删除覆盖：{nominal}"))
                skip_targets.append(SkippedTarget(nominal, access=access))
                continue
            if access.kind not in ("absent", "file"):
                raise ValueError(f"正文目标类型非法：{nominal}")
            if access.kind == "file" and existing_file_policy == "skip":
                skipped.append((block.path, f"目标已存在，按政策跳过：{nominal}"))
                skip_targets.append(SkippedTarget(nominal, access=access))
                continue
            if not access.follow_final_link:
                # 入口观察负责分类和跳过；待写普通正文必须取得明确的
                # 正文访问观察。两者不是可互换的执行契约。
                # 此处只探查元信息，后面的统一授权仍先于旧正文读取。
                content_access = inspect_link_path(
                    nominal, follow_final_link=True,
                )
                if (
                    content_access.actual_path != access.actual_path
                    or content_access.kind != access.kind
                    or content_access.object_identity != access.object_identity
                    or content_access.followed_links != access.followed_links
                    or content_access.directories != access.directories
                    or content_access.first_missing != access.first_missing
                    or content_access.entry_link is not None
                ):
                    raise ValueError(
                        "拆分入口分类至正文访问准备期间固定路径前提变化"
                    )
                access = content_access
            metadata = observe_file(access.actual_path)
        elif output_kind == "directory":
            # 目录要求沿目标现场已有路线定位，不消费来源定义创建链接。
            access = inspect_link_path(nominal, follow_final_link=True)
            if access.kind not in ("absent", "directory"):
                raise ValueError(
                    f"目录要求没有定位到实体目录或可创建位置：{nominal}"
                )
            metadata = None
        elif block.kind == "file_symlink":
            # 容器只保存该入口定义，没有目标正文。
            # 先展示来源定义和目标现场供筛选，再明确报告不执行；
            # 不能把定义JSON或空正文当作待恢复文件。
            metadata = None
        else:
            raise ValueError("容器包含未知入口类型")
        if metadata is not None and (
            metadata.kind != access.kind
            or metadata.object_identity != access.object_identity
        ):
            raise ValueError("目标元信息取得期间存在性或身份变化")
        mapped_positions.append((access.actual_path, output_kind))
        candidates.append((block, access, metadata))

    _check_positions(nominal_positions)
    _check_positions(mapped_positions)
    if select_split_scope is None:
        _authorize_restore(candidates, authorize_restore_scope)
    else:
        from core.work_selection import selected_indices

        candidates = tuple(candidates)
        retained = selected_indices(
            select_split_scope(candidates), len(candidates),
            description="代码拆分范围选择",
        )
        selected = set(retained)
        skipped.extend(
            (block.path, "本次范围选择未选，不获取旧正文、不保护、不执行")
            for number, (block, access, metadata) in enumerate(candidates)
            if number not in selected
        )
        # 授权未选不是固定现场跳过：不添加skip_targets，
        # 因而不在执行期重新访问未选入口。
        candidates = tuple(candidates[number] for number in retained)
    for _, access, _ in candidates:
        recheck_link_path(access)

    entries, directories, accesses = [], [], []
    for block, access, metadata in candidates:
        if block.kind == "file_symlink":
            skipped.append((
                block.path,
                "仅有文件链接原定义，没有普通文件正文；"
                "不创建链接、不补读引用目标、不生成空文件，目标现场保持",
            ))
            continue
        accesses.append(access)
        if block.kind in ("directory", "directory_symlink", "junction"):
            directories.append(RestoreDirectoryRequirement(block.path, access))
            continue
        expected = observe_accessed_file(
            access, include_bytes=True, include_digest=True, include_mtime=True,
        )
        if expected.kind != metadata.kind:
            raise ValueError("授权后正文目标存在性变化")
        if expected.kind == "file":
            from core.file_observation import _assert_file_attributes
            _assert_file_attributes(
                expected.attributes, metadata.attributes, expected.path,
            )
        complete = block.body
        if normalize_text_newlines:
            matched = (
                output_rules.matches(
                    access.nominal_path.relative_to(base).parts,
                )
                if target_mode == "directory" else
                output_rules.matches(expected.path.parts[1:], ignore_root=True)
            )
            complete = normalize_newlines(complete, "\r\n" if matched else "\n")
        entries.append(RestoreEntry(
            block.path, expected, "file", complete.encode("utf-8"),
        ))

    # 有后代成果的缺失目录通过正式必要父目录步骤创建。
    # 没有后代成果的目录由最深记录自身创建，祖先仍由父目录计划创建。
    missing = tuple(
        requirement for requirement in directories
        if requirement.access.kind == "absent"
    )
    for requirement in missing:
        target = requirement.target
        if any(
            identity(target) != identity(member.target)
            and contains(target, member.target)
            for member in entries
        ):
            continue
        if any(
            identity(target) != identity(other.target)
            and contains(target, other.target)
            for other in missing
        ):
            continue
        entries.append(RestoreEntry(
            requirement.source_path,
            _absent_actual(requirement.access), "directory",
        ))

    # 调序是结构依赖需要：目录创建在文件与链接叶子之前；
    # 同类成员保留容器顺序，不按后来磁盘状态重新规划。
    entries.sort(key=lambda member: member.entry_kind != "directory")
    entries = tuple(entries)
    units = []
    for number, member in enumerate(entries, 1):
        desired = (
            state_from_bytes(member.content)
            if member.entry_kind == "file" else _directory_state()
        )
        units.append(FixedUnit(
            (f"split{number:06d}",),
            (FixedEffect(member.expected, desired),),
        ))
    validate_shared_file_contents(units)
    projection = build_protection_projection(units, forbidden_roots=())

    for access in accesses:
        for definition in access.followed_links:
            if any(
                overlaps(definition.entry_path, member.target)
                for member in entries
            ):
                raise ValueError("拆分工作范围会改变本次链接访问依赖")
        for directory in access.directories:
            if contains(access.actual_path, directory.path):
                continue
            if any(contains(member.target, directory.path) for member in entries):
                raise ValueError("拆分工作范围包含原访问父目录")

    source_description = "本次输入框容器快照（粘贴或导入）"
    backup_root = None
    if entries:
        task_id = uuid.uuid4().hex
        backup_root = backup_path / f"{now_stamp()}_代码拆分_{task_id}"
    log_expected = log_content = None
    if backup_root is not None and skipped:
        log_expected = observe_file(
            backup_root / SKIPPED_LOG_FILENAME, include_digest=True,
        )
        if log_expected.kind != "absent":
            raise ValueError("本任务跳过日志目标必须固定为不存在")
        log_content = _skipped_log_bytes(source_description, skipped)
    if log_expected is not None and any(
        overlaps(member.target, log_expected.path) for member in entries
    ):
        raise ValueError("拆分工作范围与辅助日志冲突")
    return RestorePlan(
        source_description, target_mode, base, entries,
        tuple(directories), tuple(accesses), tuple(skipped), tuple(skip_targets),
        len(blocks),
        log_expected, log_content, str(backup_path), backup_root, projection,
    )


class RestoreTask:
    """唯一固定任务、共同工作账本及必要目录身份拥有者。"""

    def __init__(self, plan):
        if not isinstance(plan, RestorePlan):
            raise TypeError("拆分任务必须消费RestorePlan")
        self.plan = plan
        self.task_execution = FixedTaskExecution()
        self.observation_state = TaskObservationState(
            member.expected for member in plan.entries
        )
        self.current = None
        self.phase = "prepared"
        self.fact_error = None
        self.log_result = None
        self.protection = None
        if plan.backup_root is not None:
            if not plan.entries:
                raise ValueError("无工作任务不得生成空备份")
            task_id = plan.backup_root.name.rsplit("_", 1)[-1]
            self.protection = RecoveryProtectionSession(
                plan.projection, plan.backup_root, task_id, "split",
                material_base=(
                    inspect_link_path(plan.base, follow_final_link=True).actual_path
                    if plan.base is not None else None
                ),
            )
        elif plan.entries:
            raise ValueError("待执行任务缺少强制备份")
        self.check_backup_isolation()

    @property
    def consumed(self):
        return self.task_execution.consumed

    @property
    def results(self):
        return self.task_execution.state.results

    @property
    def completed_entries(self):
        return tuple(
            member.target
            for number, member in enumerate(self.plan.entries, 1)
            if self.task_execution.entry_result(
                f"split{number:06d}",
            ).work_completed
        )

    @property
    def completed_files(self):
        completed = set(self.completed_entries)
        return tuple(
            member.target for member in self.plan.entries
            if member.entry_kind == "file" and member.target in completed
        )

    @property
    def backup_root(self):
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
        self.observation_state.release_observations()
        self.plan = replace(
            self.plan, log_content=None,
            entries=tuple(
                replace(
                    member, content=None,
                    expected=replace(member.expected, source_bytes=None),
                )
                for member in self.plan.entries
            ),
            projection=None,
        )

    def invalidate(self):
        if not self.task_execution.invalidate():
            return False
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
        for skipped in self.plan.skip_targets:
            actual = (
                skipped.access.actual_path if skipped.access is not None
                else skipped.target
            )
            if overlaps(actual, internal):
                raise ValueError("备份创建范围与固定跳过目标重叠")
        for access in self.plan.accesses:
            for definition in access.followed_links:
                if overlaps(internal, definition.entry_path):
                    raise ValueError("备份创建范围与链接访问依赖重叠")
        for requirement in self.plan.directories:
            if contains(internal, requirement.target):
                raise ValueError("备份创建范围包含拆分目录要求")

    def recheck_skips(self):
        for skipped in self.plan.skip_targets:
            if skipped.access is not None:
                recheck_link_path(skipped.access)
                continue
            if skipped.blocked:
                try:
                    checked_chain(skipped.target)
                except UnsafePathError as error:
                    if error.observation == skipped.observation:
                        continue
                    raise ValueError("固定安全阻塞位置变化") from error
                raise ValueError("原先受阻目标已经变化，必须重新准备")
            _, chain = checked_chain(skipped.target, include_observation=True)
            if chain != skipped.observation:
                raise ValueError("固定跳过路径链变化")

    def recheck_log(self):
        if self.plan.log_expected is not None and self.log_result is None:
            recheck_file(self.plan.log_expected)

    def _check_directories(self, *, require_present=False):
        state = self.task_execution.state.directories
        for requirement in self.plan.directories:
            access = requirement.access
            recheck_path_dependencies(access)
            target, chain = checked_chain(
                requirement.target, include_observation=True,
            )
            kind = chain[-1][1]
            if access.kind == "directory":
                if kind != "directory":
                    raise ValueError(f"既有目录要求的类型变化：{target}")
                attributes = target.lstat()
                if ObjectIdentity(attributes.st_dev, attributes.st_ino) != access.object_identity:
                    raise ValueError(f"既有目录要求的身份变化：{target}")
            elif kind == "directory":
                state.require(target)
            elif kind != "absent" or require_present:
                raise ValueError(f"固定目录要求未满足或出现外部对象：{target}")

    def _expected_member(self, member):
        return self.observation_state.expected(member.expected, self.results)

    def recheck(self, member=None):
        state = self.task_execution.state
        state.recheck_created_directories(
            None if member is None else member.expected,
        )
        if member is None:
            self.check_backup_isolation()
            self.recheck_skips()
            self.recheck_log()
            self._check_directories()
            accesses = self.plan.accesses
            members = self.plan.entries
        else:
            accesses = tuple(
                access for access in self.plan.accesses
                if identity(access.actual_path) == identity(member.target)
            )
            members = (member,)
        for access in accesses:
            recheck_path_dependencies(access)
        for current in members:
            state.recheck_parents(current.expected)
            expected = self._expected_member(current)
            if isinstance(expected, LinkPathObservation):
                recheck_link_path(expected)
            elif current.entry_kind == "directory" and current.target in self.completed_entries:
                state.directories.require(current.target)
            else:
                recheck_file(expected)

    def _perform_member(self, member, effect):
        state = self.task_execution.state
        before = self._expected_member(member)
        if member.entry_kind == "file":
            execution = FixedContentExecution(
                before, member.content,
                desired_digest=effect.after.records[0][2],
            )
            actual = state.execute_active(
                execution,
                lambda prepared: prepared.execute(
                    recheck_context=lambda: self.recheck(member),
                    record_step=state.record_step, stage=self._stage,
                ),
                stage=self._stage,
            )
        elif member.entry_kind == "directory":
            receipt = state.execute_step(
                prepare_directory_creation(before), stage=self._stage,
            )
            state.accept_created_directory(receipt)
            actual = observe_tree(member.target)
            if (
                actual.content_records != (("", "directory", ""),)
                or actual.directories[0][1].object_identity
                != receipt.created_directory.object_identity
            ):
                raise OSError("固定新目录成果不对应正式创建结果")
        else:
            raise RuntimeError("拆分执行计划只允许普通正文和普通目录创建")
        state.recheck_parents(member.expected)
        return actual

    def _final_check(self):
        """新的整任务结束边界，比较正式成果，不重新选择工作范围。"""
        self._check_directories(require_present=True)
        self.recheck()

    def _publish_log(self):
        if self.plan.log_expected is None:
            return None
        execution = FixedContentExecution(
            self.plan.log_expected, self.plan.log_content,
        )
        execution.execute(
            recheck_context=self.recheck_log,
            record_step=lambda receipt: setattr(self, "log_result", receipt),
            stage=self._stage,
        )
        return str(self.plan.log_path)

    def result(self, *, cancelled=False, log_path=None, completed=False, error=None):
        backed_up = self.protection is not None and self.protection.backup_completed
        return RestoreResult(
            target_folder=str(self.plan.base) if self.plan.base is not None else "",
            restored_count=len(self.confirmed_content_results),
            skipped_count=len(self.plan.skipped),
            log_path=log_path,
            failed_count=int(error is not None),

            cancelled=cancelled,
            created_directories=tuple(
                str(member.path)
                for member in self.task_execution.state.created_directories.values()
            ),
            backup_root=str(self.backup_root) if backed_up else "",
            results=self.results,
            completed_files=self.completed_files,
            completed=completed,
            current=self.current,
            phase=self.phase,
            fact_error=self.fact_error,
            error=error,
            log_result=self.log_result,
            completed_entries=self.completed_entries,

            required_directory_count=len(self.plan.directories),
            work_completed=self.task_execution.work_completed,
        )

    def execute(self, log_func=None):
        if self.consumed:
            raise ValueError("拆分任务已经执行、失败或取消")

        def prepare_entry(entry):
            member, unit = entry
            self._stage(member.target, "固定拆分入口准备")
            if log_func:
                log_func(
                    f"正在拆分：{member.source_path}；类型={member.entry_kind}"
                )

            def accept_completed(actuals):
                self.observation_state.accept(actuals, self.results)

            return FixedTaskWork(
                unit,
                lambda: (self._perform_member(member, unit.effects[0]),),
                accept_completed, lambda: self.recheck(member),
            )

        def auxiliary():
            log_path = self._publish_log()
            if log_func:
                if log_path:
                    log_func(f"跳过日志：{log_path}")
                if self.protection is not None:
                    log_func(f"本次拆分备份路径：{self.backup_root}")
                log_func(
                    f"拆分工作完成：普通文件 {len(self.completed_files)} 个，"
                    f"授权沿用链接路线 {self.plan.linked_access_count} 项，"
                    f"文件夹要求 {len(self.plan.directories)} 个，"
                    f"固定跳过 {len(self.plan.skipped)} 个"
                )
            return log_path

        try:
            try:
                log_path = self.task_execution.execute(
                    tuple(zip(self.plan.entries, self.plan.projection.units)),
                    protection=self.protection,
                    initial_recheck=self.recheck,
                    prepare_entry=prepare_entry,
                    entry_key=lambda entry: entry[1].members[0],
                    stage=lambda role, target, phase: self._stage(target, phase),
                    final_check=self._final_check,
                    auxiliary=auxiliary, task_target=self.plan.base,
                )
                return self.result(log_path=log_path, completed=True)
            except (OSError, ValueError) as error:
                self.fact_error = self.task_execution.fact_error
                raise RestoreExecutionError(self.result(error=error), self) from error
        finally:
            self.release_contents()


def split_and_restore(
    confirm_func, output_rules, target_root_folder,
    existing_file_policy, log_func=None, *,
    source_text, target_mode="directory", normalize_text_newlines=False,
    backup_dir="", authorize_restore_scope=None, select_split_scope=None,
):
    """正式组合入口：固定准备、当次授权、一次执行确认及共同任务。"""
    if not callable(confirm_func):
        raise TypeError("实际拆分必须提供当次确认函数")
    plan = prepare_restore(
        output_rules, target_root_folder, existing_file_policy,
        source_text=source_text, target_mode=target_mode,
        normalize_text_newlines=normalize_text_newlines,
        backup_dir=backup_dir, authorize_restore_scope=authorize_restore_scope,
        select_split_scope=select_split_scope,
    )
    task = RestoreTask(plan)
    try:
        if log_func:
            for path, reason in plan.skipped:
                log_func(f"预演跳过：{path}；{reason}")
        if not plan.entries:
            task._check_directories(require_present=True)
            task.invalidate()
            return task.result()
        if confirm_func(plan) is not True:
            task.invalidate()
            return task.result(cancelled=True)
        return task.execute(log_func)
    finally:
        task.invalidate()
