# -*- coding: utf-8 -*-

"""共享文件系统核心使用的目录同步机制；不提供业务发布入口。"""

import os


def sync_directory(directory):
    """Windows标准库无本项目采用的通用目录fsync接口。

    既有POSIX机制保留在这一基础职责中，不产生新增平台支持责任，
    也不将Windows正常返回解释为断电持久化保证。
    """
    if os.name == "nt":
        return
    descriptor = os.open(
        directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
