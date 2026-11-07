# -*- coding: utf-8 -*-

"""协议更新的固定来源、完整模块成果和一次正式发布。

普通Markdown解码政策保持；候选语法检查不执行模块。
确认、配置及正式模块加载和显示归界面。
"""

import ast
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from core.content_publication import (
    dispose_content_artifact, prepare_content_artifact, publish_content,
    ContentPublicationOptions,
)
from core.file_observation import FileObservation, observe_file, recheck_file
from core.text_io import decode_import_text_bytes
from core.time_utils import current_timestamp_text
from services.patch_protocol_doc_service import get_patch_protocol_doc


# 不先resolve消除所管理模块的原路径表达；正式观察检查原链。
TARGET_MODULE_PATH = Path(__file__).parent / "patch_protocol_doc.py"


@dataclass(frozen=True, init=False)
class PatchProtocolDocUpdatePreview:
    source_path: str
    source_encoding: str
    source_sha256: str
    source_length: int
    embedded_sha256: str
    embedded_length: int
    has_difference: bool
    source_content: str
    source_observation: FileObservation
    target_observation: FileObservation
    module_bytes: bytes | None
    _lifecycle: str

    def __init__(self, *arguments, **keywords):
        raise TypeError("协议更新必须通过正式预览过程准备")

    @property
    def consumed(self):
        return self._lifecycle != "prepared"


def is_protocol_doc_update_available():
    """只允许源码开发环境更新正式模块。"""
    return not bool(getattr(sys, "frozen", False))


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _observe_markdown_source(md_path):
    observed = observe_file(Path(md_path).expanduser(), include_bytes=True)
    if observed.kind != "file":
        raise ValueError(
            f"Markdown 源文档不存在或不是文件：{observed.path}"
        )
    content, encoding = decode_import_text_bytes(
        observed.source_bytes, path=observed.path,
    )
    return observed, content, encoding


def read_markdown_source(md_path):
    """只读来源入口；获取和纯用途解码分责，保持既有返回契约。"""
    observed, content, encoding = _observe_markdown_source(md_path)
    return str(observed.path), content, encoding


def build_protocol_doc_module_content(source_path, source_sha256, source_content):
    updated_at = current_timestamp_text()
    return (
        "# -*- coding: utf-8 -*-\n\n"
        "\"\"\"\n"
        "内置修改包协议规范文档。\n\n"
        "说明：\n"
        "- 本文件由开发模式下的“从 Markdown 更新”功能自动生成；\n"
        "- 本文件是运行时唯一读取源；\n"
        "- 程序封装为 exe 后仍可稳定读取；\n"
        "- 请不要手工编辑本文件，请维护 Markdown 源文档后重新同步。\n"
        "\"\"\"\n\n"
        f"PATCH_PROTOCOL_DOC_SOURCE_PATH = {source_path!r}\n"
        f"PATCH_PROTOCOL_DOC_SOURCE_SHA256 = {source_sha256!r}\n"
        f"PATCH_PROTOCOL_DOC_UPDATED_AT = {updated_at!r}\n\n"
        f"PATCH_PROTOCOL_DOC_TEXT = {source_content!r}\n"
    )


def preview_protocol_doc_update(md_path):
    """确认前取得固定事实，并一次完成模块生成、编码及语法检查。

    无差异不生成未使用模块；本过程不创建成果暂存或改变目标。
    """
    if not is_protocol_doc_update_available():
        raise RuntimeError(
            "已封装程序不支持更新内置协议文档，"
            "请在源码开发环境中更新后重新打包。"
        )
    source, content, encoding = _observe_markdown_source(md_path)
    embedded = get_patch_protocol_doc()
    target = observe_file(TARGET_MODULE_PATH, include_digest=True)
    if source.path == target.path or (
        target.kind == "file"
        and source.object_identity == target.object_identity
    ):
        raise ValueError("Markdown来源和协议目标模块不能是同一对象")
    if any(kind != "directory" for _, kind in target.chain[:-1]):
        raise ValueError("协议模块所在目录必须已经存在")

    source_digest = sha256_text(content)
    embedded_digest = sha256_text(embedded.content)
    difference = source_digest != embedded_digest
    module_bytes = None
    if difference:
        module_content = build_protocol_doc_module_content(
            str(source.path), source_digest, content,
        )
        module_bytes = module_content.encode("utf-8")
        # 非执行式检查；正式执行不再构建、编码或检查候选语法。
        ast.parse(module_content, filename=str(target.path))

    preview = object.__new__(PatchProtocolDocUpdatePreview)
    for name, value in (
        ("source_path", str(source.path)),
        ("source_encoding", encoding),
        ("source_sha256", source_digest),
        ("source_length", len(content)),
        ("embedded_sha256", embedded_digest),
        ("embedded_length", len(embedded.content)),
        ("has_difference", difference),
        ("source_content", content),
        ("source_observation", source),
        ("target_observation", target),
        ("module_bytes", module_bytes),
        ("_lifecycle", "prepared"),
    ):
        object.__setattr__(preview, name, value)
    return preview


def invalidate_protocol_doc_update(preview):
    """取消只消费未执行资格，不改变模块或删除任何正式文件。"""
    if not isinstance(preview, PatchProtocolDocUpdatePreview):
        raise TypeError("取消必须消费本次正式协议更新预览")
    if preview.consumed:
        return False
    object.__setattr__(preview, "_lifecycle", "cancelled")
    return True


def apply_protocol_doc_update(preview):
    """用户确认后一次消费固定成果，返回明确ContentPublicationResult。

    所有执行退出路径永久消费资格；失败不重试、不回滚。
    完整应急规则直接由共享发布核心执行，不提供旧writer旁路。
    """
    if not is_protocol_doc_update_available():
        raise RuntimeError(
            "已封装程序不支持更新内置协议文档，"
            "请在源码开发环境中更新后重新打包。"
        )
    if not isinstance(preview, PatchProtocolDocUpdatePreview):
        raise TypeError("执行必须消费本次正式协议更新预览")
    if preview.consumed:
        raise ValueError("协议更新任务已消费或取消，必须重新预览")
    if not preview.has_difference:
        raise ValueError("协议正文没有差异，不存在待发布模块成果")
    if not isinstance(preview.module_bytes, bytes):
        raise RuntimeError("活动协议任务缺少已准备的完整模块bytes")

    object.__setattr__(preview, "_lifecycle", "attempted")
    artifact = None
    try:
        recheck_file(preview.source_observation)
        recheck_file(preview.target_observation)
        artifact = prepare_content_artifact(
            preview.target_observation, preview.module_bytes,
            options=ContentPublicationOptions(durable_publication=True),
        )
        # 完整暂存跨越新的时间边界；仍消费首次准备的工作bytes。
        recheck_file(preview.source_observation)
        result = publish_content(artifact)
        object.__setattr__(preview, "_lifecycle", "completed")
        return result
    finally:
        if artifact is not None and artifact._lifecycle not in (
            "completed", "disposed",
        ):
            if not (
                artifact.mechanism == "in_place"
                and artifact._lifecycle == "attempted"
            ):
                try:
                    dispose_content_artifact(artifact)
                except (OSError, ValueError) as cleanup_error:
                    print(
                        f"[协议模块自有暂存清理失败] {artifact.path}："
                        f"{cleanup_error}",
                        file=sys.stderr,
                    )
