# -*- coding: utf-8 -*-

"""完整成果准备、一次发布及明确失败事实。

确认、保护、started和固定必要目录创建归所属业务。
不解析正文、不生成恢复清单、不自动重试或回滚。
"""

import hashlib
import os
import stat
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile

from core.atomic_file import sync_directory
from core.file_observation import FileObservation, observe_file, recheck_file
from core.hardlink_content import (
    HardlinkContentError, HardlinkContentExpectation,
    _publish_prepared_hardlink, _stream_digest, _write_all,
)
from core.recovery_paths import checked_chain, entity_type_from_attributes


@dataclass(frozen=True)
class ContentPublicationOptions:
    durable_publication: bool = False
    mtime_ns: int | None = None
    check_target_mtime: bool = False
    file_mode: int | None = None
    atime_ns: int | None = None


@dataclass(frozen=True, init=False)
class PreparedContentArtifact:
    """正式准备过程创建；生命周期表达资格，不替代工作结果。

    普通流式成果content=None表示正文由完整自有暂存承载，
    不表示零长度或未准备。硬链接成果始终具有完整bytes。
    """

    expected: FileObservation
    options: ContentPublicationOptions
    content: bytes | None
    path: Path
    identity: tuple
    length: int
    digest: str
    mechanism: str
    _lifecycle: str

    def __init__(self, *arguments, **keywords):
        raise TypeError("完整成果必须通过正式成果准备过程创建")


@dataclass(frozen=True)
class ContentPublicationResult:
    target: Path
    mechanism: str
    phase: str
    mutation_attempted: bool
    replacement: str
    content_completed: bool
    confirmation_completed: bool
    emergency_path: Path | None
    emergency_state: str
    error: OSError | ValueError | None = None

    @property
    def succeeded(self):
        return (
            self.error is None
            and self.content_completed
            and self.confirmation_completed
            and self.emergency_state in ("not_required", "removed")
        )


class ContentPublicationError(OSError):
    """失败持有所属目标的明确事实，不从诊断文字猜副作用。"""

    def __init__(self, result):
        self.result = result
        super().__init__(
            f"完整内容发布未完整结束：{result.target}\n"
            f"机制：{result.mechanism}；阶段：{result.phase}\n"
            f"目标改变调用已发起：{'是' if result.mutation_attempted else '否'}\n"
            f"名称替换状态：{result.replacement}\n"
            f"完整正文已确认：{'是' if result.content_completed else '否'}\n"
            f"本次确认已完成：{'是' if result.confirmation_completed else '否'}\n"
            f"应急成果状态：{result.emergency_state}\n"
            f"应急成果位置：{result.emergency_path or '不适用'}\n"
            "没有替换不能证明没有原地改变。"
            "本成果发布资格已消费，停止后续，不自动重试或回滚。\n"
            f"{result.error}"
        )


def _validate_inputs(expected, options):
    if not isinstance(expected, FileObservation):
        raise TypeError("内容发布必须消费固定FileObservation")
    if not isinstance(options, ContentPublicationOptions):
        raise TypeError("内容发布必须消费明确选项")
    if type(options.durable_publication) is not bool:
        raise TypeError("目录持久化开关必须是布尔值")
    if type(options.check_target_mtime) is not bool:
        raise TypeError("目标正式时间约束开关必须是布尔值")
    if options.mtime_ns is not None and type(options.mtime_ns) is not int:
        raise TypeError("正式修改时间必须是mtime_ns整数")
    if options.file_mode is not None and (
        type(options.file_mode) is not int
        or not 0 <= options.file_mode <= 0o7777
    ):
        raise TypeError("基础权限必须是合法权限位整数")
    if options.atime_ns is not None and type(options.atime_ns) is not int:
        raise TypeError("基础访问时间必须是atime_ns整数")
    if options.atime_ns is not None and options.mtime_ns is None:
        raise ValueError("基础访问时间必须同时具有明确修改时间")
    if expected.kind not in ("absent", "file"):
        raise ValueError("发布目标必须是不存在或普通文件")
    if not isinstance(expected.path, Path) or not expected.path.is_absolute():
        raise ValueError("发布目标必须具有明确绝对位置")
    if expected.kind == "file":
        if expected.source_bytes is None and expected.digest is None:
            raise ValueError("既有目标缺少固定原字节或指纹前提")
        if (
            expected.attributes is None
            or expected.object_identity is None
            or type(expected.link_count) is not int
            or expected.link_count < 1
        ):
            raise ValueError("既有目标缺少可靠属性、身份或链接数量")
        if options.check_target_mtime and expected.mtime_ns is None:
            raise ValueError("原观察未请求正式时间，不能补猜时间前提")


def _object_identity(attributes):
    return attributes.st_dev, attributes.st_ino


def _new_artifact(
    expected, options, content, path, identity, digest, mechanism, length,
):
    artifact = object.__new__(PreparedContentArtifact)
    for name, value in (
        ("expected", expected), ("options", options),
        ("content", content), ("path", path), ("identity", identity),
        ("length", length), ("digest", digest),
        ("mechanism", mechanism), ("_lifecycle", "prepared"),
    ):
        object.__setattr__(artifact, name, value)
    return artifact


def _check_target_access(expected, current):
    if current.kind != expected.kind:
        raise ValueError("固定目标存在性变化，必须重新准备任务")
    if current.kind == "absent":
        return
    if (
        current.object_identity != expected.object_identity
        or current.link_count != expected.link_count
    ):
        raise ValueError("固定目标身份或链接数量变化")
    if stat.S_IMODE(current.attributes.st_mode) != stat.S_IMODE(
        expected.attributes.st_mode
    ):
        raise ValueError("固定目标基础权限变化，必须重新准备任务")
    if os.name == "nt" and not current.attributes.st_mode & stat.S_IWRITE:
        raise PermissionError(f"目标为只读，不能覆盖：{expected.path}")


def _check_preparation_target(expected):
    """只检查准备适用性；不重新规划、不创建工作目录。"""
    _check_target_access(expected, observe_file(expected.path))
    _, parents = checked_chain(expected.path.parent, include_observation=True)
    if any(kind != "directory" for _, kind in parents):
        raise ValueError("成果准备要求必要父目录已存在且为普通目录")


def _recheck_target(artifact):
    current = recheck_file(
        artifact.expected,
        check_mtime=artifact.options.check_target_mtime,
    )
    _check_target_access(artifact.expected, current)
    return current


def _matches_artifact(observed, artifact):
    return (
        observed.kind == "file"
        and (observed.object_identity.device, observed.object_identity.inode)
        == artifact.identity
        and observed.link_count == 1
        and observed.attributes.st_size == artifact.length
    )


def _verify_artifact(artifact, *, include_digest):
    observed = observe_file(
        artifact.path, include_digest=include_digest,
        include_mtime=(
            artifact.mechanism == "atomic_replace"
            and artifact.options.mtime_ns is not None
        ),
    )
    if not _matches_artifact(observed, artifact):
        raise ValueError(f"完整成果存在性、身份、链接或长度变化：{artifact.path}")
    if include_digest and observed.digest != artifact.digest:
        raise ValueError(f"完整成果内容变化：{artifact.path}")
    if (
        artifact.mechanism == "atomic_replace"
        and artifact.options.mtime_ns is not None
        and observed.mtime_ns != artifact.options.mtime_ns
    ):
        raise ValueError(f"完整成果正式时间变化：{artifact.path}")
    if artifact.mechanism == "atomic_replace":
        mode = artifact.options.file_mode
        if mode is None and artifact.expected.kind == "file":
            mode = stat.S_IMODE(artifact.expected.attributes.st_mode)
        if (
            mode is not None
            and stat.S_IMODE(observed.attributes.st_mode) != mode
        ):
            raise ValueError(f"完整成果基础权限变化：{artifact.path}")
    return observed


def _remove_owned(path, owned_identity):
    target, chain = checked_chain(path, include_observation=True)
    if chain[-1][1] == "absent":
        return
    attributes = target.lstat()
    if (
        entity_type_from_attributes(target, attributes) != "file"
        or _object_identity(attributes) != owned_identity
        or attributes.st_nlink != 1
    ):
        raise ValueError(f"成果不再归本次拥有，禁止清理：{target}")
    if os.name == "nt" and not attributes.st_mode & stat.S_IWRITE:
        os.chmod(target, stat.S_IMODE(attributes.st_mode) | stat.S_IWRITE)
    target.unlink()


def _emergency_state(artifact):
    """错误后的新观察边界：retained必须仍是完整独立应急成果。"""
    try:
        observed = observe_file(artifact.path, include_digest=True)
        if observed.kind == "absent":
            return "removed"
        if (
            _matches_artifact(observed, artifact)
            and observed.digest == artifact.digest
        ):
            return "retained"
    except (OSError, ValueError):
        # 附加状态观察失败不替换主失败，也不认证完整性。
        return "unknown"
    return "unknown"


def _prepare_staged(
    expected, chunks, options, *, retained_content, replace_target_name=False,
):
    """所有成果共用独立暂存生成、核验、同步、关闭及所有权。

    bytes入口传入单块并保留已有bytes，不从暂存重取工作正文。
    普通流式成果不持有全文bytes；硬链接流式成果在关闭后的
    准备边界取得一次完整bytes，复用同一已生成独立应急。
    """
    mechanism = (
        "in_place"
        if not replace_target_name
        and expected.kind == "file" and expected.link_count > 1
        else "atomic_replace"
    )
    temporary = None
    owned_identity = None
    completed = False
    try:
        with NamedTemporaryFile(
            mode="w+b", dir=expected.path.parent,
            prefix=f".{expected.path.name}.",
            suffix=".emergency" if mechanism == "in_place" else ".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            created = os.fstat(stream.fileno())
            owned_identity = _object_identity(created)
            if (
                not stat.S_ISREG(created.st_mode)
                or type(created.st_dev) is not int
                or created.st_dev < 0
                or type(created.st_ino) is not int
                or created.st_ino <= 0
                or created.st_nlink != 1
            ):
                raise OSError("完整暂存必须是可靠身份的独立普通文件")

            generated_length = 0
            generated_digest = hashlib.sha256()
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise TypeError("流式成果每块必须是不可变bytes")
                _write_all(stream, chunk)
                generated_length += len(chunk)
                generated_digest.update(chunk)
            digest = generated_digest.hexdigest()

            stream.flush()
            actual_digest, length = _stream_digest(stream)
            finished = os.fstat(stream.fileno())
            if (
                actual_digest != digest
                or length != generated_length
                or finished.st_size != generated_length
                or _object_identity(finished) != owned_identity
                or finished.st_nlink != 1
                or not stat.S_ISREG(finished.st_mode)
            ):
                raise OSError("完整暂存内容、身份或长度核验失败")

            # 应急保存独立新字节，不提前改变工作目标属性。
            # 原对象权限和正式mtime由唯一写回算法处理。
            if mechanism == "atomic_replace":
                mode = options.file_mode
                if mode is None and expected.kind == "file":
                    mode = stat.S_IMODE(expected.attributes.st_mode)
                if mode is not None:
                    os.chmod(temporary, mode)
                    if stat.S_IMODE(temporary.lstat().st_mode) != mode:
                        raise OSError("完整暂存基础权限未准确设置")
                if options.mtime_ns is not None:
                    attributes = os.fstat(stream.fileno())
                    os.utime(
                        temporary,
                        ns=(
                            attributes.st_atime_ns
                            if options.atime_ns is None else options.atime_ns,
                            options.mtime_ns,
                        ),
                    )
                    if temporary.lstat().st_mtime_ns != options.mtime_ns:
                        raise OSError("完整暂存正式修改时间未准确设置")
            os.fsync(stream.fileno())

        artifact = _new_artifact(
            expected, options, retained_content, temporary, owned_identity,
            digest, mechanism, generated_length,
        )
        _verify_artifact(artifact, include_digest=False)
        if mechanism == "in_place" and retained_content is None:
            # 生成流已经关闭；此新边界取得唯一工作写回bytes，
            # 同时核验应急对象及内容，不再创建另一份应急。
            observed = observe_file(
                temporary, include_bytes=True, include_digest=True,
            )
            if (
                not _matches_artifact(observed, artifact)
                or observed.digest != digest
            ):
                raise ValueError("流式应急在完整字节获取边界发生变化")
            object.__setattr__(artifact, "content", observed.source_bytes)

        completed = True
        return artifact
    finally:
        if not completed and temporary is not None and owned_identity is not None:
            try:
                _remove_owned(temporary, owned_identity)
            except (OSError, ValueError) as cleanup_error:
                print(
                    f"[不完整成果清理失败] {temporary}：{cleanup_error}",
                    file=sys.stderr,
                )


def prepare_content_artifact(
    expected, content, *, options=ContentPublicationOptions(),
):
    """完整bytes准备；不创建工作父目录或改变工作目标。"""
    if not isinstance(content, bytes):
        raise TypeError("完整成果必须是不可变bytes")
    _validate_inputs(expected, options)
    _check_preparation_target(expected)
    return _prepare_staged(
        expected, (content,), options, retained_content=content,
    )


def prepare_stream_content_artifact(
    expected, chunks, *, options=ContentPublicationOptions(),
):
    """一次消费有限bytes块，完整关闭后交付正式成果。

    调用方负责正文构建、编码及換行政策，本能力不隐式转换。
    普通成果不永久持有全文内存；硬链接成果在准备完成时
    具有完整bytes及同一独立应急。正常返回不自动发布。
    迭代对象及其外部资源归调用方，本能力不取得其清理所有权。
    """
    _validate_inputs(expected, options)
    if isinstance(chunks, (str, bytes, bytearray, memoryview)):
        raise TypeError("流式成果必须是bytes块迭代对象，不是单份正文")
    iterator = iter(chunks)
    _check_preparation_target(expected)
    return _prepare_staged(
        expected, iterator, options, retained_content=None,
    )


def _recheck_copy_source(source):
    """新准备边界检查固定来源，不用后来属性替代原观察。"""
    current = recheck_file(source, check_mtime=True)
    if stat.S_IMODE(current.attributes.st_mode) != stat.S_IMODE(
        source.attributes.st_mode
    ):
        raise ValueError(f"固定复制来源基础权限变化：{source.path}")
    return current


def prepare_copy_content_artifact(
    source, expected, *, preserve_target_mode=True,
    options=ContentPublicationOptions(), replace_target_name=False,
):
    """固定来源完整bytes和适用基础属性形成成果，正常返回不发布。

    来源只读；内容直接消费观察已有bytes。
    普通复制保留已有目标权限；恢复可以显式使用来源权限。
    正式mtime允许由恢复依据覆盖，不追认材料后来时间。
    访问时间保留基础复制设置，但后续读取可以改变它；
    不将其作为内容版本或精确恢复要求。
    不复制ACL、所有者、扩展属性或硬链接拓扑。
    """
    if not isinstance(source, FileObservation):
        raise TypeError("来源复制必须消费固定FileObservation")
    if (
        source.kind != "file"
        or not isinstance(source.source_bytes, bytes)
        or source.attributes is None
        or type(source.mtime_ns) is not int
    ):
        raise ValueError("复制来源缺少完整原字节、属性或正式修改时间")
    _validate_inputs(
        source, ContentPublicationOptions(check_target_mtime=True),
    )
    if source.mtime_ns != source.attributes.st_mtime_ns:
        raise ValueError("复制来源正式修改时间不对应固定属性")
    if type(preserve_target_mode) is not bool:
        raise TypeError("保留目标基础权限开关必须是布尔值")
    if type(replace_target_name) is not bool:
        raise TypeError("替换指定目标名称开关必须是布尔值")
    if replace_target_name and preserve_target_mode:
        raise ValueError("名称复制必须使用固定来源基础权限")
    # 目录转文件只准备移除之后的独立文件成果。
    # 完整目录的移除资格及正常结果由所属组合核心负责。
    from core.file_observation import TreeObservation, recheck_tree
    replacement_tree = expected if isinstance(expected, TreeObservation) else None
    if replacement_tree is not None:
        if preserve_target_mode or replace_target_name:
            raise ValueError("目录转文件必须使用来源权限及内容恢复政策")
        from core.filesystem_changes import prepare_tree_removal
        prepare_tree_removal(replacement_tree)
        expected = FileObservation(
            replacement_tree.path,
            (*replacement_tree.chain[:-1], (replacement_tree.path, "absent")),
            "absent", None, None, None, None, None, None,
        )
    _validate_inputs(expected, options)
    if options.file_mode is not None or options.atime_ns is not None:
        raise ValueError("复制权限及访问时间必须由固定观察和复制政策决定")
    if (
        expected.kind == "file"
        and source.object_identity == expected.object_identity
    ):
        raise ValueError("复制来源和目标实际为同一对象")

    _recheck_copy_source(source)
    mode = stat.S_IMODE(
        expected.attributes.st_mode
        if preserve_target_mode and expected.kind == "file"
        else source.attributes.st_mode
    )
    copied_options = replace(
        options,
        file_mode=mode,
        atime_ns=source.attributes.st_atime_ns,
        mtime_ns=(
            source.mtime_ns if options.mtime_ns is None else options.mtime_ns
        ),
    )
    artifact = None
    completed = False
    try:
        if replacement_tree is not None:
            recheck_tree(replacement_tree)
            _, parents = checked_chain(
                expected.path.parent, include_observation=True,
            )
            if any(kind != "directory" for _, kind in parents):
                raise ValueError("目录转文件成果要求必要父目录已经存在")
            artifact = _prepare_staged(
                expected, (source.source_bytes,), copied_options,
                retained_content=source.source_bytes,
            )
        elif replace_target_name:
            # 名称覆盖只替换指定名称，不写入旧目标共享对象。
            # 与内容复制共用完整暂存、所有权及唯一发布算法。
            _check_preparation_target(expected)
            artifact = _prepare_staged(
                expected, (source.source_bytes,), copied_options,
                retained_content=source.source_bytes, replace_target_name=True,
            )
        else:
            artifact = prepare_content_artifact(
                expected, source.source_bytes, options=copied_options,
            )
        _recheck_copy_source(source)
        if replacement_tree is not None:
            recheck_tree(replacement_tree)
        completed = True
        return artifact
    finally:
        if not completed and artifact is not None:
            try:
                dispose_content_artifact(artifact)
            except (OSError, ValueError) as cleanup_error:
                # 清理没有完成不能覆盖来源复核的主失败；
                # 通用所有权能力不会删除外部替换对象。
                print(
                    f"[复制成果放弃清理失败] {artifact.path}：{cleanup_error}",
                    file=sys.stderr,
                )


def dispose_content_artifact(artifact):
    """明确放弃未执行成果，或清理失败后的普通暂存。

    disposing与发布attempted分开：取消清理失败可明确重试清理，
    已尝试硬链接发布的完整应急禁止通用清理。
    """
    if not isinstance(artifact, PreparedContentArtifact):
        raise TypeError("成果清理必须消费PreparedContentArtifact")
    if artifact._lifecycle in ("completed", "disposed"):
        raise ValueError("成果已经消费或清理，不能再次操作")
    if artifact.mechanism == "in_place" and artifact._lifecycle == "attempted":
        raise ValueError("发布失败后的完整应急必须保留，禁止通用清理")
    object.__setattr__(artifact, "_lifecycle", "disposing")
    _remove_owned(artifact.path, artifact.identity)
    object.__setattr__(artifact, "_lifecycle", "disposed")


def _hardlink_expectation(expected):
    digest = expected.digest
    if digest is None:
        digest = hashlib.sha256(expected.source_bytes).hexdigest()
    return HardlinkContentExpectation(
        expected.path,
        (expected.object_identity.device, expected.object_identity.inode),
        expected.link_count, digest,
    )


def _verify_published_atomic(artifact):
    actual = observe_file(
        artifact.expected.path, include_digest=True,
        include_mtime=artifact.options.mtime_ns is not None,
    )
    if (
        not _matches_artifact(actual, artifact)
        or actual.digest != artifact.digest
        or artifact.options.mtime_ns is not None
        and actual.mtime_ns != artifact.options.mtime_ns
    ):
        raise OSError("正式替换后成果身份、链接、内容或时间不符")
    mode = artifact.options.file_mode
    if mode is None and artifact.expected.kind == "file":
        mode = stat.S_IMODE(artifact.expected.attributes.st_mode)
    if mode is not None and stat.S_IMODE(actual.attributes.st_mode) != mode:
        raise OSError("正式替换后成果基础权限不符")


def publish_content(artifact):
    """一次消费成果；失败、清理或完成后均不能再次发布。

    明确I/O和状态失败携带结果；程序缺陷自然传播。
    无论异常类型如何，发布资格已消费，完整应急不由finally删除。
    """
    if not isinstance(artifact, PreparedContentArtifact):
        raise TypeError("正式发布必须消费PreparedContentArtifact")
    if artifact._lifecycle != "prepared":
        raise ValueError("成果已消费、失败或清理，必须建立新成果")
    object.__setattr__(artifact, "_lifecycle", "attempted")
    expected = artifact.expected
    options = artifact.options
    phase = "完整成果及固定前提复核"
    mutation_attempted = False
    replacement = "not_attempted"
    content_completed = False
    confirmation_completed = False
    emergency_path = artifact.path if artifact.mechanism == "in_place" else None
    try:
        _verify_artifact(artifact, include_digest=True)
        _recheck_target(artifact)
        if artifact.mechanism == "in_place":
            if not isinstance(artifact.content, bytes):
                raise RuntimeError("硬链接活动成果缺少已准备的完整bytes")
            phase = "固定原对象内容写回"
            _publish_prepared_hardlink(
                _hardlink_expectation(expected),
                artifact.content, artifact.path, artifact.identity,
                artifact.digest, mtime_ns=options.mtime_ns,
                file_mode=options.file_mode, atime_ns=options.atime_ns,
                before_mutation=lambda: _recheck_target(artifact),
                durable_publication=options.durable_publication,
            )
            content_completed = True
            confirmation_completed = True
        else:
            phase = "原子名称替换"
            mutation_attempted = True
            replacement = "unknown"
            os.replace(artifact.path, expected.path)
            replacement = "replaced"
            phase = "正式成果完整性核验"
            _verify_published_atomic(artifact)
            content_completed = True
            if options.durable_publication:
                phase = "正式成果目录持久化确认"
                sync_directory(expected.path.parent)
            confirmation_completed = True
    except HardlinkContentError as error:
        result = ContentPublicationResult(
            expected.path, "in_place", error.phase,
            error.mutation_attempted, "not_attempted",
            error.content_completed, error.confirmation_completed,
            artifact.path, _emergency_state(artifact), error,
        )
        raise ContentPublicationError(result) from error
    except (OSError, ValueError) as error:
        result = ContentPublicationResult(
            expected.path, artifact.mechanism, phase,
            mutation_attempted, replacement,
            content_completed, confirmation_completed,
            emergency_path,
            _emergency_state(artifact) if emergency_path is not None
            else "not_required",
            error,
        )
        raise ContentPublicationError(result) from error

    object.__setattr__(artifact, "_lifecycle", "completed")
    return ContentPublicationResult(
        expected.path, artifact.mechanism, "completed",
        True, replacement, content_completed, confirmation_completed,
        emergency_path, "removed" if emergency_path is not None
        else "not_required",
    )
