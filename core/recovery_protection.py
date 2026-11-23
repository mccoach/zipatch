# -*- coding: utf-8 -*-

"""唯一投影的材料、严格依据及工作开始屏障协调。

不解析OP、不决定用户选择、不执行工作改变。
任务位置由所属业务固定；内部创建只消费固定不存在链。
正式材料保留原名和目录关系；只读取version 4，不兼容旧布局。
"""

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from core.backup_material_layout import build_backup_material_paths
from core.content_publication import (
    ContentPublicationError,
    ContentPublicationOptions,
    cleanup_content_artifact,
    prepare_content_artifact,
    publish_content,
)
from core.file_observation import (
    FileObservation,
    ObjectIdentity,
    TreeObservation,
    observe_file,
    observe_tree,
    recheck_file,
    recheck_tree,
)
from core.filesystem_changes import FilesystemChangeError
from core.directory_creation import (
    DirectoryCreationState, fixed_directory_observations,
)
from core.internal_materials import (
    InternalMaterialError,
    prepare_internal_material,
    publish_internal_material,
    validate_material_state,
)
from core.protection_projection import (
    ProtectionProjection,
    validate_absolute_path,
)
from core.recovery_document import (
    RecoveryDocument,
    build_document,
    decode_document,
    encode_document,
    with_execution_fact,
)
from core.recovery_paths import checked_chain, contains, identity, overlaps


@dataclass(frozen=True)
class RecoveryEvidence:
    """严格读取后的固定来源；材料时间不是正式原时间权威。"""

    task_root: Path
    manifest: FileObservation | None
    document: RecoveryDocument
    materials: object
    completion: FileObservation | None = None
    material_layout: TreeObservation | None = None


def _check_material_layout(task, document, materials):
    """整个files范围准确对应引用材料及必要普通目录。

    任务根的合法辅助日志不属于files材料范围。
    不忽略临时名称、未引用文件或空的额外目录；
    活动链接由普通树观察拒绝，材料文件必须独立。
    新观察只服务固定来源校验，不增加恢复对象。
    """
    required_ids = {
        entry["id"] for entry in document.value["objects"]
        if entry["material"] is not None
    }
    if set(materials) != required_ids:
        raise ValueError("恢复材料编号必须准确覆盖清单引用")
    root = task / "files"
    if not required_ids:
        _, chain = checked_chain(root, include_observation=True)
        if chain[-1][1] != "absent":
            raise ValueError("无引用材料的备份不得包含额外files目录")
        return None

    records = {"": ("", "directory", "")}
    for entry in document.value["objects"]:
        if entry["material"] is None:
            continue
        observed = materials[entry["id"]]
        position = entry["material"]
        if observed.path != root / position:
            raise ValueError("编号材料实际位置不对应清单")
        from core.recovery_document import content_state_from_value
        validate_material_state(
            observed,
            content_state_from_value(
                entry["original"], target=Path(entry["target"]),
            ),
            target=Path(entry["target"]),
        )
        parent = Path(position).parent
        while parent.as_posix() != ".":
            relative = parent.as_posix()
            records[relative] = (relative, "directory", "")
            parent = parent.parent
        members = (
            observed.content_records
            if isinstance(observed, TreeObservation) else
            (("", "file", observed.digest),)
        )
        for relative, kind, digest in members:
            located = position + "/" + relative if relative else position
            record = (located, kind, digest)
            previous = records.setdefault(located, record)
            if previous != record:
                raise ValueError("编号材料与必要目录位置矛盾")

    current = observe_tree(root)
    required = tuple(sorted(
        records.values(), key=lambda record: record[0].encode("utf-8"),
    ))
    if (
        current.content_records != required
        or any(member.link_count != 1 for _, member in current.files)
    ):
        raise ValueError(
            "files材料范围包含缺失、额外、损坏、类型错配或共享材料"
        )
    return current


class ProtectionPublicationError(OSError):
    """所属位置及对象角色明确，不从cause猜工作目标副作用。"""

    def __init__(self, session, error, result=None):
        self.session = session
        self.error = error
        self.result = result
        self.role = session.role
        self.current = session.current
        self.phase = session.phase
        super().__init__(
            f"正式保护流程未完整结束；对象角色={self.role}。\n"
            f"位置：{self.current}；阶段：{self.phase}。\n"
            "保护资格已失效，禁止继续工作、重试发布或回退旧清单。\n"
            "内部目录、材料及清单可能已有副作用；"
            "这些事实不证明用户工作目标已改变。\n"
            "前序正式依据和编号材料不自动删除。\n"
            f"{error}"
        )


def _absent_child(parent, name):
    target = parent.path / name
    return FileObservation(
        target, (*parent.chain, (target, "absent")), "absent",
        None, None, None, None, None, None,
    )


def _recheck_observation(observed):
    from core.link_paths import LinkPathObservation, recheck_link_path

    if isinstance(observed, LinkPathObservation):
        return recheck_link_path(observed)
    if isinstance(observed, TreeObservation):
        return recheck_tree(observed)
    if isinstance(observed, FileObservation):
        return recheck_file(observed)
    raise TypeError("固定来源复核必须消费正式观察")



def _observe_material(path, original, *, include_bytes, target):
    from core.recovery_document import content_state_from_value

    state = content_state_from_value(original, target=target)
    if state.kind == "directory":
        observed = observe_tree(
            path, include_bytes=include_bytes, include_mtime=include_bytes,
        )
    elif state.kind != "absent":
        observed = observe_file(
            path, include_bytes=include_bytes, include_digest=True,
            include_mtime=include_bytes,
        )
    else:
        raise ValueError("不存在原状态没有编号材料")
    return validate_material_state(observed, state, target=target)


def _check_evidence_isolation(task, document, forbidden_roots):
    for root in forbidden_roots:
        validate_absolute_path(root)
        if overlaps(root, task):
            raise ValueError("源恢复材料与明确禁止位置重叠")
    for entry in document.value["objects"]:
        if overlaps(Path(entry["target"]), task):
            raise ValueError("恢复原对象与源清单或材料重叠")
    for unit in document.value["units"]:
        for expression in (
            *unit["parent_directories"], *unit["tree_directories"],
        ):
            if overlaps(Path(expression), task):
                raise ValueError("固定工作目录与源恢复材料重叠")


def read_recovery_evidence(task_root, *, forbidden_roots=()):
    """严格解析一次，仅引用材料取得完整bytes。

    未引用文件不成为恢复对象。
    原文件时间来自清单，不以材料后来时间替代。
    返回前的新边界核验不重新解析或改变固定来源。
    """
    task, chain = checked_chain(task_root, include_observation=True)
    validate_absolute_path(task)
    if chain[-1][1] != "directory":
        raise ValueError("恢复依据来源必须是既有普通任务目录")
    manifest = observe_file(
        task / "recovery.json", include_bytes=True, include_digest=True,
    )
    if manifest.kind != "file" or manifest.link_count != 1:
        raise ValueError("缺少独立普通recovery.json；不读取旧manifest")
    document = decode_document(manifest.source_bytes)
    completion = observe_file(
        task / "backup.complete", include_bytes=True, include_digest=True,
    )
    if (
        completion.kind != "file"
        or completion.link_count != 1
        or completion.source_bytes
        != (document.value["task_id"] + "\n").encode("utf-8")
    ):
        raise ValueError(
            "备份缺少有效的全量完成凭据 backup.complete，禁止恢复。\n"
            "清单存在或结构正确不能证明全部备份屏障成功；"
            "不完整残留及未提供完成凭据的旧任务均不作为正式恢复依据。"
        )
    _check_evidence_isolation(task, document, tuple(forbidden_roots))
    materials = {}
    for entry in document.value["objects"]:
        if entry["material"] is not None:
            materials[entry["id"]] = _observe_material(
                task / "files" / entry["material"],
                entry["original"], include_bytes=True,
                target=Path(entry["target"]),
            )
    layout = _check_material_layout(task, document, materials)
    evidence = RecoveryEvidence(
        task, manifest, document, MappingProxyType(materials),
        completion, layout,
    )
    # 晚材料获取可能跨越早材料变化；新边界复核只比较，
    # 后续工作仍消费第一次获取的完整bytes。
    recheck_recovery_evidence(evidence)
    return evidence


def read_protection_evidence(session):
    """回滚使用本任务已确认完整文档和磁盘原材料。

    执行事实清单发布可能是原失败原因，因此不重新读取该清单；
    不使用未发布候选，不回退旧格式或重新解释原OP。
    """
    if not isinstance(session, RecoveryProtectionSession):
        raise TypeError("回滚依据必须来自正式保护协调")
    document = session.document
    if not session.backup_completed or document is None or {
        unit["id"] for unit in document.value["units"]
    } != set(session._units):
        raise ValueError("本次全部原状态保护尚未完成")
    _check_evidence_isolation(session.task_root, document, ())
    # 回滚取得完整bytes是新的读取边界，不是重新认证备份身份的边界。
    # 先消费正式材料发布时固定的身份及完整files范围；
    # 不调用失败清单复核，不因事实发布失败而回退读取旧清单。
    session._check_materials()
    recheck_file(session._completion_expected)
    materials = {
        entry["id"]: _observe_material(
            session.task_root / "files" / entry["material"],
            entry["original"], include_bytes=True,
            target=Path(entry["target"]),
        )
        for entry in document.value["objects"]
        if entry["material"] is not None
    }
    # 晚材料读取可能跨越早材料替换；仍比回正式发布的固定对象，
    # 不用这次新观察替代原备份身份、范围或完成凭据。
    session._check_materials()
    recheck_file(session._completion_expected)
    layout = _check_material_layout(
        session.task_root, document, materials,
    )
    evidence = RecoveryEvidence(
        session.task_root, None, document, MappingProxyType(materials),
        session._completion_expected, layout,
    )
    recheck_recovery_evidence(evidence)
    return evidence


def recheck_recovery_evidence(evidence, *, forbidden_roots=()):
    """执行期固定来源比较，不重新解析或替代正式原时间。"""
    if not isinstance(evidence, RecoveryEvidence):
        raise TypeError("来源复核必须消费RecoveryEvidence")
    _check_evidence_isolation(
        evidence.task_root, evidence.document, tuple(forbidden_roots),
    )
    if evidence.manifest is not None:
        recheck_file(evidence.manifest)
    if evidence.completion is not None:
        recheck_file(evidence.completion)
    for observed in evidence.materials.values():
        _recheck_observation(observed)
    if evidence.material_layout is not None:
        recheck_tree(evidence.material_layout)
        if any(
            member.link_count != 1
            for _, member in evidence.material_layout.files
        ):
            raise ValueError("固定材料范围包含非独立文件")
    else:
        # 无材料时同样拒绝额外files范围。
        # 明确构造的内存依据也必须满足准确材料对应，不补猜定义。
        _check_material_layout(
            evidence.task_root, evidence.document, evidence.materials,
        )
    if evidence.manifest is not None:
        recheck_file(evidence.manifest)
    if evidence.completion is not None:
        recheck_file(evidence.completion)


class RecoveryProtectionSession:
    """唯一正式清单拥有者，直接组织材料及清单发布屏障。

    document只持有正常发布候选，candidate保留待发布依据。
    材料结果、清单结果及内部目录结果分别持有。
    工作确认、成果预期核验及工作执行归所属协调。
    """

    def __init__(
        self, projection, task_root, task_id, task_kind, *,
        durable_publication=True, material_base=None,
    ):
        if not isinstance(projection, ProtectionProjection):
            raise TypeError("保护协调必须消费唯一ProtectionProjection")
        validate_absolute_path(task_root)
        if type(task_id) is not str or not task_id:
            raise ValueError("保护任务必须具有明确非空标识")
        if type(task_kind) is not str or task_kind not in (
            "patch", "restore", "split",
        ):
            raise ValueError("保护任务类别非法")
        if type(durable_publication) is not bool:
            raise TypeError("保护确认开关必须是布尔值")
        self.projection = projection
        self.task_root = task_root
        self.task_id = task_id
        self.task_kind = task_kind
        self.durable_publication = durable_publication
        self._task_expected = observe_file(task_root)
        if self._task_expected.kind != "absent":
            raise ValueError("保护任务目录必须固定为不存在")
        self._check_isolation()
        self._directories = fixed_directory_observations(
            self._task_expected, include_target=True,
        )
        self._manifest_expected = _absent_child(
            self._task_expected, "recovery.json",
        )
        self._completion_expected = _absent_child(
            self._task_expected, "backup.complete",
        )

        self.material_paths = build_backup_material_paths(
            projection, base=material_base,
        )
        # 所有业务只在此消费唯一布局；全量材料准备及事实更新不重算位置。
        self._material_expected = {
            member.object_id: observe_file(
                task_root / "files" / self.material_paths[member.object_id],
            )
            for member in projection.objects
            if member.original_type != "absent"
        }
        if any(
            expected.kind != "absent"
            for expected in self._material_expected.values()
        ):
            raise ValueError("本任务材料位置必须固定为不存在")
        self._directory_state = DirectoryCreationState()
        self._units = {
            member.unit_id: member for member in projection.units
        }
        self._document = None
        self._candidate = None
        self._material_results = {}
        self._material_observations = {}
        self._material_layout = None
        self._directory_results = []
        self._publication_results = []
        self._active_material = None
        self._artifact = None
        self._failed = False
        self._busy = False
        self._initialized = False

        self.backup_completed = False
        self.backup_cleanup_report = ""
        self.backup_cleanup_error = None
        self.role = "内部任务目录"
        self.current = task_root
        self.phase = "prepared"

    @property
    def document(self):
        return self._document

    @property
    def candidate(self):
        return self._candidate

    @property
    def failed(self):
        return self._failed

    @property
    def material_results(self):
        return MappingProxyType(self._material_results)

    @property
    def directory_results(self):
        return tuple(self._directory_results)

    @property
    def publication_results(self):
        return tuple(self._publication_results)

    @property
    def active_material(self):
        return self._active_material

    @property
    def active_artifact(self):
        return self._artifact

    def _check_isolation(self):
        # 业务根禁止被内部创建范围包含，但允许无关备份位于根内部。
        # 实际effects、保护对象及reads仍必须双向隔离。
        for root in self.projection.forbidden_roots:
            if contains(self.task_root, root):
                raise ValueError("任务材料范围包含业务定位根")
        positions = [
            member.target for member in self.projection.objects
        ]
        for unit in self.projection.units:
            positions.extend(source.path for source in unit.fixed.reads)
            positions.extend(effect.before.path for effect in unit.effects)
            positions.extend(unit.parent_directories)
            positions.extend(unit.tree_directories)
        if any(overlaps(position, self.task_root) for position in positions):
            raise ValueError("任务材料与工作范围或只读来源重叠")
        missing = tuple(
            path for path, kind in self._task_expected.chain if kind == "absent"
        )
        if missing:
            internal_root = missing[0]
            if any(
                contains(internal_root, root)
                for root in self.projection.forbidden_roots
            ) or any(overlaps(position, internal_root) for position in positions):
                raise ValueError("必要内部缺失祖先与业务范围或来源重叠")

    def _stage(self, role, current, phase):
        self.role, self.current, self.phase = role, current, phase

    def _enter(self):
        if self._failed:
            raise ValueError("保护流程已失败，禁止重试或继续工作")
        if self._busy:
            raise ValueError("保护协调不允许重入")
        self._busy = True

    def _initialize(self):
        if self._initialized:
            return
        self._check_isolation()
        self._directory_state.ensure_all(
            self._directories,
            record_step=self._directory_results.append,
            stage=lambda target, phase:
            self._stage("内部任务目录", target, phase),
        )
        self._initialized = True

    def _ensure_material_parents(self, expected):
        """同一固定原链、同一目录机制；内部结果不进入工作账本。"""
        self._directory_state.ensure_all(
            fixed_directory_observations(expected),
            record_step=self._directory_results.append,
            stage=lambda target, phase:
            self._stage("材料路径目录", target, phase),
        )

    def _check_materials(self):
        self._directory_state.recheck()
        if self._material_layout is not None:
            recheck_tree(self._material_layout)
        elif self._document is not None:
            _check_material_layout(
                self.task_root, self._document,
                self._material_observations,
            )
        for observed in self._material_observations.values():
            self._stage("已发布编号材料", observed.path, "正式引用材料复核")
            _recheck_observation(observed)

    def _publish(self, candidate):
        self._candidate = candidate
        self._check_isolation()
        self._check_materials()
        target = self.task_root / "recovery.json"
        self._stage("恢复清单", target, "完整清单成果准备")
        artifact = prepare_content_artifact(
            self._manifest_expected, encode_document(candidate),
            options=ContentPublicationOptions(
                durable_publication=self.durable_publication,
            ),
        )
        self._artifact = artifact
        self._check_materials()
        self._stage("恢复清单", target, "正式清单发布")
        try:
            result = publish_content(artifact)
        except ContentPublicationError as error:
            self._publication_results.append(error.result)
            raise
        self._publication_results.append(result)
        # 正常发布事实先保留。随后固定观察失败不抹去已发生发布。
        self._document = candidate
        self._candidate = None
        self._stage("恢复清单", target, "已发布清单固定观察")
        actual = result.observation
        if not isinstance(actual, FileObservation):
            raise RuntimeError("正常清单发布缺少正式成果观察")
        if (
            actual.path != target
            or actual.kind != "file"
            or actual.object_identity != ObjectIdentity(*artifact.identity)
            or actual.link_count != 1
            or actual.attributes.st_size != artifact.length
            or actual.digest != artifact.digest
        ):
            raise OSError("已发布清单固定观察与正式成果不符")
        # 只关联真实观察和已有raw，不人为拼装缺失身份或属性的观察。
        self._manifest_expected = replace(
            actual, source_bytes=candidate.raw,
        )
        self._artifact = None

    def _raise_failure(self, error):
        result = None
        if isinstance(error, InternalMaterialError):
            self._active_material = error.material
            self._stage("编号材料", error.result.current, error.result.phase)
            result = error.result
        elif isinstance(error, ContentPublicationError):
            self._stage("恢复清单", error.result.target, error.result.phase)
            result = error.result
        elif isinstance(error, FilesystemChangeError):
            self._stage(self.role, error.result.target, error.result.phase)
            result = error.result
        raise ProtectionPublicationError(self, error, result) from error

    def discard_before_work(self):
        """放弃工作开始前的本次备份；失败由所属业务提示人工处理。

        只删除本任务实际创建且身份未变的唯一目录，
        不比较工作文件，不删除其他任务或原有父目录。
        """
        import os
        import stat
        from core.filesystem_changes import (
            execute_tree_removal, prepare_tree_removal,
        )

        if self._document is not None and any(
            unit["fact"]["state"] != "prepared"
            for unit in self._document.value["units"]
        ):
            raise ValueError("工作开始后禁止按失败备份目录清理")
        target, chain = checked_chain(
            self.task_root, include_observation=True,
        )
        if chain[-1][1] == "absent":
            return
        if chain[-1][1] != "directory":
            raise ValueError("无法确认本次备份任务目录所有权，未删除")
        owned = self._directory_state.require(target)
        # 备份失败的清理范围仅为本次任务目录，不是工作恢复范围。
        # 先固定完整普通树；活动链接或共享文件导致停止并提示人工处理。
        current = observe_tree(target)
        if current.directories[0][1].object_identity != owned.object_identity:
            raise ValueError("失败备份任务目录身份变化，禁止清理")
        if any(member.link_count != 1 for _, member in current.files):
            raise ValueError("失败备份包含共享文件，禁止通用清理")
        for _, member in current.files:
            fixed = recheck_file(member)
            mode = stat.S_IMODE(fixed.attributes.st_mode)
            if not mode & stat.S_IWRITE:
                os.chmod(member.path, mode | stat.S_IWRITE)
        removal = prepare_tree_removal(current)
        execute_tree_removal(removal)

    def protect_all(self):
        """三个入口共用：全部材料、一次完整清单、首个工作修改屏障。

        当前原字节来自所属预演已经取得的固定观察。
        材料保存消费这些字节，不重新生成修改基线。
        失败清理只执行一次，清理失败直接交给人工处理。
        """
        if self._document is not None or self.backup_completed:
            raise ValueError("本次备份屏障已消费，禁止重复备份")
        self._enter()
        try:
            prepared = False
            try:
                try:
                    self._initialize()
                    for member in self.projection.objects:
                        if member.original_type == "absent":
                            continue
                        number = member.object_id
                        expected = self._material_expected[number]
                        self._ensure_material_parents(expected)
                        self._stage(
                            "原名材料", member.target, "固定最初来源材料准备",
                        )
                        material = prepare_internal_material(
                            member.before, expected,
                            durable_publication=self.durable_publication,
                        )
                        self._active_material = material
                        self._stage(
                            "编号材料", expected.path, "正式原状态材料发布",
                        )
                        receipt = publish_internal_material(material)
                        self._material_results[number] = receipt
                        self._stage(
                            "编号材料", receipt.target, "正式材料固定观察",
                        )
                        actual = receipt.observation
                        if not isinstance(actual, (FileObservation, TreeObservation)):
                            raise RuntimeError("正常内部材料缺少正式成果观察")
                        if actual.path != receipt.target:
                            raise OSError("正式材料成果位置不对应固定材料结果")
                        validate_material_state(
                            actual, member.original, target=member.target,
                        )
                        self._material_observations[number] = actual
                        self._active_material = None
                    candidate = build_document(
                        self.projection, self.task_root,
                        self.task_id, self.task_kind, self._material_results,
                    )
                    self._material_layout = _check_material_layout(
                        self.task_root, candidate,
                        self._material_observations,
                    )
                    self._publish(candidate)
                    prepared = True
                except (OSError, ValueError) as error:
                    self._raise_failure(error)
            finally:
                self._busy = False
                if not prepared:
                    self._failed = True
                    if cleanup_content_artifact(self._artifact):
                        self._artifact = None

            self.recheck_all_before_work()
            try:
                self._stage(
                    "备份完成凭据", self._completion_expected.path,
                    "全量备份完成凭据准备",
                )
                self._artifact = prepare_content_artifact(
                    self._completion_expected,
                    (self.task_id + "\n").encode("utf-8"),
                    options=ContentPublicationOptions(
                        durable_publication=self.durable_publication,
                    ),
                )
                self._stage(
                    "备份完成凭据", self._completion_expected.path,
                    "全量备份完成凭据发布",
                )
                try:
                    receipt = publish_content(self._artifact)
                except ContentPublicationError as error:
                    self._publication_results.append(error.result)
                    raise
                self._publication_results.append(receipt)
                self._stage(
                    "备份完成凭据", self._completion_expected.path,
                    "已发布完成凭据固定观察",
                )
                completion = receipt.observation
                if not isinstance(completion, FileObservation):
                    raise RuntimeError("正常完成凭据发布缺少正式成果观察")
                if (
                    completion.path != self._completion_expected.path
                    or completion.kind != "file"
                    or completion.object_identity
                    != ObjectIdentity(*self._artifact.identity)
                    or completion.link_count != 1
                    or completion.attributes.st_size != self._artifact.length
                    or completion.digest != self._artifact.digest
                ):
                    raise OSError("已发布备份完成凭据与固定成果不符")
                self._completion_expected = replace(
                    completion, source_bytes=self._artifact.content,
                )
                self._artifact = None
            except (OSError, ValueError) as error:
                self._failed = True
                if cleanup_content_artifact(self._artifact):
                    self._artifact = None
                raise ProtectionPublicationError(self, error) from error
            self.backup_completed = True
            return self._document
        except ProtectionPublicationError:
            try:
                self.discard_before_work()
            except (OSError, ValueError) as cleanup_error:
                self.backup_cleanup_error = cleanup_error
                self.backup_cleanup_report = (
                    "全量备份阶段失败，未开始工作目标修改。\n"
                    "本次备份任务目录清理失败，请人工删除，勿用于恢复：\n"
                    f"{self.task_root}\n{cleanup_error}"
                )
            else:
                self.backup_cleanup_report = (
                    "全量备份阶段失败，未开始工作目标修改；"
                    "本次备份任务目录已清理。\n"
                    "其他备份任务及原有父目录没有删除。"
                )
            raise

    def recheck_all_before_work(self):
        """全部保护后核对首个工作改变前的固定事实。

        后续自有工作改变不得再比回最初状态。
        用户确认、正式来源任务复核和具体工作授权归所属协调。
        """
        self._enter()
        completed = False
        try:
            try:
                if self._document is None or tuple(
                    unit["id"] for unit in self._document.value["units"]
                ) != tuple(unit.unit_id for unit in self.projection.units):
                    raise ValueError("全部必要保护尚未正式发布")
                if self._document is not None and any(
                    unit["fact"]["state"] != "prepared"
                    for unit in self._document.value["units"]
                ):
                    raise ValueError("全量初始屏障只能用于首个工作开始之前")
                self._check_isolation()
                self._check_materials()
                if self._document is not None:
                    recheck_file(self._manifest_expected)
                seen = set()
                for unit in self.projection.units:
                    for observed in (
                        *(effect.before for effect in unit.effects),
                        *unit.fixed.reads,
                    ):
                        key = identity(observed.path)
                        if key in seen:
                            continue
                        seen.add(key)
                        self._stage("工作固定前提", observed.path, "全量保护后复核")
                        _recheck_observation(observed)
                completed = True
            except (OSError, ValueError) as error:
                self._raise_failure(error)
        finally:
            self._busy = False
            if not completed:
                self._failed = True

    def _update_fact(self, unit_id, state, results):
        self._enter()
        completed = False
        try:
            try:
                if not self.backup_completed or self._document is None:
                    raise ValueError("全量备份屏障尚未完成，禁止发布工作事实")
                self._stage(
                    "备份完成凭据", self._completion_expected.path,
                    "工作事实发布前固定完成凭据复核",
                )
                recheck_file(self._completion_expected)
                candidate = with_execution_fact(
                    self._document, unit_id, state, results,
                )
                self._publish(candidate)
                self._stage("恢复清单", self.task_root / "recovery.json", state)
                completed = True
                return self._document
            except (OSError, ValueError) as error:
                self._raise_failure(error)
        finally:
            self._busy = False
            if not completed:
                self._failed = True
                if cleanup_content_artifact(self._artifact):
                    self._artifact = None

    def begin(self, unit_id):
        """正常返回才满足started屏障，工作目录创建必须在其后。"""
        return self._update_fact(unit_id, "started", ())

    def complete(self, unit_id, results):
        """消费真实步骤；所属工作协调须先核验预期、来源及正式时间。"""
        return self._update_fact(unit_id, "completed", tuple(results))

    def uncertain(self, unit_id, results=()):
        """记录实际前缀，不从现场补造没有正式结果的调用。"""
        return self._update_fact(unit_id, "uncertain", tuple(results))
