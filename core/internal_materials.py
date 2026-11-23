# -*- coding: utf-8 -*-

"""独立内部材料、Windows不可覆盖名称提交及自有材料清理。

编号分配、保护范围、最初状态、清单引用及started归保护协调。
来源只读，不生成恢复清单，不自动重试、回滚或删除正式编号。
"""

import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import mkdtemp

from core.atomic_file import sync_directory
from core.execution_ledger import ExecutionLedger
from core.content_publication import (
    ContentPublicationOptions, PreparedContentArtifact,
    _recheck_copy_source, _verify_artifact, _verify_published_atomic,
    dispose_content_artifact, prepare_content_artifact,
    prepare_copy_content_artifact,
)
from core.file_observation import (
    FileObservation, ObjectIdentity, TreeObservation,
    observe_file, observe_tree, recheck_file, recheck_tree,
)
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult,

    _recheck_tree_copy_source, tree_execution_results,
    execute_filesystem_change, execute_tree_copy, execute_tree_removal,
    prepare_directory_creation, prepare_empty_directory_removal,
    prepare_tree_copy, prepare_tree_removal,
    _validate_tree_observation,
    execute_directory_attributes, prepare_directory_attributes,
)
from core.recovery_paths import (
    checked_chain, entity_type_from_attributes, overlaps,
)
from core.link_paths import (
    LinkPathObservation, encode_link_specification,
    recheck_link_path, specification_from_link,
)


def _material_source_path(source):
    """材料来源位置指向被保护入口，不指向链接目标。"""
    return (
        source.actual_path
        if isinstance(source, LinkPathObservation) else source.path
    )


def _material_source_kind(source):
    """结果类型表达原对象语义；材料实体自身仍是普通文件或目录。"""
    if isinstance(source, LinkPathObservation):
        return source.kind
    return "directory" if isinstance(source, TreeObservation) else "file"


@dataclass(frozen=True, init=False)
class PreparedInternalMaterial:
    """固定来源、独立暂存、一次资格及活动子核心现场。"""

    source: FileObservation | TreeObservation | LinkPathObservation
    expected: FileObservation
    durable_publication: bool
    _artifact: PreparedContentArtifact | None
    _container: Path | None
    _container_identity: ObjectIdentity | None
    _copy: object
    _staged_tree: TreeObservation | None
    _execution: ExecutionLedger
    _lifecycle: str
    _current: Path | None
    _phase: str
    _publication: str
    _publication_completed: bool
    _confirmation_completed: bool

    def __init__(self, *arguments, **keywords):
        raise TypeError("内部材料必须通过正式准备过程创建")

    @property
    def staging_path(self):
        if self._artifact is not None:
            return self._artifact.path
        return None if self._container is None else self._container / "material"

    @property
    def container_path(self):
        return self._container

    @property
    def results(self):
        return self._execution.results

    @property
    def current(self):
        return self._current

    @property
    def phase(self):
        return self._phase

    @property
    def active_change(self):
        """失败子核心只读保留，不恢复其执行资格。"""
        return self._execution.active_change


@dataclass(frozen=True)
class InternalMaterialResult:
    source: Path
    target: Path
    kind: str
    phase: str
    current: Path | None
    staging_path: Path | None
    container_path: Path | None
    name_call_attempted: bool
    publication: str
    publication_completed: bool
    confirmation_completed: bool
    cleanup_state: str
    results: tuple
    error: OSError | ValueError | None = None
    observation: FileObservation | TreeObservation | None = None

    @property
    def succeeded(self):
        return (
            self.error is None
            and self.publication_completed
            and self.confirmation_completed
            and self.cleanup_state in ("not_required", "removed")
        )


class InternalMaterialError(OSError):
    """内部对象失败，不冒充用户工作目标内容发布错误。"""

    def __init__(self, result, material):
        self.result = result
        self.material = material
        super().__init__(
            f"独立内部材料流程未完整结束：{result.source} -> {result.target}\n"
            f"材料类型：{result.kind}；阶段：{result.phase}\n"
            f"当前位置：{result.current or '不适用'}\n"
            f"暂存材料：{result.staging_path or '未取得'}\n"
            f"自有容器：{result.container_path or '不适用'}\n"
            f"编号名称调用已发起：{'是' if result.name_call_attempted else '否'}\n"
            f"名称发布状态：{result.publication}\n"
            f"正式材料已核验：{'是' if result.publication_completed else '否'}\n"
            f"必要确认已完成：{'是' if result.confirmation_completed else '否'}\n"
            f"清理状态：{result.cleanup_state}\n"
            "内部材料错误不证明用户工作目标已经改变。"
            "名称调用报错不能证明没有副作用。"
            "不自动重试、覆盖、回滚或删除已发布编号材料。\n"
            f"{result.error}"
        )


def _stage(material, current, phase):
    object.__setattr__(material, "_current", current)
    object.__setattr__(material, "_phase", phase)


def _append_results(material, results):
    material._execution.append_results(tuple(results))


def _removed_container(material):
    """复合结果按正式类型消费，不要求内容结果具有名称步骤字段。"""
    return any(
        isinstance(result, FilesystemChangeResult)
        and result.kind == "remove_empty_directory"
        and result.target == material.container_path
        and result.succeeded
        for result in material.results
    )


def _cleanup_state(material):
    """只表达已确认事实；retained_unverified不认证完整性。"""
    if material._lifecycle in ("disposed", "cleanup_confirmation_pending"):
        return "removed"
    if material._publication == "published" and material._publication_completed:
        if material.container_path is None:
            return "not_required"
        return "removed" if _removed_container(material) else "unknown"
    if material._publication != "not_attempted":
        return "unknown"
    if material._lifecycle == "cleanup_failed":
        return "unknown"
    if material._artifact is not None:
        return "retained_unverified"
    if material.container_path is not None:
        return (
            "retained_unverified"
            if material._staged_tree is not None else "incomplete_or_unknown"
        )
    return "not_acquired"


def _result(material, error=None, *, observation=None):
    return InternalMaterialResult(
        _material_source_path(material.source), material.expected.path,
        _material_source_kind(material.source),
        material.phase, material.current, material.staging_path,
        material.container_path,
        material._publication != "not_attempted",
        material._publication, material._publication_completed,
        material._confirmation_completed, _cleanup_state(material),
        material.results, error, observation,
    )


def _validate_inputs(source, expected, durable_publication):
    if os.name != "nt":
        raise OSError("内部材料不可覆盖名称提交仅支持本项目Windows环境")
    if not isinstance(source, (
        FileObservation, TreeObservation, LinkPathObservation,
    )):
        raise TypeError("内部材料来源必须是正式文件、链接入口或完整树观察")
    prepare_directory_creation(expected)
    if type(durable_publication) is not bool:
        raise TypeError("内部材料持久化开关必须是布尔值")
    if overlaps(_material_source_path(source), expected.path.parent):
        raise ValueError("内部材料来源与暂存父目录不得重叠")
    if isinstance(source, LinkPathObservation):
        if (
            source.follow_final_link
            or source.entry_link is None
            or source.entry_link.entry_path != source.actual_path
            or source.entry_link.object_identity != source.object_identity
            or source.entry_link.kind != source.kind
        ):
            raise ValueError("链接材料要求固定且不跟随的末级入口事实")
        # 完整状态拥有定义；此处只校验材料保存适用性，不查询磁盘。
        from core.protection_projection import observation_state
        observation_state(source)
    elif isinstance(source, FileObservation):
        if (
            source.kind != "file"
            or not isinstance(source.source_bytes, bytes)
            or source.attributes is None
            or source.object_identity is None
            or type(source.mtime_ns) is not int
            or source.mtime_ns != source.attributes.st_mtime_ns
        ):
            raise ValueError("内部文件材料缺少固定完整bytes、身份及正式时间")
    elif source.links:
        # 原结构树不能作为普通复制树：链接须保存为定义材料，
        # 不能创建活动链接，也不能遗漏入口。
        _validate_tree_observation(source)
        from core.protection_projection import observation_state
        observation_state(source)
    else:
        # 复用完整来源契约；这里只校验，不扫描或改变磁盘。
        prepare_tree_copy(source, expected)


def _check_parent(material):
    _, parents = checked_chain(
        material.expected.path.parent, include_observation=True,
    )
    if any(kind != "directory" for _, kind in parents):
        raise ValueError("内部材料必要父目录必须由所属固定计划先行创建")


def _recheck_source(material):
    if isinstance(material.source, LinkPathObservation):
        return recheck_link_path(material.source)
    if isinstance(material.source, FileObservation):
        return _recheck_copy_source(material.source)
    if material.source.links:
        return _recheck_structure_material_source(material.source)
    return _recheck_tree_copy_source(material._copy)


def _check_container(material, *, empty=False):
    """只检查容器身份及直接成员，不再次读取材料树正文。"""
    target, chain = checked_chain(
        material.container_path, include_observation=True,
    )
    if chain[-1][1] != "directory":
        raise ValueError("内部材料容器不再是普通目录")
    attributes = target.lstat()
    if (
        entity_type_from_attributes(target, attributes) != "directory"
        or ObjectIdentity(attributes.st_dev, attributes.st_ino)
        != material._container_identity
    ):
        raise ValueError("内部材料容器不再归本次拥有")
    with os.scandir(target) as entries:
        names = tuple(entry.name for entry in entries)
    if set(names) != (set() if empty else {"material"}):
        raise ValueError("内部材料容器包含不属于本次的直接成员")
    if not empty:
        staged = material._staged_tree.directories[0][1]
        child = target / "material"
        attributes = child.lstat()
        if (
            entity_type_from_attributes(child, attributes) != "directory"
            or ObjectIdentity(attributes.st_dev, attributes.st_ino)
            != staged.object_identity
        ):
            raise ValueError("内部材料容器成员身份变化")


def _check_material_tree(expected):
    """固定材料完整性与基础属性，不用来源身份要求独立副本。"""
    current = recheck_tree(expected, check_mtime=True)
    directories = dict(expected.directories)
    files = dict(expected.files)
    for relative, directory in current.directories:
        fixed = directories[relative]
        if (
            stat.S_IMODE(directory.attributes.st_mode)
            != stat.S_IMODE(fixed.attributes.st_mode)
            or directory.attributes.st_mtime_ns
            != fixed.attributes.st_mtime_ns
        ):
            raise ValueError("独立材料目录基础属性变化")
    for relative, observed in current.files:
        if (
            observed.link_count != 1
            or stat.S_IMODE(observed.attributes.st_mode)
            != stat.S_IMODE(files[relative].attributes.st_mode)
        ):
            raise ValueError("独立材料文件链接数量或基础权限变化")
    return current


def _relocate_tree(expected, target):
    return replace(
        expected, path=target,
        directories=tuple(
            (relative, replace(directory, path=target / relative))
            for relative, directory in expected.directories
        ),
        files=tuple(
            (relative, replace(observed, path=target / relative))
            for relative, observed in expected.files
        ),
    )


def _remove_empty_container(material):
    _stage(material, material.container_path, "自有空容器身份及范围核验")
    _check_container(material, empty=True)
    expected = observe_tree(material.container_path)
    if (
        expected.directories[0][1].object_identity
        != material._container_identity
    ):
        raise ValueError("空容器不再归本次拥有")
    step = prepare_empty_directory_removal(expected)
    material._execution.scene = step
    _stage(material, material.container_path, "自有空容器移除")
    try:
        result = execute_filesystem_change(step)
    except FilesystemChangeError as error:
        _append_results(material, (error.result,))
        _stage(material, error.result.target, error.result.phase)
        raise
    _append_results(material, (result,))
    material._execution.scene = None


def prepare_internal_material(source, expected, *, durable_publication=True):
    """完整独立准备；不发布编号，不创建未计划材料父目录。"""
    _validate_inputs(source, expected, durable_publication)
    material = object.__new__(PreparedInternalMaterial)
    for name, value in (
        ("source", source), ("expected", expected),
        ("durable_publication", durable_publication),
        ("_artifact", None), ("_container", None),
        ("_container_identity", None), ("_copy", None),
        ("_staged_tree", None),
        ("_execution", ExecutionLedger(tree_execution_results)),
        ("_lifecycle", "preparing"),
        ("_current", expected.path),
        ("_phase", "固定编号不存在及必要父目录复核"),
        ("_publication", "not_attempted"),
        ("_publication_completed", False),
        ("_confirmation_completed", False),
    ):
        object.__setattr__(material, name, value)
    completed = False
    try:
        try:
            recheck_file(expected)
            _check_parent(material)
            if isinstance(source, LinkPathObservation):
                _stage(
                    material, source.actual_path,
                    "固定链接入口原定义及身份复核",
                )
                recheck_link_path(source)
                specification = specification_from_link(source.entry_link)
                content = encode_link_specification(
                    specification, entry_path=source.actual_path,
                )
                _stage(
                    material, expected.path,
                    "普通链接定义材料完整成果准备",
                )
                artifact = prepare_content_artifact(
                    expected, content,
                    options=ContentPublicationOptions(
                        durable_publication=durable_publication,
                    ),
                )
                object.__setattr__(material, "_artifact", artifact)
                if artifact.mechanism != "atomic_replace":
                    raise RuntimeError("链接定义材料必须形成独立普通文件成果")
            elif isinstance(source, FileObservation):
                _stage(material, source.path, "固定文件独立完整成果准备")
                artifact = prepare_copy_content_artifact(
                    source, expected, preserve_target_mode=False,
                    options=ContentPublicationOptions(
                        durable_publication=durable_publication,
                    ),
                )
                object.__setattr__(material, "_artifact", artifact)
                if artifact.mechanism != "atomic_replace":
                    raise RuntimeError("不存在编号目标必须形成独立文件成果")
            else:
                _stage(material, expected.path.parent, "独占内部材料容器创建")
                container = Path(mkdtemp(
                    dir=expected.path.parent,
                    prefix=f".{expected.path.name}.", suffix=".preparing",
                ))
                object.__setattr__(material, "_container", container)
                observed = observe_tree(container)
                if observed.content_records != (("", "directory", ""),):
                    raise ValueError("新内部材料容器不是空普通目录")
                object.__setattr__(
                    material, "_container_identity",
                    observed.directories[0][1].object_identity,
                )
                if source.links:
                    _stage(material, container / "material", "固定结构普通材料树生成")
                    staged = _prepare_structure_material_tree(material)
                else:
                    copy = prepare_tree_copy(
                        source, observe_file(container / "material"),
                        durable_publication=durable_publication,
                    )
                    object.__setattr__(material, "_copy", copy)
                    _stage(material, container / "material", "固定独立材料树复制")
                    material._execution.execute_active(
                        copy, execute_tree_copy,
                        stage=lambda current, phase: _stage(material, current, phase),
                    )
                    _stage(material, container / "material", "完整独立材料树核验")
                    staged = copy.final_observation
                if not isinstance(staged, TreeObservation):
                    raise RuntimeError("正常内部材料复制缺少最终树观察")
                object.__setattr__(material, "_staged_tree", staged)
                _check_container(material)

            _stage(
                material, _material_source_path(source),
                "完整材料准备后固定来源复核",
            )
            _recheck_source(material)
            _stage(material, expected.path, "完整材料准备后编号前提复核")
            recheck_file(expected)
            if durable_publication:
                _stage(material, expected.path.parent, "完整暂存材料父目录确认")
                sync_directory(expected.path.parent)
        except (OSError, ValueError) as error:
            raise InternalMaterialError(_result(material, error), material) from error
        completed = True
    finally:
        if not completed:
            object.__setattr__(material, "_lifecycle", "preparation_failed")

    object.__setattr__(material, "_lifecycle", "prepared")
    _stage(material, material.staging_path, "prepared")
    return material


def _rename_without_overwrite(source, target):
    """Windows rename拒绝既有目标，不使用replace或生产硬链接。"""
    if os.name != "nt":
        raise OSError("不可覆盖编号名称提交仅支持Windows")
    os.rename(source, target)


def publish_internal_material(material):
    """一次消费完整材料；失败不换机制、不删除正式编号或重试。"""
    if not isinstance(material, PreparedInternalMaterial):
        raise TypeError("编号发布必须消费PreparedInternalMaterial")
    if material._lifecycle != "prepared":
        raise ValueError("内部材料已消费或未完整准备，必须建立新材料")
    object.__setattr__(material, "_lifecycle", "attempted")
    try:
        _stage(
            material, _material_source_path(material.source),
            "编号发布前固定来源复核",
        )
        _recheck_source(material)
        _stage(material, material.expected.path, "编号发布前不存在及父链复核")
        recheck_file(material.expected)
        _check_parent(material)
        _stage(material, material.staging_path, "编号发布前完整材料及所有权复核")
        if material._artifact is not None:
            _verify_artifact(material._artifact, include_digest=True)
        else:
            _check_material_tree(material._staged_tree)
            _check_container(material)

        _stage(material, material.expected.path, "不可覆盖编号名称提交")
        object.__setattr__(material, "_publication", "unknown")
        if material._artifact is not None:
            object.__setattr__(material._artifact, "_lifecycle", "attempted")
        _rename_without_overwrite(material.staging_path, material.expected.path)
        object.__setattr__(material, "_publication", "published")

        _stage(material, material.expected.path, "正式独立材料结果核验")
        if material._artifact is not None:
            actual = _verify_published_atomic(material._artifact)
        else:
            actual = _check_material_tree(_relocate_tree(
                material._staged_tree, material.expected.path,
            ))
        _, chain = checked_chain(
            material.staging_path, include_observation=True,
        )
        if chain[-1][1] != "absent":
            raise OSError("编号名称正常返回，但原暂存名称仍存在")
        object.__setattr__(material, "_publication_completed", True)

        if material.durable_publication:
            _stage(material, material.expected.path.parent, "正式编号父目录确认")
            sync_directory(material.expected.path.parent)
            if material.container_path is not None:
                _stage(material, material.container_path, "暂存容器名称移出确认")
                sync_directory(material.container_path)
        object.__setattr__(material, "_confirmation_completed", True)
        if material.container_path is not None:
            _remove_empty_container(material)
            if material.durable_publication:
                _stage(material, material.expected.path.parent, "空容器清理后父目录确认")
                sync_directory(material.expected.path.parent)
    except (OSError, ValueError) as error:
        raise InternalMaterialError(_result(material, error), material) from error

    if material._artifact is not None:
        object.__setattr__(material._artifact, "_lifecycle", "completed")
    object.__setattr__(material, "_lifecycle", "completed")
    _stage(material, None, "completed")
    return _result(material, observation=actual)


def _make_staged_files_writable(material):
    """只解除自有独立材料的只读位，不修改来源或正式编号。"""
    for _, expected in material._staged_tree.files:
        current = observe_file(expected.path)
        if (
            current.object_identity != expected.object_identity
            or current.link_count != 1
        ):
            raise ValueError("取消材料不再是本次独立文件，禁止改变权限")
        mode = stat.S_IMODE(current.attributes.st_mode)
        if not mode & stat.S_IWRITE:
            _stage(material, expected.path, "取消自有材料只读位解除")
            os.chmod(expected.path, mode | stat.S_IWRITE)


def _confirm_disposal(material):
    """删除已确认后只重试必要确认，不再次删除或扫描工作对象。"""
    object.__setattr__(material, "_lifecycle", "cleanup_confirmation_pending")
    if material.durable_publication:
        _stage(material, material.expected.path.parent, "材料取消后父目录确认")
        sync_directory(material.expected.path.parent)
    object.__setattr__(material, "_lifecycle", "disposed")
    _stage(material, None, "disposed")
    return _result(material)


def dispose_internal_material(material):
    """显式放弃完整未发布材料；删除结果未知或部分树失败后停止。

    文件删除前失败可以按共享成果所有权明确重试。
    已确认删除后同步失败只允许重试同步。
    树取消失败保留活动移除核心，不重新准备剩余删除资格。
    发布尝试后不提供通用删除资格。
    """
    if not isinstance(material, PreparedInternalMaterial):
        raise TypeError("材料取消必须消费PreparedInternalMaterial")
    if material._lifecycle not in (
        "prepared", "preparation_failed", "disposing",
        "cleanup_confirmation_pending",
    ):
        raise ValueError("内部材料已消费、清理或清理失败，禁止通用删除")
    if material._lifecycle == "cleanup_confirmation_pending":
        try:
            return _confirm_disposal(material)
        except (OSError, ValueError) as error:
            raise InternalMaterialError(_result(material, error), material) from error
    if material._artifact is None and material._staged_tree is None:
        raise ValueError("不完整材料树保留现场，禁止通用递归清理")

    object.__setattr__(material, "_lifecycle", "disposing")
    try:
        if material._artifact is not None:
            _stage(material, material.staging_path, "自有完整文件材料取消")
            dispose_content_artifact(material._artifact)
        else:
            _stage(material, material.staging_path, "取消前材料树及容器核验")
            _check_material_tree(material._staged_tree)
            _check_container(material)
            _make_staged_files_writable(material)
            removal = prepare_tree_removal(material._staged_tree)
            _stage(material, material.staging_path, "自有完整材料树取消移除")
            material._execution.execute_active(
                removal, execute_tree_removal,
                stage=lambda current, phase: _stage(material, current, phase),
            )
            _remove_empty_container(material)
        return _confirm_disposal(material)
    except (OSError, ValueError) as error:
        if material._lifecycle != "cleanup_confirmation_pending":
            if material._artifact is None:
                object.__setattr__(material, "_lifecycle", "cleanup_failed")
        raise InternalMaterialError(_result(material, error), material) from error
    finally:
        # 程序缺陷自然传播；树删除资格不因缺少包装结果而复活。
        if material._artifact is None and material._lifecycle == "disposing":
            object.__setattr__(material, "_lifecycle", "cleanup_failed")


def _recheck_structure_material_source(source):
    """比较原结构、定义、身份及适用属性，不认证新来源。"""
    current = recheck_tree(source, check_mtime=True)
    for original_members, current_members in (
        (source.directories, current.directories),
        (source.files, current.files),
    ):
        originals = dict(original_members)
        for relative, actual in current_members:
            original = originals[relative]
            if (
                stat.S_IMODE(actual.attributes.st_mode)
                != stat.S_IMODE(original.attributes.st_mode)
            ):
                raise ValueError("固定结构材料来源基础权限变化")
            if (
                isinstance(original, type(source.directories[0][1]))
                and actual.attributes.st_mtime_ns
                != original.attributes.st_mtime_ns
            ):
                raise ValueError("固定结构材料来源目录时间变化")
    return current


def _prepare_structure_material_tree(material):
    """现有内部材料流程的结构树生成部分，不提交正式名称。

    普通正文使用唯一文件执行核心，链接定义使用同一正文发布。
    每个材料位置只能来自固定普通目录、普通文件或链接入口。
    目标范围只接受正式成功结果及当前自有完整暂存，不忽略外部成员。
    """
    from core.fixed_content_execution import FixedContentExecution

    source = material.source
    root = material.container_path / "material"
    expected = observe_file(root)
    if expected.kind != "absent":
        raise ValueError("结构材料树固定目标必须不存在")
    directories = {}
    files = {}
    execution = None

    def record(receipt):
        _append_results(material, (receipt,))

    def stage(position, phase):
        _stage(material, position, phase)

    def check():
        _recheck_structure_material_source(source)
        if not directories:
            recheck_file(expected)
            return None
        expected_files = dict(files)
        artifact = None if execution is None else execution.artifact
        if artifact is not None and artifact._lifecycle == "prepared":
            relative = artifact.path.relative_to(root).as_posix()
            if relative in directories or relative in expected_files:
                raise ValueError("结构材料暂存与正式成员冲突")
            expected_files[relative] = (
                ObjectIdentity(*artifact.identity), artifact.digest,
            )
        current = observe_tree(root, include_mtime=True)
        if (
            set(dict(current.directories)) != set(directories)
            or set(dict(current.files)) != set(expected_files)
            or current.links
        ):
            raise ValueError("结构材料树偏离本次实际成功前缀")
        for relative, directory in current.directories:
            if directory.object_identity != directories[relative]:
                raise ValueError("结构材料目录身份变化")
        for relative, observed in current.files:
            identifier, digest = expected_files[relative]
            if (
                observed.object_identity != identifier
                or observed.link_count != 1
                or observed.digest != digest
            ):
                raise ValueError("结构材料文件身份、独立性或正文变化")
        return current

    ordered_directories = sorted(
        source.directories,
        key=lambda member: (
            len(Path(member[0]).parts), member[0].encode("utf-8"),
        ),
    )
    for relative, _ in ordered_directories:
        target = root / relative
        stage(target, "结构材料目录创建前固定范围复核")
        check()
        fixed = expected if not relative else observe_file(target)
        if fixed.kind != "absent":
            raise ValueError("固定结构材料目录位置已出现")
        step = prepare_directory_creation(fixed)
        material._execution.scene = step
        try:
            receipt = execute_filesystem_change(step)
        except FilesystemChangeError as error:
            record(error.result)
            raise
        record(receipt)
        directories[relative] = receipt.created_directory.object_identity
        material._execution.scene = None
        check()

    ordinary_files = dict(source.files)
    definitions = dict(
        (relative, specification_from_link(observed.entry_link))
        for relative, observed in source.links
    )
    positions = sorted(
        (*ordinary_files, *definitions),
        key=lambda relative: relative.encode("utf-8"),
    )
    for relative in positions:
        target = root / relative
        stage(target, "结构材料文件准备前固定范围复核")
        check()
        fixed = observe_file(target)
        if fixed.kind != "absent":
            raise ValueError("固定结构材料文件位置已出现")
        if relative in ordinary_files:
            execution = FixedContentExecution(
                fixed, source=ordinary_files[relative],
                preserve_target_mode=False,
                options=ContentPublicationOptions(
                    durable_publication=material.durable_publication,
                ),
                desired_digest=ordinary_files[relative].digest,
            )
        else:
            execution = FixedContentExecution(
                fixed,
                encode_link_specification(
                    definitions[relative], entry_path=source.path / relative,
                ),
                options=ContentPublicationOptions(
                    durable_publication=material.durable_publication,
                ),
            )
        material._execution.scene = execution
        actual = execution.execute(
            recheck_context=check, record_step=record, stage=stage,
        )
        files[relative] = (actual.object_identity, actual.digest)
        material._execution.scene = None
        execution = None
        check()

    for relative, original in reversed(ordered_directories):
        stage(root / relative, "结构材料目录属性前固定范围复核")
        current = check()
        directory = dict(current.directories)[relative]
        attributes = original.attributes
        step = prepare_directory_attributes(
            directory,
            file_mode=stat.S_IMODE(attributes.st_mode),
            atime_ns=attributes.st_atime_ns,
            mtime_ns=attributes.st_mtime_ns,
            durable_publication=material.durable_publication,
        )
        material._execution.scene = step
        try:
            receipt = execute_directory_attributes(step)
        except FilesystemChangeError as error:
            record(error.result)
            raise
        record(receipt)
        material._execution.scene = None

    stage(root, "完整结构普通材料树最终核验")
    actual = check()
    expected_records = tuple(sorted(
        (
            *((relative, "directory", "") for relative in directories),
            *((relative, "file", digest)
              for relative, (_, digest) in files.items()),
        ),
        key=lambda record: record[0].encode("utf-8"),
    ))
    if actual.content_records != expected_records:
        raise OSError("结构普通材料树未完整覆盖固定来源")
    if material.durable_publication:
        sync_directory(material.container_path)
    return actual


def validate_material_state(observed, original, *, target):
    """普通材料与原结构的一一对应；不读取现场，不创建活动链接。

    原定义由ContentState拥有。链接位置要求唯一编码生成的普通
    定义材料摘要，不能把定义JSON误认作原文件正文。
    额外、缺失、类型变化、活动链接或共享硬链接材料均拒绝。
    """
    import hashlib
    from core.protection_projection import validate_content_state

    validate_content_state(original, target=target)
    if original.kind == "absent":
        raise ValueError("不存在原状态不得提供材料")
    definitions = dict(original.link_definitions)
    required = tuple(
        (
            relative,
            "file" if relative in definitions else kind,
            hashlib.sha256(encode_link_specification(
                definitions[relative], entry_path=target / relative,
            )).hexdigest() if relative in definitions else digest,
        )
        for relative, kind, digest in original.records
    )
    if isinstance(observed, TreeObservation):
        if observed.links:
            raise ValueError("正式材料不得包含活动链接")
        kind = "directory"
        records = observed.content_records
        files = tuple(member for _, member in observed.files)
    elif isinstance(observed, FileObservation):
        kind = observed.kind
        records = (
            (("", "file", observed.digest),) if kind == "file" else ()
        )
        files = (observed,) if kind == "file" else ()
    else:
        raise TypeError("材料必须是正式普通文件或普通树观察")
    required_kind = (
        "directory" if original.kind == "directory" else "file"
    )
    if (
        kind != required_kind or records != required
        or any(member.link_count != 1 for member in files)
    ):
        raise ValueError("普通材料与固定原结构的位置、类型或完整内容不对应")
    return observed
