# -*- coding: utf-8 -*-

"""恢复详情的固定索引、按需字节核验及查看差异；不决定或执行恢复。"""


import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from core.text_io import decode_text_bytes
from services.patch_models import BackupRestorePreviewResult


@dataclass(frozen=True)
class RestoreDetailEntry:
    relative: str
    target: Path
    original_kind: str
    original_digest: str
    original_mtime_ns: int | None
    current_kind: str
    current_digest: str
    current_mtime_ns: int | None
    expected_kind: str
    expected_digest: str
    material: Path | None

    @property
    def has_file_content(self):
        return self.original_kind == "file" or self.current_kind == "file"


@dataclass(frozen=True)
class RestoreDetailScope:
    object_id: str
    target: Path
    source: Path
    classification: str
    entries: tuple
    execution_facts: tuple


@dataclass(frozen=True)
class RestoreFileDetail:
    entry: RestoreDetailEntry
    original_bytes: bytes | None
    current_bytes: bytes | None


def check_detail_task(preview):
    """查看依附本次活动任务，不使作废任务借缓存继续提供旧结论。"""
    if not isinstance(preview, BackupRestorePreviewResult):
        raise TypeError("详情必须消费本次正式恢复预演")
    if preview.execution.consumed:
        raise ValueError("恢复任务已结束或作废，不能继续查看旧任务详情")
    return preview.execution


def build_restore_detail_scope(preview, identifier):
    """只消费正式原状态、固定当前观察和比较，不访问文件系统。"""
    executor = check_detail_task(preview)
    if type(identifier) is not str:
        raise TypeError("详情对象编号必须是字符串")
    action = next((
        action for action in executor.selection_plan.actions
        if action.payload["id"] == identifier
    ), None)
    if action is None:
        raise ValueError("详情对象不属于本次正式恢复任务")

    original = action.payload["original"]
    current = action.current
    comparison = action.comparison
    original_records = {record[0]: record for record in original["records"]}
    current_records = {record[0]: record for record in current.content_records}
    expected_records = {record[0]: record for record in comparison.expected_records}
    original_times = dict(action.payload["file_times"])
    current_times = dict(current.file_times)
    material_id = action.payload["material"]
    material_root = (
        executor.source / "files" / material_id
        if material_id is not None else None
    )
    positions = set(original_records) | set(current_records) | set(expected_records)
    positions.add("")
    entries = []
    for relative in sorted(positions, key=lambda value: value.encode("utf-8")):
        before = original_records.get(relative)
        actual = current_records.get(relative)
        expected = expected_records.get(relative)
        original_kind = before[1] if before is not None else "absent"
        current_kind = actual[1] if actual is not None else "absent"
        entries.append(RestoreDetailEntry(
            relative,
            current.path / relative if relative else current.path,
            original_kind,
            before[2] if before is not None else "",
            original_times[relative] if original_kind == "file" else None,
            current_kind,
            actual[2] if actual is not None else "",
            current_times[relative] if current_kind == "file" else None,
            expected[1] if expected is not None else "absent",
            expected[2] if expected is not None else "",
            (
                material_root / relative if relative else material_root
            ) if original_kind == "file" else None,
        ))
    facts = tuple(
        (
            unit["id"], unit["members"],
            unit["fact"]["state"], unit["fact"]["changes"],
        )
        for unit in executor.source_document["units"]
        if identifier in unit["objects"]
    )
    return RestoreDetailScope(
        identifier, current.path, executor.source,
        comparison.status, tuple(entries), facts,
    )


def _fixed_member(observed, relative):
    """位置来自固定详情范围，不从后来对象建立新的查看前提。"""
    from core.file_observation import FileObservation, TreeObservation
    if isinstance(observed, TreeObservation):
        member = dict(observed.files).get(relative)
        if member is None:
            raise ValueError("详情文件不属于首次完整树观察")
        return member
    if (
        isinstance(observed, FileObservation)
        and not relative and observed.kind == "file"
    ):
        return observed
    raise ValueError("详情位置没有固定普通文件观察")


def _read_fixed_file(expected, *, check_mtime):
    """新查看边界一次共享获取，核对身份、链接、内容及适用时间。"""
    from core.file_observation import observe_file
    observed = observe_file(
        expected.path, include_bytes=True, include_digest=True,
        include_mtime=check_mtime,
    )
    if (
        observed.kind != "file"
        or observed.object_identity != expected.object_identity
        or observed.link_count != expected.link_count
        or observed.digest != expected.digest
        or expected.source_bytes is not None
        and observed.source_bytes != expected.source_bytes
        or check_mtime and observed.mtime_ns != expected.mtime_ns
    ):
        raise ValueError(
            f"详情来源身份、链接、内容或适用文件时间"
            f"已偏离固定依据：{expected.path}\n"
            "未生成差异结论，请重新预演。"
        )
    return observed.source_bytes


def read_restore_file_detail(preview, scope, relative):
    """只读查看使用材料和当前对象各自首次固定观察。"""
    from core.file_observation import recheck_file
    executor = check_detail_task(preview)
    if not isinstance(scope, RestoreDetailScope):
        raise TypeError("详情读取必须消费固定详情范围")
    if build_restore_detail_scope(preview, scope.object_id) != scope:
        raise ValueError("详情范围不对应本次正式恢复任务")
    if type(relative) is not str:
        raise TypeError("详情相对位置必须是字符串")
    entry = next((
        entry for entry in scope.entries if entry.relative == relative
    ), None)
    if entry is None:
        raise ValueError("详情位置不属于本次固定范围")
    if not entry.has_file_content:
        raise ValueError(
            "本位置没有原文件或当前文件正文，请查看结构详情"
        )
    action = next(
        action for action in executor.selection_plan.actions
        if action.payload["id"] == scope.object_id
    )
    recheck_file(executor.evidence.manifest)
    original_bytes = current_bytes = None
    if entry.original_kind == "file":
        material = executor.evidence.materials[scope.object_id]
        original_bytes = _read_fixed_file(
            _fixed_member(material, relative), check_mtime=False,
        )
    if entry.current_kind == "file":
        current_bytes = _read_fixed_file(
            _fixed_member(action.current.observed, relative),
            check_mtime=True,
        )
    recheck_file(executor.evidence.manifest)
    return RestoreFileDetail(entry, original_bytes, current_bytes)


def format_file_detail(detail):
    """大小从本次已核验字节派生，不持久化第二份版本事实。"""
    if not isinstance(detail, RestoreFileDetail):
        raise TypeError("文件详情必须消费已核验读取结果")
    entry = detail.entry
    lines = [
        f"实际位置：{entry.target}",
        "比较方向：操作前原状态 → 本次预演固定的当前状态。",
        "操作后预期没有正文材料，只展示类型及指纹，不伪造预期正文或大小。",
        "以下是本窗口已核验的查看快照，不是实时监控或执行资格。",
        "",
    ]
    for title, kind, digest, modified, content in (
        (
            "操作前原状态", entry.original_kind, entry.original_digest,
            entry.original_mtime_ns, detail.original_bytes,
        ),
        (
            "固定当前状态", entry.current_kind, entry.current_digest,
            entry.current_mtime_ns, detail.current_bytes,
        ),
    ):
        lines.append(f"{title}：{kind}")
        if content is not None:
            lines.extend((
                f"大小：{len(content)} bytes",
                f"SHA-256：{digest}",
                f"正式／固定mtime_ns：{modified}",
            ))
        else:
            lines.append("本侧不是文件，不将其冒充零字节文件。")
        lines.append("")
    lines.extend((
        f"操作后预期类型：{entry.expected_kind}",
        f"操作后预期SHA-256：{entry.expected_digest or '不适用'}",
    ))
    if entry.original_kind != entry.current_kind:
        lines.append(
            "原状态与固定当前类型或存在性不同；不存在、目录和空文件不能互相替代。"
        )
    elif detail.original_bytes is not None and detail.current_bytes is not None:
        lines.append(
            "原字节完全一致；文件时间要求独立判断。"
            if detail.original_bytes == detail.current_bytes else
            "原字节不同；这不认证文件的文本或二进制用途。"
        )
    return "\n".join(lines)


def _text_lines(text):
    """查看分行保留实际CRLF、LF、单独CR及无尾换行末行。"""
    return tuple(
        match.group()
        for match in re.finditer(r".*?(?:\r\n|\r|\n|$)", text, re.DOTALL)
        if match.group() != ""
    )


def format_text_difference(detail):
    """严格解码仅服务明确请求的查看，不参与恢复资格或执行。"""
    if not isinstance(detail, RestoreFileDetail):
        raise TypeError("文本差异必须消费已核验字节")
    entry = detail.entry
    texts = []
    encodings = []
    for title, kind, content in (
        ("操作前", entry.original_kind, detail.original_bytes),
        ("固定当前", entry.current_kind, detail.current_bytes),
    ):
        if kind == "directory":
            raise ValueError(
                "本位置包含目录与文件的类型替换，不能把目录解释为空文本；"
                "请查看结构及文件字节详情。"
            )
        if content is None:
            texts.append("")
            encodings.append("不存在，无正文")
        else:
            text, encoding = decode_text_bytes(
                content, source_description=f"{title}查看文件", path=entry.target,
            )
            texts.append(text)
            encodings.append(encoding)

    lines = [
        format_file_detail(detail),
        "",
        "【文本查看差异：操作前 → 固定当前】",
        f"操作前解码：{encodings[0]}",
        f"固定当前解码：{encodings[1]}",
        "严格解码不认证文件用途；正文只用于查看，不参与恢复资格或执行。",
        "每行使用转义展示：空格、Tab、CRLF、LF、单独CR和尾换行可辨认。",
        "“- 原”来自操作前，“+ 当前”来自固定当前；各自行号分别计数。",
    ]
    if entry.original_kind != entry.current_kind:
        lines.append(
            "存在性不同：一侧不存在，另一侧是文件；没有正文不等于存在空文件。"
        )
        if texts[0] == texts[1]:
            lines.append(
                "文件侧正文为空，但存在性差异仍然成立；不能认定原状态相同。"
            )
            return "\n".join(lines)
    elif detail.original_bytes == detail.current_bytes:
        lines.append("原字节完全一致，无字节差异。")
        return "\n".join(lines)
    elif texts[0] == texts[1]:
        lines.append(
            "解码后字符一致，但原字节不同；可能涉及编码或BOM。"
            "不能据此声称文件无差异。"
        )
        return "\n".join(lines)

    before, current = map(_text_lines, texts)
    matcher = SequenceMatcher(None, before, current, autojunk=False)
    for operation, start, end, new_start, new_end in matcher.get_opcodes():
        if operation == "equal":
            lines.append(
                f"相同行：原 {start + 1}—{end}；当前 {new_start + 1}—{new_end}"
            )
            continue
        lines.append(
            f"差异区间：原偏移 [{start}, {end})；"
            f"当前偏移 [{new_start}, {new_end})（按行，零起点）"
        )
        lines.extend(
            f"- 原 {number + 1}：{before[number]!r}"
            for number in range(start, end)
        )
        lines.extend(
            f"+ 当前 {number + 1}：{current[number]!r}"
            for number in range(new_start, new_end)
        )
    return "\n".join(lines)
