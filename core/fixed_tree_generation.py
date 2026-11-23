# -*- coding: utf-8 -*-

"""固定结构成员的唯一生成、正式步骤及成果范围复核。

调用方准备成员并提供来源复核、步骤交付和阶段展示。
本核心不解释复制、备份或恢复业务，不取得新的工作授权。
目录、普通正文及链接定义共用一份成功前缀约束；
完整自有暂存参与现场比较，不忽略其他临时名称。
"""

import hashlib
import stat
from dataclasses import dataclass

from core.content_publication import (
    ContentPublicationOptions, ContentPublicationResult,
    _validate_inputs, cleanup_content_artifact,
)
from core.execution_state import ExecutionState
from core.file_observation import (
    DirectoryObservation, FileObservation, ObjectIdentity,
    observe_file, observe_tree, recheck_file,
)
from core.filesystem_changes import (
    FilesystemChangeError, execute_directory_attributes,
    execute_filesystem_change, prepare_directory_attributes,
    prepare_directory_creation,
)
from core.fixed_content_execution import FixedContentExecution
from core.link_paths import (
    LinkSpecification, link_specification_digest,
    recheck_link_path, specification_from_link,
)
from core.filesystem_changes import prepare_link_creation
from core.protection_projection import (
    ContentState, validate_content_state, validate_relative_position,
)
from core.recovery_content import content_integrity
from core.recovery_paths import overlaps


@dataclass(frozen=True)
class FixedTreeMember:
    """一个固定位置的成果政策，不包含现场执行资格。

    文件只提供固定来源或完整bytes之一。
    目录可提供固定基础属性；None表示保持创建后的默认属性。
    链接只提供完整定义，不读取其引用目标。
    """

    position: str
    kind: str
    source: FileObservation | None = None
    content: bytes | None = None
    specification: LinkSpecification | None = None
    directory: DirectoryObservation | None = None
    options: ContentPublicationOptions = ContentPublicationOptions()


class FixedTreeGeneration:
    """一次生成资格；使用所属账本，不登记第二份步骤事实。"""

    def __init__(
        self, expected, members, *, recheck_context, record_step, stage,
        durable_publication=False, state=None,
    ):
        prepare_directory_creation(expected)
        for callback in (recheck_context, record_step, stage):
            if not callable(callback):
                raise TypeError("固定结构生成必须具有明确协调回调")
        if type(durable_publication) is not bool:
            raise TypeError("结构持久化开关必须是布尔值")
        if state is not None and not isinstance(state, ExecutionState):
            raise TypeError("结构生成必须使用正式ExecutionState")

        self.expected = expected
        self.members = tuple(members)
        self.recheck_context = recheck_context
        self.record_step = record_step
        self.stage = stage
        self.durable_publication = durable_publication
        self.state = ExecutionState() if state is None else state
        self._start = len(self.state.results)
        self._content_execution = None
        self.current = expected.path
        self.phase = "prepared"
        self._lifecycle = "prepared"
        self.final_observation = None
        self.desired = self._validate_members()
        self._members = {
            member.position: member for member in self.members
        }

    @property
    def results(self):
        return self.state.results[self._start:]

    @property
    def artifact(self):
        execution = self._content_execution
        return None if execution is None else execution.artifact

    def _validate_members(self):
        records = []
        definitions = []
        for member in self.members:
            if not isinstance(member, FixedTreeMember):
                raise TypeError("结构成员必须是FixedTreeMember")
            validate_relative_position(member.position)
            if not isinstance(member.options, ContentPublicationOptions):
                raise TypeError("文件成果必须具有明确发布选项")
            if member.kind == "directory":
                if any(value is not None for value in (
                    member.source, member.content, member.specification,
                )):
                    raise ValueError("目录不得携带正文或链接定义")
                if member.directory is not None and not isinstance(
                    member.directory, DirectoryObservation,
                ):
                    raise TypeError("目录属性必须来自正式目录观察")
                records.append((member.position, "directory", ""))
            elif member.kind == "file":
                # 文件选项的纯契约检查先于任何目录创建。
                # 成员目标均固定为缺失，复用唯一发布输入校验；
                # 实际成员位置和父目录关系由下方完整内容状态校验负责。
                _validate_inputs(self.expected, member.options)
                if member.directory is not None or member.specification is not None:
                    raise ValueError("文件不得携带目录属性或链接定义")
                if member.source is None:
                    if not isinstance(member.content, bytes):
                        raise TypeError("文件必须提供完整不可变bytes")
                    digest = hashlib.sha256(member.content).hexdigest()
                else:
                    source = member.source
                    if (
                        not isinstance(source, FileObservation)
                        or source.kind != "file"
                        or member.content is not None
                        or not isinstance(source.source_bytes, bytes)
                        or source.attributes is None
                        or type(source.mtime_ns) is not int
                        or source.mtime_ns != source.attributes.st_mtime_ns
                        or len(source.source_bytes) != source.attributes.st_size
                        or hashlib.sha256(source.source_bytes).hexdigest()
                        != source.digest
                    ):
                        raise ValueError("固定文件来源缺少一致的完整正文及属性")
                    if overlaps(source.path, self.expected.path):
                        raise ValueError("固定文件来源与生成目标重叠")
                    if (
                        member.options.file_mode is not None
                        or member.options.atime_ns is not None
                    ):
                        raise ValueError("来源复制的基础属性由固定来源决定")
                    digest = source.digest
                records.append((member.position, "file", digest))
            elif member.kind in (
                "file_symlink", "directory_symlink", "junction",
            ):
                if any(value is not None for value in (
                    member.source, member.content, member.directory,
                )):
                    raise ValueError("链接不得携带普通正文或目录属性")
                specification = member.specification
                if (
                    not isinstance(specification, LinkSpecification)
                    or specification.kind != member.kind
                ):
                    raise ValueError("链接成员缺少对应的完整定义")
                records.append((
                    member.position, member.kind,
                    link_specification_digest(specification),
                ))
                definitions.append((member.position, specification))
            else:
                raise ValueError("固定结构成员类型非法")

        if not records:
            raise ValueError("固定结构生成不得为空")
        # 成员调用顺序不是执行政策；统一以固定UTF-8位置形成成果结构。
        records = tuple(sorted(
            records, key=lambda record: record[0].encode("utf-8"),
        ))
        definitions = tuple(sorted(
            definitions, key=lambda member: member[0].encode("utf-8"),
        ))
        kind = records[0][1]
        integrity = (
            {
                "algorithm": "sha256-link-definition-json-v1",
                "digest": records[0][2],
            }
            if kind in ("file_symlink", "directory_symlink", "junction")
            else content_integrity(kind, records)
        )
        desired = ContentState(kind, records, integrity, definitions)
        validate_content_state(desired, target=self.expected.path)
        if kind not in (
            "directory", "file_symlink", "directory_symlink", "junction",
        ):
            raise ValueError("结构生成根必须是目录或单个链接入口")
        return desired

    def _stage(self, target, phase):
        self.current, self.phase = target, phase
        self.stage(target, phase)

    def _relative(self, target):
        position = target.relative_to(self.expected.path).as_posix()
        return "" if position == "." else position

    def object_identity_for(self, receipt):
        """展示适配器复用正式结果身份，不重新观察现场。"""
        if isinstance(receipt, ContentPublicationResult):
            if receipt.observation is not None:
                return receipt.observation.object_identity
            artifact = self.artifact
            return None if artifact is None else ObjectIdentity(*artifact.identity)
        if receipt.created_directory is not None:
            return receipt.created_directory.object_identity
        if receipt.created_link is not None:
            return receipt.created_link.object_identity
        if receipt.kind == "set_directory_attributes":
            return next(
                member.created_directory.object_identity
                for member in self.results
                if not isinstance(member, ContentPublicationResult)
                and member.kind == "create_directory"
                and member.target == receipt.target
                and member.succeeded
            )
        return None

    def _record(self, receipt):
        self.state.record_step(receipt)
        self.record_step(receipt)

    def _execute_step(self, prepared, executor=execute_filesystem_change):
        self.state.scene = prepared
        try:
            receipt = executor(prepared)
        except FilesystemChangeError as error:
            self._record(error.result)
            self._stage(error.result.target, error.result.phase)
            raise
        self._record(receipt)
        self.state.scene = None
        return receipt

    def _constraints(self):
        """范围只由本次正式成功前缀形成，不由现场补认成果。"""
        directories, files, links, modes, times = {}, {}, {}, {}, {}
        for receipt in self.results:
            if not receipt.succeeded:
                raise ValueError("结构生成范围只消费真实正常前缀")
            relative = self._relative(receipt.target)
            if isinstance(receipt, ContentPublicationResult):
                observed = receipt.observation
                if not isinstance(observed, FileObservation):
                    raise RuntimeError("正常正文步骤缺少正式成果观察")
                files[relative] = observed
            elif receipt.kind == "create_directory":
                directory = receipt.created_directory
                if not isinstance(directory, DirectoryObservation):
                    raise RuntimeError("正常目录步骤缺少正式创建身份")
                directories[relative] = directory
                modes[relative] = stat.S_IMODE(directory.attributes.st_mode)
            elif receipt.kind == "create_link":
                observed = receipt.created_link
                if observed is None:
                    raise RuntimeError("正常链接步骤缺少正式创建观察")
                if specification_from_link(observed.entry_link) != (
                    self._members[relative].specification
                ):
                    raise ValueError("正常链接成果不对应固定成员原定义")
                links[relative] = observed
            elif receipt.kind == "set_directory_attributes":
                member = self._members[relative]
                if member.directory is None or relative not in directories:
                    raise ValueError("目录属性步骤不对应固定创建成员")
                attributes = member.directory.attributes
                modes[relative] = stat.S_IMODE(attributes.st_mode)
                times[relative] = attributes.st_mtime_ns
            else:
                raise ValueError("结构生成遇到非本次生成步骤")
        return directories, files, links, modes, times

    def _check_target(self):
        directories, files, links, modes, times = self._constraints()
        if self.desired.kind != "directory":
            if not links:
                recheck_file(self.expected)
                return None
            if set(links) != {""} or directories or files:
                raise ValueError("单链接入口成果范围非法")
            return recheck_link_path(links[""])

        if not directories:
            recheck_file(self.expected)
            return None

        artifact = self.artifact
        staged = None
        if artifact is not None and artifact._lifecycle == "prepared":
            relative = self._relative(artifact.path)
            if relative in files or relative in directories or relative in links:
                raise ValueError("自有暂存与正式成果位置冲突")
            staged = (relative, artifact)

        actual = observe_tree(
            self.expected.path, include_mtime=True, include_links=True,
        )
        required_files = set(files)
        if staged is not None:
            required_files.add(staged[0])
        if (
            set(dict(actual.directories)) != set(directories)
            or set(dict(actual.files)) != required_files
            or set(dict(actual.links)) != set(links)
        ):
            raise ValueError("结构生成现场偏离本次正式成功前缀")

        for relative, directory in actual.directories:
            if (
                directory.object_identity != directories[relative].object_identity
                or stat.S_IMODE(directory.attributes.st_mode) != modes[relative]
                or relative in times
                and directory.attributes.st_mtime_ns != times[relative]
            ):
                raise ValueError(f"生成目录身份或基础属性变化：{directory.path}")
        for relative, observed in actual.files:
            if staged is not None and relative == staged[0]:
                artifact = staged[1]
                if (
                    (observed.object_identity.device, observed.object_identity.inode)
                    != artifact.identity
                    or observed.link_count != 1
                    or observed.digest != artifact.digest
                    or observed.attributes.st_size != artifact.length
                    or artifact.options.mtime_ns is not None
                    and observed.mtime_ns != artifact.options.mtime_ns
                    or artifact.options.file_mode is not None
                    and stat.S_IMODE(observed.attributes.st_mode)
                    != artifact.options.file_mode
                ):
                    raise ValueError("完整自有暂存身份、内容或属性变化")
            else:
                fixed = files[relative]
                if (
                    observed.object_identity != fixed.object_identity
                    or observed.link_count != fixed.link_count
                    or observed.digest != fixed.digest
                    or observed.mtime_ns != fixed.mtime_ns
                    or stat.S_IMODE(observed.attributes.st_mode)
                    != stat.S_IMODE(fixed.attributes.st_mode)
                ):
                    raise ValueError(f"生成文件身份、内容或属性变化：{observed.path}")
        for relative, observed in actual.links:
            if observed != links[relative]:
                raise ValueError("生成链接入口身份、定义或访问依赖变化")
        return actual

    def check(self):
        self.recheck_context()
        return self._check_target()

    def _missing_member(self, position):
        """已有成果范围先比较，再核对固定未生成位置仍不存在。"""
        target = self.expected.path / position
        observed = self.expected if not position else observe_file(target)
        if observed.kind != "absent":
            raise ValueError(f"固定未生成成员位置已经出现：{target}")
        return observed

    def execute(self):
        if self._lifecycle != "prepared":
            raise ValueError("固定结构生成已经执行、失败或取消")
        self._lifecycle = "attempted"
        completed = False
        try:
            self._stage(self.expected.path, "完整固定来源及不存在目标复核")
            self.check()
            directories = sorted(
                (member for member in self.members if member.kind == "directory"),
                key=lambda member: (
                    member.position.count("/") + bool(member.position),
                    member.position.encode("utf-8"),
                ),
            )
            for member in directories:
                target = self.expected.path / member.position
                self._stage(target, "固定普通目录创建前范围复核")
                self.check()
                self._execute_step(prepare_directory_creation(
                    self._missing_member(member.position),
                ))
                self._stage(target, "目录创建后正式前缀复核")
                self.check()

            for member in sorted(
                (member for member in self.members if member.kind != "directory"),
                key=lambda member: member.position.encode("utf-8"),
            ):
                target = self.expected.path / member.position
                self._stage(target, "固定成员准备前范围复核")
                self.check()
                fixed = self._missing_member(member.position)
                if member.kind == "file":
                    self._content_execution = FixedContentExecution(
                        fixed, member.content, source=member.source,
                        preserve_target_mode=False,
                        desired_digest=next(
                            digest for relative, kind, digest in self.desired.records
                            if relative == member.position and kind == "file"
                        ),
                        options=member.options,
                    )
                    self.state.execute_active(
                        self._content_execution,
                        lambda prepared: prepared.execute(
                            recheck_context=self.check,
                            record_step=self._record, stage=self._stage,
                        ),
                        stage=self._stage,
                    )
                    self._content_execution = None
                else:
                    prepared = prepare_link_creation(fixed, member.specification)
                    self.check()
                    self._stage(target, "固定链接入口创建")
                    self._execute_step(prepared)
                self._stage(target, "成员生成后正式前缀复核")
                self.check()

            for member in reversed(directories):
                if member.directory is None:
                    continue
                target = self.expected.path / member.position
                self._stage(target, "目录基础属性设置前范围复核")
                current = self.check()
                directory = dict(current.directories)[member.position]
                attributes = member.directory.attributes
                self._execute_step(
                    prepare_directory_attributes(
                        directory,
                        file_mode=stat.S_IMODE(attributes.st_mode),
                        atime_ns=attributes.st_atime_ns,
                        mtime_ns=attributes.st_mtime_ns,
                        durable_publication=self.durable_publication,
                    ),
                    execute_directory_attributes,
                )
                self._stage(target, "目录基础属性设置后正式前缀复核")
                self.check()

            self._stage(self.expected.path, "完整固定结构最终成果核验")
            actual = self.check()
            if self.desired.kind == "directory":
                records = actual.content_records
                definitions = tuple(
                    (relative, specification_from_link(member.entry_link))
                    for relative, member in actual.links
                )
            else:
                records = (("", actual.kind, link_specification_digest(
                    specification_from_link(actual.entry_link),
                )),)
                definitions = (("", specification_from_link(actual.entry_link)),)
            if (
                records != self.desired.records
                or definitions != self.desired.link_definitions
            ):
                raise OSError("生成成果未完整对应全部固定成员")
            if self.durable_publication:
                from core.atomic_file import sync_directory
                self._stage(self.expected.path.parent, "生成结构父目录持久化确认")
                sync_directory(self.expected.path.parent)
            self.final_observation = actual
            self._lifecycle = "completed"
            self._stage(None, "completed")
            completed = True
            return actual
        finally:
            cleanup_content_artifact(self.artifact)
            if not completed:
                self._lifecycle = "failed"