# -*- coding: utf-8 -*-

"""完整文本成果的同目录暂存与原子提交。"""

import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile


class PublicationError(OSError):
    """replacement 为 unknown 或 replaced，不能推断旧文件一定未变。"""

    def __init__(self, message, replacement):
        super().__init__(message)
        self.replacement = replacement


def sync_directory(directory):
    """POSIX 请求目录 fsync；Windows 标准库无等价通用目录接口。"""
    if os.name == "nt":
        return
    descriptor = os.open(
        directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _existing_output_mode(target):
    """只取得本批普通成果需要保留的基础权限，不复制高级元数据。"""
    try:
        attributes = target.stat()
    except FileNotFoundError:
        return None

    if stat.S_ISDIR(attributes.st_mode):
        raise IsADirectoryError(f"成果目标是文件夹：{target}")
    if not stat.S_ISREG(attributes.st_mode):
        raise ValueError(f"成果目标不是普通文件：{target}")

    mode = stat.S_IMODE(attributes.st_mode)
    if os.name == "nt" and not mode & stat.S_IWRITE:
        raise PermissionError(f"成果文件为只读，不能覆盖：{target}")
    return mode


@contextmanager
def atomic_text_writer(target_path, *, newline=None, durable_publication=False):
    """
    消费方只向临时流生成正文；完整生成并关闭后才提交正式目标。

    临时文件由标准库以独占创建方式生成，位于目标同目录。
    newline 由调用方按已有文本契约指定，默认平台文本换行。
    newline="" 不转换正文换行；编码错误不允许静默忽略。

    已有普通目标保留基础权限位，不保留旧修改时间。
    不承诺高级 ACL、所有者、备用数据流等元数据保留，
    也不协调并发写入或提供多文件事务、启动恢复。
    """
    if durable_publication:
        from core.recovery_paths import checked_chain
        target = checked_chain(target_path)
    else:
        target = Path(target_path).resolve()
    existing_mode = _existing_output_mode(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline=newline,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            yield stream
            if existing_mode is not None:
                os.chmod(temporary, existing_mode)
            stream.flush()
            os.fsync(stream.fileno())

        # Windows 替换前关闭临时流；同目录避免跨文件系统复制。
        try:
            os.replace(temporary, target)
        except OSError as error:
            if durable_publication:
                raise PublicationError(
                    f"原子替换报告错误，替换结果无法确认：{target}\n{error}",
                    "unknown",
                ) from error
            raise
        temporary = None
        if durable_publication:
            try:
                sync_directory(target.parent)
            except OSError as error:
                raise PublicationError(
                    f"正式文件已替换，但目录持久化失败：{target}\n{error}",
                    "replaced",
                ) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                # 清理失败是独立事实，不能掩盖原始生成或提交异常。
                print(
                    f"[成果临时文件清理失败] {temporary}：{cleanup_error}",
                    file=sys.stderr,
                )
