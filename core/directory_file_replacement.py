# -*- coding: utf-8 -*-

"""固定目录转文件：完整成果先准备，正常目录移除后一次发布。

业务解释、确认、保护、started及整任务协调归调用方。
本组合不重新授权失败现场，不重写移除或发布算法。
"""

import sys
from dataclasses import dataclass
from pathlib import Path

from core.content_publication import (
    ContentPublicationError, PreparedContentArtifact,
    ContentPublicationOptions, ContentPublicationResult,
    _recheck_copy_source, _verify_artifact, _verify_published_atomic,
    dispose_content_artifact, prepare_copy_content_artifact, publish_content,
)
from core.file_observation import FileObservation, TreeObservation
from core.filesystem_changes import (
    PreparedTreeRemoval, cancel_tree_removal,
    execute_tree_removal, prepare_tree_removal,
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
    _results: tuple
    _current: Path | None
    _phase: str
    _active: object
    _removal_completed: bool
    _publication: ContentPublicationResult | None

    def __init__(self, *arguments, **keywords):
        raise TypeError("目录转文件必须通过正式准备过程创建")

    @property
    def results(self):
        return self._results

    @property
    def current(self):
        return self._current

    @property
    def phase(self):
        return self._phase

    @property
    def active_change(self):
        return self._active


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
    artifact = None
    completed = False
    try:
        artifact = prepare_copy_content_artifact(
            source, effect.before, preserve_target_mode=False,
            options=ContentPublicationOptions(
                durable_publication=durable_publication,
                mtime_ns=mtime_ns,
            ),
        )
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
            ("_lifecycle", "prepared"), ("_results", ()),
            ("_current", None), ("_phase", "prepared"), ("_active", None),
            ("_removal_completed", False), ("_publication", None),
        ):
            object.__setattr__(replacement, name, value)
        completed = True
        return replacement
    finally:
        if not completed and artifact is not None:
            try:
                dispose_content_artifact(artifact)
            except (OSError, ValueError) as cleanup_error:
                print(
                    f"[目录转文件准备放弃清理失败] {artifact.path}：{cleanup_error}",
                    file=sys.stderr,
                )


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


def _dispose_failed_artifact(replacement):
    artifact = replacement.artifact
    if artifact._lifecycle in ("completed", "disposed"):
        return
    try:
        dispose_content_artifact(artifact)
    except (OSError, ValueError) as cleanup_error:
        print(
            f"[目录转文件暂存清理失败] {artifact.path}：{cleanup_error}",
            file=sys.stderr,
        )


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
        object.__setattr__(replacement, "_active", replacement.removal)
        removed = False
        try:
            execute_tree_removal(
                replacement.removal, before_step=check_sources,
            )
            removed = True
        finally:
            object.__setattr__(
                replacement, "_results", replacement.removal.results,
            )
            if removed:
                object.__setattr__(replacement, "_active", None)
            else:
                _stage(
                    replacement, replacement.removal.current,
                    replacement.removal.phase,
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
            object.__setattr__(
                replacement, "_results", (*replacement.results, error.result),
            )
            _stage(replacement, error.result.target, error.result.phase)
            raise
        object.__setattr__(replacement, "_publication", publication)
        object.__setattr__(
            replacement, "_results", (*replacement.results, publication),
        )

        _stage(replacement, replacement.source.path, "发布后固定来源及所属范围复核")
        check_sources()
        _stage(replacement, target, "组合最终文件内容身份及正式时间核验")
        _verify_published_atomic(replacement.artifact)
    except (OSError, ValueError) as error:
        raise DirectoryFileReplacementError(
            _result(replacement, error=error), replacement,
        ) from error
    finally:
        # 只放弃本次自有独立暂存，不删除目录残余或已发布工作文件。
        _dispose_failed_artifact(replacement)

    object.__setattr__(replacement, "_lifecycle", "completed")
    _stage(replacement, None, "completed")
    return _result(replacement, completed=True)
