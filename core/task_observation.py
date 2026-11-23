# -*- coding: utf-8 -*-

"""任务正常成果到固定观察的纯投影，不读取磁盘或取得新授权。"""

from dataclasses import replace
from types import MappingProxyType

from core.content_publication import ContentPublicationResult
from core.file_observation import FileObservation, TreeObservation
from core.filesystem_changes import FilesystemChangeResult
from core.name_moves import NativeNameMoveResult
from core.recovery_content import content_integrity
from core.recovery_paths import identity


def observed_files(observed):
    if isinstance(observed, TreeObservation):
        return observed.files
    if isinstance(observed, FileObservation) and observed.kind == "file":
        return (("", observed),)
    return ()


class TaskObservationState:
    """任务正常变化的唯一拥有者；实际步骤仍由正式步骤账本提供。

    名称计数只增量消费新步骤，不反复扫描历史。
    正常成果接受时一次登记其共享对象和计数基线；
    复核只消费派生状态，不从现场取得新的成功事实或授权。
    """

    def __init__(self, observations):
        self._original_files = {}
        for observed in observations:
            for _, member in observed_files(observed):
                key = identity(member.path)
                previous = self._original_files.setdefault(key, member)
                if previous != member:
                    raise ValueError("任务同一文件具有矛盾的最初观察")
        self._consumed_results = ()
        self._removed_names = set()
        self._removed_counts = {}
        self._finished = {}
        self._shared = {}

    @property
    def finished(self):
        return MappingProxyType(self._finished)

    def consume_results(self, results):
        """只接受同一本任务账本的追加前缀，不能重排或替换已消费事实。"""
        if type(results) is not tuple:
            raise TypeError("任务步骤必须是正式结果元组")
        previous = self._consumed_results
        if len(results) < len(previous) or any(
            current is not accepted
            for current, accepted in zip(results, previous)
        ):
            raise ValueError("任务实际步骤不是原账本的追加前缀")
        for receipt in results[len(previous):]:
            if not isinstance(receipt, (
                FilesystemChangeResult, ContentPublicationResult,
                NativeNameMoveResult,
            )):
                raise TypeError("任务正常变化只消费正式工作步骤")
            removes_name = receipt.succeeded and (
                isinstance(receipt, FilesystemChangeResult)
                and receipt.kind == "remove_file"
                or isinstance(receipt, ContentPublicationResult)
                and receipt.mechanism == "atomic_replace"
                or isinstance(receipt, NativeNameMoveResult)
            )
            if not removes_name:
                continue
            # 原生移动的来源名称没有减少原对象链接数量；
            # 被覆盖的原目标名称才需要扣减。
            key = identity(receipt.target)
            original = self._original_files.get(key)
            if original is None or key in self._removed_names:
                continue
            self._removed_names.add(key)
            identifier = original.object_identity
            self._removed_counts[identifier] = (
                self._removed_counts.get(identifier, 0) + 1
            )
        self._consumed_results = results

    def removed_counts(self, results):
        self.consume_results(results)
        return MappingProxyType(self._removed_counts)

    def project(self, observed, results, *, baseline=None):
        self.consume_results(results)
        return project_task_observation(
            observed, self._removed_counts, self._shared, baseline=baseline,
        )

    def expected(self, observed, results):
        """正式位置已有正常成果时消费它，否则消费原固定观察。"""
        finished = self._finished.get(identity(observed.path))
        if finished is None:
            return self.project(observed, results)
        actual, baseline = finished
        return self.project(actual, results, baseline=baseline)

    def accept(self, actuals, results):
        """调用方已完成具体成果核验；一次登记全部位置和共享对象。"""
        self.consume_results(results)
        from core.link_paths import LinkPathObservation

        if type(actuals) is not tuple or any(
            not isinstance(actual, (
                FileObservation, TreeObservation, LinkPathObservation,
            ))
            for actual in actuals
        ):
            raise TypeError("正常成果必须是正式观察元组")
        baseline = dict(self._removed_counts)
        for actual in actuals:
            self._finished[identity(actual.path)] = (actual, baseline)
            for _, member in observed_files(actual):
                self._shared[member.object_identity] = (member, baseline)

    def release_observations(self):
        """任务结束后释放输入和正常成果引用，不影响外部正式步骤结果。"""
        self._original_files.clear()
        self._finished.clear()
        self._shared.clear()
        self._consumed_results = ()
        self._removed_names.clear()
        self._removed_counts.clear()


def project_task_observation(
    observed, removed_counts, shared_states, *, baseline=None,
):
    """接受正常名称移除和正常共享写回，不改变初始业务基线。

    baseline只用于已经正常完成对象的链接计数；
    所有内容变化必须来自正式完成成果，不能来自失败现场。
    """
    baseline = {} if baseline is None else baseline

    def project_file(file):
        if file.kind != "file":
            return file
        identifier = file.object_identity
        links = file.link_count - (
            removed_counts.get(identifier, 0) - baseline.get(identifier, 0)
        )
        if links < 1:
            raise ValueError("任务正常步骤形成非法剩余链接数量")
        shared = shared_states.get(identifier)
        if shared is None:
            return replace(file, link_count=links)
        actual, shared_baseline = shared
        actual_links = actual.link_count - (
            removed_counts.get(identifier, 0)
            - shared_baseline.get(identifier, 0)
        )
        if actual_links != links:
            raise ValueError("任务共享对象的链接计数前提矛盾")
        return replace(
            file, link_count=links,
            source_bytes=(
                file.source_bytes if file.digest == actual.digest else None
            ),
            digest=actual.digest, mtime_ns=actual.mtime_ns,
            attributes=actual.attributes,
        )

    from core.link_paths import LinkPathObservation

    if isinstance(observed, LinkPathObservation):
        # 普通硬链接正文和名称计数不改变链接入口原定义。
        return observed
    if isinstance(observed, FileObservation):
        return project_file(observed)
    if not isinstance(observed, TreeObservation):
        raise TypeError("任务投影必须消费正式文件或完整树观察")
    files = tuple(
        (relative, project_file(file)) for relative, file in observed.files
    )
    records = tuple(sorted(
        [
            *((relative, "directory", "")
              for relative, _ in observed.directories),
            *((relative, "file", file.digest) for relative, file in files),
            # 正常共享文件内容变化不改变链接定义。
            # 链接记录直接消费原结构观察，不从现场补造。
            *(record for record in observed.content_records
              if record[1] in (
                  "file_symlink", "directory_symlink", "junction",
              )),
        ],
        key=lambda record: record[0].encode("utf-8"),
    ))
    return replace(
        observed, files=files, content_records=records,
        integrity=content_integrity("directory", records),
        file_times=(
            tuple((relative, file.mtime_ns) for relative, file in files)
            if observed.file_times else ()
        ),
    )


def validate_shared_file_contents(units):
    """固定effects对同一现存文件对象必须承诺一致的最终内容。"""
    desired = {}
    for unit in units:
        for effect in unit.effects:
            before = effect.before
            if (
                not isinstance(before, FileObservation)
                or before.kind != "file" or effect.after.kind != "file"
            ):
                continue
            digest = effect.after.records[0][2]
            previous = desired.setdefault(before.object_identity, digest)
            if previous != digest:
                raise ValueError(
                    "关联硬链接名称要求不同最终内容，无法同时满足："
                    f"{before.path}；请合并为一致成果或分开任务。"
                )