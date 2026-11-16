# -*- coding: utf-8 -*-

"""固定目录转文件：完整成果先准备，正常目录移除后一次发布。

业务解释、确认、保护、started及整任务协调归调用方。
本组合不重新授权失败现场，不重写移除或发布算法。
"""

from dataclasses import dataclass
from pathlib import Path

from core.content_publication import (
    ContentPublicationError, PreparedContentArtifact,
    ContentPublicationOptions, ContentPublicationResult,
    _prepare_staged, _recheck_copy_source, _validate_inputs,
    _verify_artifact, _verify_published_atomic,
    cleanup_content_artifact, dispose_content_artifact, publish_content,
)
from core.file_observation import FileObservation, TreeObservation, recheck_tree
from core.execution_ledger import ExecutionLedger
from core.filesystem_changes import (
    PreparedTreeRemoval, cancel_tree_removal,
    execute_tree_removal, prepare_tree_removal, tree_execution_results,
)
from core.protection_projection import FixedEffect, validate_content_state
from core.recovery_paths import overlaps


@dataclass(frozen=True, init=False)
class PreparedDirectoryFileReplacement:
    effect: FixedEffect
    source: FileObservation
    artifact: PreparedContentArtifact
    removal: PreparedTreeRemoval
    _lifecycle: str
    _execution: ExecutionLedger
    _current: Path | None
    _phase: str
    _removal_completed: bool
    _publication: ContentPublicationResult | None
    _final_observation: FileObservation | None

    @property
    def final_observation(self):
        """组合最终边界已核验的观察；失败不交付完成资格。"""
        return self._final_observation

    def __init__(self, *arguments, **keywords):
        raise TypeError("目录转文件必须通过正式准备过程创建")

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
        return self._execution.active_change


@dataclass(frozen=True)
class DirectoryFileReplacementResult:
    target: Path
    results: tuple
    removal_completed: bool
    publication: ContentPublicationResult | None
    completed: bool
    current: Path | None
    phase: str
    error: OSError | ValueError | None = None

    @property
    def succeeded(self):
        return (
            self.error is None and self.completed
            and self.removal_completed
            and self.publication is not None
            and self.publication.succeeded
            and self.current is None
        )


class DirectoryFileReplacementError(OSError):
    def __init__(self, result, replacement):
        self.result = result
        self.replacement = replacement
        super().__init__(
            f"固定目录转文件未完整结束：{result.target}\n"
            f"位置：{result.current}；阶段：{result.phase}\n"
            f"目录移除已正常完成：{'是' if result.removal_completed else '否'}\n"
            "真实步骤和准确子核心现场保留；"
            "移除或发布报错不能证明目标没有改变。\n"
            "资格已消费，停止后续，不自动重试或回滚。\n"
            f"{result.error}"
        )


def _stage(replacement, current, phase):
    object.__setattr__(replacement, "_current", current)
    object.__setattr__(replacement, "_phase", phase)


def _result(replacement, *, completed=False, error=None):
    return DirectoryFileReplacementResult(
        replacement.effect.before.path, replacement.results,
        replacement._removal_completed, replacement._publication,
        completed, replacement.current, replacement.phase, error,
    )


def prepare_directory_file_replacement(
    effect, source, *, mtime_ns, durable_publication=True,
):
    """消费具体effect、首次来源bytes及清单权威mtime。

    正常返回时文件成果已经完整核验、同步并关闭，
    当前目录未移除，成果仍可明确取消。
    """
    if not isinstance(effect, FixedEffect):
        raise TypeError("目录转文件必须消费FixedEffect")
    if not isinstance(effect.before, TreeObservation):
        raise TypeError("目录转文件必须消费固定完整目录观察")
    validate_content_state(effect.after)
    if effect.after.kind != "file":
        raise ValueError("目录转文件具体预期必须是文件")
    if not isinstance(source, FileObservation):
        raise TypeError("目录转文件来源必须是FileObservation")
    if (
        source.kind != "file"
        or not isinstance(source.source_bytes, bytes)
        or source.digest is None
        or effect.after.records != (("", "file", source.digest),)
    ):
        raise ValueError("目录转文件来源必须对应完整固定文件预期")
    if type(mtime_ns) is not int:
        raise TypeError("正式文件修改时间必须是mtime_ns整数")
    if type(durable_publication) is not bool:
        raise TypeError("目录确认开关必须是布尔值")
    if overlaps(source.path, effect.before.path):
        raise ValueError("目录转文件来源不能位于待移除范围")

    removal = prepare_tree_removal(effect.before)
    expected = FileObservation(
        effect.before.path,
        (*effect.before.chain[:-1], (effect.before.path, "absent")),
        "absent", None, None, None, None, None, None,
    )
    # 目标目录仍存在，不能把预测absent拿去进行普通文件准备入场检查。
    # 组合拥有目录前提和移除资格；暂存生成仍使用唯一内容算法。
    import stat
    from core.recovery_paths import checked_chain

    _validate_inputs(
        source, ContentPublicationOptions(check_target_mtime=True),
    )
    if (
        source.attributes is None
        or type(source.mtime_ns) is not int
        or source.mtime_ns != source.attributes.st_mtime_ns
    ):
        raise ValueError("目录转文件来源缺少一致的固定属性及正式时间")
    options = ContentPublicationOptions(
        durable_publication=durable_publication,
        mtime_ns=mtime_ns,
        file_mode=stat.S_IMODE(source.attributes.st_mode),
        atime_ns=source.attributes.st_atime_ns,
    )
    _validate_inputs(expected, options)
    artifact = None
    completed = False
    try:
        _recheck_copy_source(source)
        recheck_tree(effect.before)
        _, parents = checked_chain(
            expected.path.parent, include_observation=True,
        )
        if any(kind != "directory" for _, kind in parents):
            raise ValueError("目录转文件成果要求必要父目录已经存在")
        artifact = _prepare_staged(
            expected, (source.source_bytes,), options,
            retained_content=source.source_bytes,
        )
        _recheck_copy_source(source)
        recheck_tree(effect.before)
        if (
            artifact.mechanism != "atomic_replace"
            or artifact.expected.kind != "absent"
            or artifact.expected.path != effect.before.path
            or artifact.content is not source.source_bytes
            or artifact.digest != source.digest
            or artifact.length != len(source.source_bytes)
            or artifact.options.mtime_ns != mtime_ns
        ):
            raise ValueError("准备成果不对应固定目录移除后的文件预期")
        replacement = object.__new__(PreparedDirectoryFileReplacement)
        for name, value in (
            ("effect", effect), ("source", source),
            ("artifact", artifact), ("removal", removal),
            ("_lifecycle", "prepared"),
            ("_execution", ExecutionLedger(tree_execution_results)),
            ("_current", None), ("_phase", "prepared"),
            ("_removal_completed", False), ("_publication", None),
            ("_final_observation", None),
        ):
            object.__setattr__(replacement, name, value)
        completed = True
        return replacement
    finally:
        if not completed:
            cleanup_content_artifact(artifact)


def cancel_directory_file_replacement(replacement):
    """明确取消未执行组合；清理失败只允许再明确清理，不允许执行。"""
    if not isinstance(replacement, PreparedDirectoryFileReplacement):
        raise TypeError("取消必须消费PreparedDirectoryFileReplacement")
    if replacement._lifecycle not in ("prepared", "cancelling"):
        raise ValueError("目录转文件已消费或取消")
    object.__setattr__(replacement, "_lifecycle", "cancelling")
    cancel = replacement.removal
    if cancel._lifecycle == "prepared":
        cancel_tree_removal(cancel)
    dispose_content_artifact(replacement.artifact)
    object.__setattr__(replacement, "_lifecycle", "cancelled")
    _stage(replacement, None, "cancelled")


def execute_directory_file_replacement(replacement, *, recheck_context=None):
    """一次组合固定移除和唯一发布；调用方已完成适用工作授权。

    recheck_context仅复核源清单、其他材料及其他固定范围。
    本次来源文件由本核心复核，不让回调重新获取工作正文。
    """
    if not isinstance(replacement, PreparedDirectoryFileReplacement):
        raise TypeError("执行必须消费PreparedDirectoryFileReplacement")
    if recheck_context is not None and not callable(recheck_context):
        raise TypeError("所属范围复核必须是可调用对象")
    if replacement._lifecycle != "prepared":
        raise ValueError("目录转文件已消费、失败或取消")
    object.__setattr__(replacement, "_lifecycle", "attempted")
    target = replacement.effect.before.path

    def check_sources():
        if recheck_context is not None:
            recheck_context()
        _recheck_copy_source(replacement.source)

    try:
        _stage(replacement, replacement.artifact.path, "破坏性移除前完整文件成果复核")
        _verify_artifact(replacement.artifact, include_digest=True)
        _stage(replacement, replacement.source.path, "破坏性移除前固定来源复核")
        check_sources()

        _stage(replacement, target, "固定完整目录移除")
        replacement._execution.execute_active(
            replacement.removal, execute_tree_removal,
            stage=lambda current, phase: _stage(replacement, current, phase),
            before_step=check_sources,
        )
        object.__setattr__(replacement, "_removal_completed", True)

        # 不重新观察并授权目标：成果始终消费准备时固定的absent投影；
        # publish_content负责验证该前提确实成立。
        _stage(replacement, replacement.source.path, "目录移除后发布前固定来源复核")
        check_sources()
        _stage(replacement, target, "固定完整文件成果发布")
        try:
            publication = publish_content(replacement.artifact)
        except ContentPublicationError as error:
            object.__setattr__(replacement, "_publication", error.result)
            replacement._execution.append_results((error.result,))
            _stage(replacement, error.result.target, error.result.phase)
            raise
        object.__setattr__(replacement, "_publication", publication)
        replacement._execution.append_results((publication,))

        _stage(replacement, replacement.source.path, "发布后固定来源及所属范围复核")
        check_sources()
        _stage(replacement, target, "组合最终文件内容身份及正式时间核验")
        actual = _verify_published_atomic(replacement.artifact)
    except (OSError, ValueError) as error:
        raise DirectoryFileReplacementError(
            _result(replacement, error=error), replacement,
        ) from error
    finally:
        # 只放弃本次自有独立暂存，不删除目录残余或已发布工作文件。
        cleanup_content_artifact(replacement.artifact)

    object.__setattr__(replacement, "_final_observation", actual)
    object.__setattr__(replacement, "_lifecycle", "completed")
    _stage(replacement, None, "completed")
    return _result(replacement, completed=True)
