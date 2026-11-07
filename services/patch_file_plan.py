# -*- coding: utf-8 -*-

"""预演文件成果、固定执行单元及共同成员事实；不读取磁盘或发布文件。"""

from dataclasses import dataclass
from pathlib import Path

from core.content_publication import ContentPublicationResult
from core.filesystem_changes import FilesystemChangeResult
from core.name_moves import NativeNameMoveResult
from core.text_newlines import normalize_newlines
from services.patch_ops import CONTENT_OPS
from core.file_observation import FileObservation, TreeObservation
from core.protection_projection import (
    ContentState, FixedEffect, FixedUnit, absent_state,
    state_from_bytes, state_from_observation,
)
from core.recovery_content import content_integrity


@dataclass(frozen=True)
class SkippedPatchPath:
    """跳过只固定原链和类型，不读取正文或展开目录后代。"""

    path: Path
    chain: tuple
    kind: str


@dataclass(frozen=True)
class PatchAction:
    """修改包解释后的业务动作；共享核心不重新解析OP。"""

    label: str
    payload: object
    execute: bool
    expectations: tuple
    name_move: object = None


@dataclass(frozen=True)
class PatchPlan:
    """有序业务动作；保护范围只由唯一正式投影拥有。"""

    root: Path
    actions: tuple


def patch_observation_kind(observed):
    if isinstance(observed, TreeObservation):
        return "directory"
    if isinstance(observed, (FileObservation, SkippedPatchPath)):
        return observed.kind
    raise TypeError("修改包必须消费正式观察或固定跳过路径")


def fixed_patch_unit(members, text_file):
    """成果到具体effects，不查询磁盘或另行推导保护祖先。"""
    first = members[0]
    kind = first.payload["op"]
    source = first.expectations[0]
    reads = ()
    if text_file is not None:
        effects = (
            FixedEffect(source, state_from_bytes(text_file.content)),
        )
    elif kind in ("delete_file", "delete_dir"):
        effects = (FixedEffect(source, absent_state()),)
    elif kind == "create_dir":
        records = (("", "directory", ""),)
        effects = (FixedEffect(source, ContentState(
            "directory", records, content_integrity("directory", records),
        )),)
    elif kind in (
        "copy_file", "copy_dir", "move_file", "rename_file",
        "move_dir", "rename_dir",
    ):
        target = first.expectations[1]
        desired = state_from_observation(source)
        if kind in ("copy_file", "copy_dir"):
            effects = (FixedEffect(target, desired),)
            reads = (source,)
        else:
            effects = (
                FixedEffect(source, absent_state()),
                FixedEffect(target, desired),
            )
    else:
        raise ValueError(f"缺少修改包具体成果政策：{kind}")
    return FixedUnit(
        tuple(member.payload["id"] for member in members), effects, reads,
    )


TEXT_WRITE_OPS = frozenset({"write_file", *CONTENT_OPS})


@dataclass(frozen=True)
class TextFileSource:
    """仅在预演准备期间消费的原文、原文区间及追加片段。"""

    target: Path
    source: str | None
    replacements: tuple
    appendices: tuple
    content: str | None = None


@dataclass(frozen=True)
class TextFilePlan:
    """活动任务持有不可变成果字节；终止后 content=None，不代表空成果。"""

    target: Path
    content: bytes | None


@dataclass(frozen=True)
class PatchExecutionUnit:
    """同文件文本成员共同执行，其他动作各自形成一个单元。"""

    index: int
    indices: tuple
    members: tuple[PatchAction, ...]
    text_file: TextFilePlan | None = None
    fixed: FixedUnit | None = None

    @property
    def labels(self):
        return tuple(member.label for member in self.members)

    @property
    def execute(self):
        return self.members[0].execute


@dataclass(frozen=True)
class PatchMemberResult:
    """共同成员引用同一单元结果，不拥有独立发布或完成状态。"""

    op_id: str
    path: str
    unit_result: "PatchUnitResult"

    @property
    def status(self):
        return self.unit_result.status

    @property
    def stage(self):
        return self.unit_result.stage

    @property
    def error(self):
        return self.unit_result.error

    @property
    def results(self):
        return self.unit_result.results

    @property
    def work_completed(self):
        return self.unit_result.work_completed


@dataclass(frozen=True)
class PatchUnitResult:
    """任务状态归单元；工作事实直接引用正式有序步骤。

    completed由所属协调在工作及必要最终核验结束后登记。
    后续完成事实或日志失败可以status=failed且work_completed=True；
    正文确认查询直接消费内容结果，不使用名称替换作为判据。
    """

    unit: PatchExecutionUnit
    status: str = "pending"
    results: tuple[
        FilesystemChangeResult | ContentPublicationResult | NativeNameMoveResult,
        ...
    ] = ()
    stage: str = "尚未执行"
    error: OSError | ValueError | None = None
    work_completed: bool = False

    def __post_init__(self):
        if not isinstance(self.unit, PatchExecutionUnit):
            raise TypeError("单元结果必须消费PatchExecutionUnit")
        if self.status not in (
            "pending", "running", "skipped", "completed", "failed",
        ):
            raise ValueError("修改包单元任务状态非法")
        if type(self.results) is not tuple:
            raise TypeError("实际步骤必须是不可变元组")
        if any(
            not isinstance(result, (
                FilesystemChangeResult,
                ContentPublicationResult,
                NativeNameMoveResult,
            ))
            for result in self.results
        ):
            raise TypeError("单元只接受正式工作步骤，不接受日志、计划或材料")
        if type(self.work_completed) is not bool:
            raise TypeError("工作完成事实必须是布尔值")
        if type(self.stage) is not str or not self.stage:
            raise ValueError("单元阶段必须是明确非空字符串")
        if self.error is not None and not isinstance(
            self.error, (OSError, ValueError),
        ):
            raise TypeError("单元明确失败必须保留具体I/O或状态错误")

        if self.status in ("pending", "skipped") and (
            self.results or self.work_completed or self.error is not None
        ):
            raise ValueError("尚未执行或固定跳过不得携带工作步骤或完成事实")
        if self.work_completed and (
            not self.results or any(not result.succeeded for result in self.results)
        ):
            raise ValueError("工作完成必须具有非空正常实际步骤")
        if self.status == "completed" and (
            not self.work_completed or self.error is not None
        ):
            raise ValueError("正常完成状态必须具有工作完成且没有错误")
        if self.status == "running" and (
            self.work_completed or self.error is not None
        ):
            raise ValueError("运行中不得登记结束事实或明确失败")

        # 正式失败步骤之后没有继续工作资格；正常前缀后的
        # 所属范围核验失败可以保留全正常步骤及单元error。
        failed = False
        for result in self.results:
            if failed:
                raise ValueError("未正常完成步骤之后不得登记后续工作")
            failed = not result.succeeded

    @property
    def content_results(self):
        return tuple(
            result for result in self.results
            if isinstance(result, ContentPublicationResult)
        )

    @property
    def confirmed_content_results(self):
        """正文确认不是整体成功；清理失败仍保留已确认正文。"""
        return tuple(
            result for result in self.content_results
            if result.content_completed
        )

    @property
    def member_results(self):
        return tuple(
            PatchMemberResult(
                member.payload["id"],
                member.payload["path"],
                self,
            )
            for member in self.unit.members
        )


def compose_file_text(file_source):
    """一次组合原文替换，再顺序追加；不搜索生成的新文字。"""
    if file_source.content is not None:
        return file_source.content
    if file_source.source is None:
        raise ValueError("局部文本修改缺少固定原文")

    fragments = []
    previous = 0
    for start, end, replacement in file_source.replacements:
        fragments.extend((file_source.source[previous:start], replacement))
        previous = end
    fragments.append(file_source.source[previous:])
    fragments.extend(file_source.appendices)
    return "".join(fragments)


def prepare_text_file(file_source, members, newline):
    """预演唯一成果准备链；失败定位到文件、共同成员及实际准备阶段。"""
    stage = "原文组合"
    try:
        complete = compose_file_text(file_source)
        if newline is not None:
            stage = "换行转换"
            complete = normalize_newlines(complete, newline)
        stage = "UTF-8编码"
        content = complete.encode("utf-8")
        if not isinstance(content, bytes):
            raise TypeError("UTF-8成果编码必须返回不可变 bytes")
        return TextFilePlan(file_source.target, content)
    except (OSError, ValueError) as error:
        message = (
            f"文件成果准备失败：path={members[0].payload['path']}；"
            f"共同成员={'; '.join(member.label for member in members)}；"
            f"阶段={stage}。\n"
            "本次预演未取得整包执行资格，工作目标未修改。\n"
            f"{error}"
        )
        if isinstance(error, OSError):
            raise OSError(message) from error
        raise ValueError(message) from error


def build_execution_units(
    plan, source_snapshots, located_operations, *, text_newlines=None,
):
    """预演一次形成全部成果；只有完整返回才交付可执行单元。"""
    locations = {
        operation["id"]: location
        for operation, location in located_operations
    }
    text_groups = {}
    ordered = []

    for index, action in enumerate(plan.actions):
        if action.execute and action.payload["op"] in TEXT_WRITE_OPS:
            target = action.expectations[0].path
            group = text_groups.get(target)
            if group is None:
                group = []
                text_groups[target] = group
                ordered.append(group)
            group.append((index, action))
        else:
            ordered.append([(index, action)])

    units = []
    for group in ordered:
        indices = tuple(index for index, _ in group)
        members = tuple(action for _, action in group)
        first = members[0]
        text_file = None

        if first.execute and first.payload["op"] in TEXT_WRITE_OPS:
            target = first.expectations[0].path
            if first.payload["op"] == "write_file":
                if len(members) != 1:
                    raise ValueError("整体写入不能与同文件其他成员共同执行")
                file_source = TextFileSource(
                    target, None, (), (), first.payload["content"],
                )
            else:
                snapshot = source_snapshots[target]
                replacements = []
                appendices = []
                for member in members:
                    operation = member.payload
                    if operation["op"] == "append_text":
                        appendices.append(operation["content"])
                        continue
                    location = locations[operation["id"]]
                    if location.snapshot is not snapshot:
                        raise ValueError("同文件替换没有使用同一份预演原文")
                    replacement = (
                        operation["new"]
                        if operation["op"] == "replace_exact"
                        else operation["content"]
                    )
                    replacements.extend(
                        (start, end, replacement)
                        for start, end in location.original_ranges
                    )
                replacements.sort(key=lambda value: (value[0], value[1]))
                previous_end = 0
                for start, end, _ in replacements:
                    if (
                        start < previous_end
                        or not 0 <= start <= end <= len(snapshot.source)
                    ):
                        raise ValueError("文件执行单元包含重叠或越界原文区间")
                    previous_end = end
                file_source = TextFileSource(
                    target, snapshot.source,
                    tuple(replacements), tuple(appendices),
                )

            text_file = prepare_text_file(
                file_source, members,
                None if text_newlines is None else text_newlines[target],
            )

        units.append(PatchExecutionUnit(
            indices[0], indices, members, text_file,
            fixed_patch_unit(members, text_file) if first.execute else None,
        ))
    return tuple(units)