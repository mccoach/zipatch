# -*- coding: utf-8 -*-

"""任务正常成果到固定观察的纯投影，不读取磁盘或取得新授权。"""

from dataclasses import replace

from core.file_observation import FileObservation, TreeObservation
from core.recovery_content import content_integrity


def observed_files(observed):
    if isinstance(observed, TreeObservation):
        return observed.files
    if isinstance(observed, FileObservation) and observed.kind == "file":
        return (("", observed),)
    return ()


def shared_file_states(finished):
    """按正常成果登记顺序保留同一对象的最近内容和计数基线。"""
    states = {}
    for actual, baseline in finished.values():
        for _, file in observed_files(actual):
            states[file.object_identity] = (file, baseline)
    return states


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