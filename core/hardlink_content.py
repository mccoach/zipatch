# -*- coding: utf-8 -*-

"""固定硬链接原对象写回与独立完整应急成果。

业务授权、正式保护和started归调用方。
本模块不生成恢复清单，不扫描其他关联名称。
"""

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from core.recovery_paths import checked_chain


_CONTENT_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class HardlinkContentExpectation:
    target: Path
    identity: tuple
    links: int
    digest: str


@dataclass(frozen=True)
class HardlinkContentResult:
    target: Path
    identity: tuple
    length: int
    digest: str
    mtime_ns: int | None


class HardlinkContentError(OSError):
    """完整应急成果已准备，明确表达目标改变及正文完成事实。"""

    def __init__(
        self, message, *, target, emergency_path, phase,
        mutation_attempted, content_completed, confirmation_completed,
    ):
        super().__init__(message)
        self.target = target
        self.emergency_path = emergency_path
        self.phase = phase
        self.mutation_attempted = mutation_attempted
        self.content_completed = content_completed
        self.confirmation_completed = confirmation_completed


def _file_identity(attributes):
    if (
        not stat.S_ISREG(attributes.st_mode)
        or type(attributes.st_dev) is not int
        or attributes.st_dev < 0
        or type(attributes.st_ino) is not int
        or attributes.st_ino <= 0
        or type(attributes.st_nlink) is not int
        or attributes.st_nlink < 1
    ):
        raise OSError("无法取得可靠的普通文件身份或链接数量")
    return attributes.st_dev, attributes.st_ino


def _check_attributes(attributes, expected):
    if (
        _file_identity(attributes) != expected.identity
        or attributes.st_nlink != expected.links
    ):
        raise ValueError(
            f"硬链接目标身份或链接数量已偏离固定前提：{expected.target}；"
            "必须重新预演及确认"
        )


def _check_path(expected):
    target = checked_chain(expected.target)
    _check_attributes(target.lstat(), expected)
    return target


def _stream_digest(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    length = 0
    while True:
        chunk = stream.read(_CONTENT_CHUNK_BYTES)
        if not chunk:
            return digest.hexdigest(), length
        digest.update(chunk)
        length += len(chunk)


def _write_all(stream, content):
    view = memoryview(content)
    offset = 0
    while offset < len(view):
        written = stream.write(view[offset:])
        if (
            type(written) is not int
            or written <= 0
            or written > len(view) - offset
        ):
            raise OSError("文件写入未取得有效进展或返回非法长度")
        offset += written


def _check_original(stream, expected):
    _check_attributes(os.fstat(stream.fileno()), expected)
    _check_path(expected)
    digest, length = _stream_digest(stream)
    attributes = os.fstat(stream.fileno())
    _check_attributes(attributes, expected)
    _check_path(expected)
    if digest != expected.digest or attributes.st_size != length:
        raise ValueError(
            f"硬链接目标原字节已偏离固定前提：{expected.target}；"
            "必须重新预演及确认"
        )


def _check_result(stream, expected, desired_digest, length, mtime_ns):
    digest, actual_length = _stream_digest(stream)
    # 完整读取后的属性核验不能由读取前的属性代替。
    attributes = os.fstat(stream.fileno())
    _check_attributes(attributes, expected)
    _check_path(expected)
    if (
        digest != desired_digest
        or actual_length != length
        or attributes.st_size != length
    ):
        raise OSError(f"硬链接写回成果内容或长度不符：{expected.target}")
    if mtime_ns is not None and attributes.st_mtime_ns != mtime_ns:
        raise OSError(f"正式文件修改时间未准确恢复：{expected.target}")


def _publish_prepared_hardlink(
    expected, content, emergency, emergency_identity, desired_digest, *,
    mtime_ns=None, before_mutation=None, durable_publication=False,
    file_mode=None, atime_ns=None,
):
    """消费完整应急成果的唯一写回算法，不决定业务授权或保护。

    共享内容发布消费已完整准备的同一应急成果，
    不复制短写、截断、同步、核验和清理实现。
    即时回调仅复核固定前提，不组织业务授权或正式保护。
    """
    target = expected.target
    phase = "原对象打开"
    mutation_attempted = False
    content_completed = False
    confirmation_completed = False

    try:
        target = _check_path(expected)
        if (
            os.name == "nt"
            and not stat.S_IMODE(target.lstat().st_mode) & stat.S_IWRITE
        ):
            raise PermissionError(f"硬链接目标为只读，不能覆盖：{target}")
        # r+b不会创建或提前截断目标；写回使用同一个句柄。
        with target.open("r+b", buffering=0) as stream:
            phase = "打开句柄身份及固定原内容核验"
            _check_original(stream, expected)

            if before_mutation is not None:
                phase = "破坏性写入前业务复核"
                before_mutation()
                # 回调是新的时间边界，不能复用回调前内容结论。
                _check_original(stream, expected)

            # 打开、原内容读取和即时回调已经跨越新的时间边界。
            # 入场检查不能证明此刻完整独立应急仍然成立。
            # 两个入口共用这个最后关口，不把应急路径存在当成完整性。
            phase = "首次写入前完整独立应急成果复核"
            from core.file_observation import observe_file
            prepared = observe_file(emergency, include_digest=True)
            if (
                prepared.kind != "file"
                or (
                    prepared.object_identity.device,
                    prepared.object_identity.inode,
                ) != emergency_identity
                or prepared.link_count != 1
                or prepared.attributes.st_size != len(content)
                or prepared.digest != desired_digest
            ):
                raise ValueError(
                    "首次写入前完整独立应急成果已偏离固定前提，"
                    "禁止开始原对象写回"
                )

            phase = "原对象完整内容写入"
            stream.seek(0)
            mutation_attempted = True
            _write_all(stream, content)

            phase = "原对象准确长度调整"
            stream.truncate(len(content))
            phase = "原对象刷新及文件同步"
            stream.flush()
            os.fsync(stream.fileno())

            if mtime_ns is not None:
                phase = "正式文件修改时间设置"
                _check_attributes(os.fstat(stream.fileno()), expected)
                _check_path(expected)
                attributes = os.fstat(stream.fileno())
                times = (
                    attributes.st_atime_ns if atime_ns is None else atime_ns,
                    mtime_ns,
                )
                if os.utime in os.supports_fd:
                    os.utime(stream.fileno(), ns=times)
                else:
                    # Windows标准库无通用句柄utime；即时核验路径，
                    # 不承诺消除路径系统调用之间的全部竞态。
                    os.utime(target, ns=times)
                os.fsync(stream.fileno())

            phase = "原对象完整成果及身份核验"
            _check_result(
                stream, expected, desired_digest, len(content), mtime_ns,
            )
            if file_mode is not None:
                # 完整正文已写入并核验后再设置权限：
                # 只读来源不能使工作句柄提前无法打开。
                phase = "原对象基础权限设置及核验"
                _check_path(expected)
                if os.chmod in os.supports_fd:
                    os.chmod(stream.fileno(), file_mode)
                else:
                    os.chmod(target, file_mode)
                _check_path(expected)
                if stat.S_IMODE(target.lstat().st_mode) != file_mode:
                    raise OSError("原对象基础权限未准确设置")
                os.fsync(stream.fileno())
            phase = "原对象关闭"

        phase = "原对象关闭后属性核验"
        target = _check_path(expected)
        attributes = target.lstat()
        if (
            attributes.st_size != len(content)
            or mtime_ns is not None and attributes.st_mtime_ns != mtime_ns
            or file_mode is not None
            and stat.S_IMODE(attributes.st_mode) != file_mode
        ):
            raise OSError(
                f"硬链接目标关闭后长度、正式时间或基础权限变化：{target}"
            )
        content_completed = True
        if durable_publication:
            from core.atomic_file import sync_directory
            phase = "原对象所在目录持久化确认"
            sync_directory(target.parent)
        confirmation_completed = True

        phase = "完整应急成果清理"
        safe_emergency = checked_chain(emergency)
        attributes = safe_emergency.lstat()
        if (
            _file_identity(attributes) != emergency_identity
            or attributes.st_nlink != 1
        ):
            raise ValueError("应急成果路径已被替换，禁止清理其他对象")
        safe_emergency.unlink()

    except (OSError, ValueError) as error:
        raise HardlinkContentError(
            f"硬链接内容发布未完整结束：{target}\n"
            f"失败阶段：{phase}\n"
            f"目标改变调用已发起：{'是' if mutation_attempted else '否'}\n"
            f"正文完成已确认：{'是' if content_completed else '否'}\n"
            f"完整新成果应急准备位置：{emergency}\n"
            "本次没有使用原子替换；没有替换不能证明目标内容未变。"
            "写入已开始时，其他硬链接名称可能看到部分内容。\n"
            "完整成果不自动清理；若外部已移动、替换或删除它，"
            "本模块不能保证该路径仍保有原成果。\n"
            "停止后续，不自动重试或回滚。保持硬链接关系时应把"
            "应急文件内容写入原对象，不应重命名覆盖原路径。\n"
            f"{error}",
            target=target, emergency_path=emergency, phase=phase,
            mutation_attempted=mutation_attempted,
            content_completed=content_completed,
            confirmation_completed=confirmation_completed,
        ) from error

    return HardlinkContentResult(
        target, expected.identity, len(content), desired_digest, mtime_ns,
    )