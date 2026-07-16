# -*- coding: utf-8 -*-

import importlib
import sys
from dataclasses import dataclass

import services.patch_protocol_doc as patch_protocol_doc


@dataclass
class PatchProtocolDoc:
    content: str
    source_path: str
    source_sha256: str
    updated_at: str


def get_patch_protocol_doc():
    """
    获取运行时内置协议文档。

    运行时唯一真相源是 services.patch_protocol_doc：
    - 不读取外部 md；
    - 不依赖 assets；
    - 不依赖 PyInstaller data 文件；
    - 保证封装 exe 后稳定可显示。

    开发模式下允许用户把 Markdown 同步写入 patch_protocol_doc.py。
    为了让同一进程内重新打开弹窗也能看到刚写入的新内容，
    源码运行时每次读取前 reload 一次该模块。
    """
    module = patch_protocol_doc

    if not bool(getattr(sys, "frozen", False)):
        module = importlib.reload(patch_protocol_doc)

    return PatchProtocolDoc(
        content=module.PATCH_PROTOCOL_DOC_TEXT,
        source_path=module.PATCH_PROTOCOL_DOC_SOURCE_PATH,
        source_sha256=module.PATCH_PROTOCOL_DOC_SOURCE_SHA256,
        updated_at=module.PATCH_PROTOCOL_DOC_UPDATED_AT,
    )