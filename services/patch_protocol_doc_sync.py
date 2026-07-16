# -*- coding: utf-8 -*-

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from core.text_io import read_text_with_fallback_encodings
from core.time_utils import current_timestamp_text
from services.patch_protocol_doc_service import get_patch_protocol_doc


TARGET_MODULE_PATH = Path(__file__).resolve().parent / "patch_protocol_doc.py"


@dataclass
class PatchProtocolDocUpdatePreview:
    source_path: str
    source_encoding: str
    source_sha256: str
    source_length: int
    embedded_sha256: str
    embedded_length: int
    has_difference: bool
    source_content: str


def is_protocol_doc_update_available():
    """
    内置协议文档更新只允许在源码开发模式执行。

    PyInstaller 封装后 sys.frozen 为 True，此时 exe 内部模块不可被真正更新，
    因此 UI 会隐藏更新入口，服务层也保留防御性限制。
    """
    return not bool(getattr(sys, "frozen", False))


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def read_markdown_source(md_path):
    path = Path(md_path).expanduser().resolve()

    if not path.is_file():
        raise ValueError(f"Markdown 源文档不存在或不是文件：{path}")

    content, encoding = read_text_with_fallback_encodings(path)

    return str(path), content, encoding


def preview_protocol_doc_update(md_path):
    """
    读取用户指定 Markdown，并与当前内置协议文档比对。
    """
    source_path, source_content, source_encoding = read_markdown_source(md_path)
    embedded = get_patch_protocol_doc()

    source_sha256 = sha256_text(source_content)
    embedded_sha256 = sha256_text(embedded.content)

    return PatchProtocolDocUpdatePreview(
        source_path=source_path,
        source_encoding=source_encoding,
        source_sha256=source_sha256,
        source_length=len(source_content),
        embedded_sha256=embedded_sha256,
        embedded_length=len(embedded.content),
        has_difference=source_sha256 != embedded_sha256,
        source_content=source_content,
    )


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


def apply_protocol_doc_update(preview):
    """
    将已确认的 Markdown 内容写入内置协议文档模块。
    """
    if not is_protocol_doc_update_available():
        raise RuntimeError("已封装程序不支持更新内置协议文档，请在源码开发环境中更新后重新打包。")

    if not isinstance(preview, PatchProtocolDocUpdatePreview):
        raise ValueError("协议文档更新预览对象非法。")

    module_content = build_protocol_doc_module_content(
        source_path=preview.source_path,
        source_sha256=preview.source_sha256,
        source_content=preview.source_content,
    )

    TARGET_MODULE_PATH.write_text(
        module_content,
        encoding="utf-8",
        newline="\n",
    )

    return str(TARGET_MODULE_PATH)