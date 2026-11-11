# -*- coding: utf-8 -*-

"""唯一投影的材料、严格依据及工作开始屏障协调。

不解析OP、不决定用户选择、不执行工作改变。
任务位置由所属业务固定；内部创建只消费固定不存在链。
正式材料保留原名和目录关系；只读取version 4，不兼容旧布局。
"""

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from core.backup_material_layout import build_backup_material_paths
from core.content_publication import (
    ContentPublicationError,
    ContentPublicationOptions,
    dispose_content_artifact,
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
from core.filesystem_changes import (
    FilesystemChangeError,
    execute_filesystem_change,
    prepare_directory_creation,
)
from core.internal_materials import (
    InternalMaterialError,
    prepare_internal_material,
    publish_internal_material,
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
    manifest: FileObservation
    document: RecoveryDocument
    materials: object


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


def _absent_from_chain(observed, target):
    index = next(
        number for number, (path, _) in enumerate(observed.chain)
        if path == target
    )
    chain = observed.chain[:index + 1]
    if chain[-1][1] != "absent":
        raise ValueError("内部目录计划只能消费固定不存在事实")
    return FileObservation(
        target, chain, "absent",
        None, None, None, None, None, None,
    )


def _absent_child(parent, name):
    target = parent.path / name
    return FileObservation(
        target, (*parent.chain, (target, "absent")), "absent",
        None, None, None, None, None, None,
    )


def _recheck_observation(observed):
    if isinstance(observed, TreeObservation):
        return recheck_tree(observed)
    if isinstance(observed, FileObservation):
        return recheck_file(observed)
    raise TypeError("固定来源复核必须消费正式观察")


def _material_records(observed):
    if isinstance(observed, TreeObservation):
        if any(member.link_count != 1 for _, member in observed.files):
            raise ValueError("正式目录材料必须具有独立单链接文件")
        return "directory", observed.content_records
    if observed.kind != "file" or observed.link_count != 1:
        raise ValueError("正式文件材料必须是独立单链接普通文件")
    return "file", (("", "file", observed.digest),)


def _observe_material(path, original, *, include_bytes):
    if original["kind"] == "directory":
        observed = observe_tree(
            path, include_bytes=include_bytes, include_mtime=include_bytes,
        )
    elif original["kind"] == "file":
        observed = observe_file(
            path, include_bytes=include_bytes, include_digest=True,
            include_mtime=include_bytes,
        )
    else:
        raise ValueError("不存在原状态没有编号材料")
    kind, records = _material_records(observed)
    if (
        kind != original["kind"]
        or records != tuple(tuple(member) for member in original["records"])
    ):
        raise ValueError(f"正式编号材料缺失、类型错误或损坏：{path}")
    return observed


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
    if manifest.kind != "file":
        raise ValueError("缺少正式recovery.json；不读取旧manifest")
    document = decode_document(manifest.source_bytes)
    _check_evidence_isolation(task, document, tuple(forbidden_roots))
    materials = {}
    for entry in document.value["objects"]:
        if entry["material"] is not None:
            materials[entry["id"]] = _observe_material(
                task / "files" / entry["material"],
                entry["original"], include_bytes=True,
            )
    evidence = RecoveryEvidence(
        task, manifest, document, MappingProxyType(materials),
    )
    # 晚材料获取可能跨越早材料变化；新边界复核只比较，
    # 后续工作仍消费第一次获取的完整bytes。
    recheck_recovery_evidence(evidence)
    return evidence


def recheck_recovery_evidence(evidence, *, forbidden_roots=()):
    """执行期固定来源比较，不重新解析或替代正式原时间。"""
    if not isinstance(evidence, RecoveryEvidence):
        raise TypeError("来源复核必须消费RecoveryEvidence")
    _check_evidence_isolation(
        evidence.task_root, evidence.document, tuple(forbidden_roots),
    )
    recheck_file(evidence.manifest)
    for observed in evidence.materials.values():
        _recheck_observation(observed)
    recheck_file(evidence.manifest)


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
        self._directories = tuple(
            _absent_from_chain(self._task_expected, path)
            for path, kind in self._task_expected.chain
            if kind == "absent"
        )
        self._files_expected = _absent_child(self._task_expected, "files")
        self._manifest_expected = _absent_child(
            self._task_expected, "recovery.json",
        )
        self._objects = {
            member.object_id: member for member in projection.objects
        }
        self.material_paths = build_backup_material_paths(
            projection, base=material_base,
        )
        # 全部位置在首个材料准备之前固定；逐单元保护不改变布局。
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
        self._material_directories = {}
        self._units = {
            member.unit_id: member for member in projection.units
        }
        self._document = None
        self._candidate = None
        self._material_results = {}
        self._material_observations = {}
        self._directory_results = []
        self._publication_results = []
        self._active_material = None
        self._artifact = None
        self._failed = False
        self._busy = False
        self._initialized = False
        self._files_created = False
        self._included = ()
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

    def _create_directory(self, expected, role):
        self._stage(role, expected.path, "固定内部目录创建")
        try:
            result = execute_filesystem_change(
                prepare_directory_creation(expected),
            )
        except FilesystemChangeError as error:
            self._directory_results.append(error.result)
            raise
        self._directory_results.append(result)

    def _initialize(self):
        if self._initialized:
            return
        self._check_isolation()
        for expected in self._directories:
            self._create_directory(expected, "内部任务目录")
        self._initialized = True

    def _ensure_files_directory(self):
        if not self._files_created:
            self._create_directory(self._files_expected, "编号材料目录")
            self._files_created = True

    def _ensure_material_parents(self, expected):
        """只创建预演固定的材料父目录，正常创建结果复用一次。"""
        self._ensure_files_directory()
        root = self.task_root / "files"
        for path, kind in expected.chain[:-1]:
            if identity(path) == identity(root) or not contains(root, path):
                continue
            if kind != "absent":
                raise ValueError("新任务材料父目录没有固定不存在前提")
            key = identity(path)
            created = self._material_directories.get(key)
            if created is not None:
                target, chain = checked_chain(path, include_observation=True)
                if chain[-1][1] != "directory":
                    raise ValueError("已创建材料父目录存在性或类型变化")
                attributes = target.lstat()
                if (
                    ObjectIdentity(attributes.st_dev, attributes.st_ino)
                    != created.object_identity
                ):
                    raise ValueError("已创建材料父目录身份变化")
                continue
            self._create_directory(
                _absent_from_chain(expected, path), "材料路径目录",
            )
            receipt = self._directory_results[-1]
            if not receipt.succeeded or receipt.created_directory is None:
                raise RuntimeError("正常材料目录创建缺少正式对象结果")
            self._material_directories[key] = receipt.created_directory

    def _check_materials(self):
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
        actual = observe_file(target, include_digest=True)
        if (
            actual.kind != "file"
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

    def _dispose_manifest_staging(self):
        artifact = self._artifact
        if artifact is None or artifact._lifecycle in ("completed", "disposed"):
            return
        if artifact.mechanism == "in_place" and artifact._lifecycle == "attempted":
            # 完整硬链接应急由发布失败事实保留，禁止通用清理。
            return
        try:
            dispose_content_artifact(artifact)
        except (OSError, ValueError) as error:
            print(
                f"[清单成果放弃清理失败] {artifact.path}：{error}",
                file=sys.stderr,
            )
            return
        self._artifact = None

    def protect(self, unit_id, *, removed_link_counts=None):
        """按投影顺序保护，最初材料只准备一次。

        前序正常名称移除的数量只投影本次材料来源链接约束；
        最初内容状态和清单时间继续直接消费唯一原投影。
        数量由所属工作协调从真实步骤提供，不从现场重新授权。
        """
        self._enter()
        completed = False
        try:
            try:
                if type(unit_id) is not str or unit_id not in self._units:
                    raise ValueError("保护单元不属于固定投影")
                unit = self._units[unit_id]
                if unit_id in self._included:
                    raise ValueError("单元保护已经发布，不重复消费")
                if self.projection.units[len(self._included)] is not unit:
                    raise ValueError("逐动作保护必须保持固定投影顺序")
                self._initialize()
                from core.file_observation import project_removed_file_links
                for number in unit.object_ids:
                    member = self._objects[number]
                    source = project_removed_file_links(
                        member.before, removed_link_counts,
                    )
                    if member.original_type == "absent":
                        continue
                    if number in self._material_results:
                        continue
                    expected = self._material_expected[number]
                    self._ensure_material_parents(expected)
                    self._stage(
                        "原名材料", member.target, "固定最初来源材料准备",
                    )
                    material = prepare_internal_material(
                        source, expected,
                        durable_publication=self.durable_publication,
                    )
                    self._active_material = material
                    self._stage(
                        "编号材料", material.expected.path, "正式编号材料发布",
                    )
                    receipt = publish_internal_material(material)
                    self._material_results[number] = receipt
                    original = {
                        "kind": member.original.kind,
                        "records": member.original.records,
                    }
                    self._stage(
                        "编号材料", receipt.target, "正式编号材料固定观察",
                    )
                    self._material_observations[number] = _observe_material(
                        receipt.target, original, include_bytes=False,
                    )
                    self._active_material = None
                selected = (*self._included, unit_id)
                needed = {
                    number for member in self.projection.units
                    if member.unit_id in selected for number in member.object_ids
                }
                receipts = {
                    number: receipt
                    for number, receipt in self._material_results.items()
                    if number in needed
                }
                candidate = build_document(
                    self.projection, self.task_root, self.task_id,
                    self.task_kind, receipts, unit_ids=selected,
                    previous_document=self._document,
                )
                self._publish(candidate)
                self._included = selected
                self._stage("恢复清单", self.task_root / "recovery.json", "prepared")
                completed = True
                return self._document
            except (OSError, ValueError) as error:
                self._raise_failure(error)
        finally:
            self._busy = False
            if not completed:
                self._failed = True
                self._dispose_manifest_staging()

    def protect_all(self):
        """全量必要保护及复核屏障，不执行工作步骤。"""
        for unit in self.projection.units[len(self._included):]:
            self.protect(unit.unit_id)
        self.recheck_all_before_work()
        return self._document

    def recheck_all_before_work(self):
        """全部保护后核对首个工作改变前的固定事实。

        后续自有工作改变不得再比回最初状态。
        用户确认、正式来源任务复核和具体工作授权归所属协调。
        """
        self._enter()
        completed = False
        try:
            try:
                if len(self._included) != len(self.projection.units):
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
                if self._document is None:
                    raise ValueError("尚无正常发布的正式保护依据")
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
                self._dispose_manifest_staging()

    def begin(self, unit_id):
        """正常返回才满足started屏障，工作目录创建必须在其后。"""
        return self._update_fact(unit_id, "started", ())

    def complete(self, unit_id, results):
        """消费真实步骤；所属工作协调须先核验预期、来源及正式时间。"""
        return self._update_fact(unit_id, "completed", tuple(results))

    def uncertain(self, unit_id, results=()):
        """记录实际前缀，不从现场补造没有正式结果的调用。"""
        return self._update_fact(unit_id, "uncertain", tuple(results))
