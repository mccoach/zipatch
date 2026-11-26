# -*- coding: utf-8 -*-

"""单次来源准备的目录名称及懒获取属性；不拥有业务授权或正文。

不同规则复用已经枚举的名称，不重新枚举接受后来成员。
每次复用先比较可靠目录身份及结构时间；跨任务不共享。
只有调用方已经获得适用枚举许可后才能请求目录。
"""

import os
from pathlib import Path

from core.recovery_paths import (
    checked_chain, entity_type_from_attributes, identity,
)


def _revision(attributes):
    return (
        attributes.st_dev, attributes.st_ino,
        attributes.st_mtime_ns, attributes.st_ctime_ns,
    )


def _directory_attributes(path):
    target, chain = checked_chain(path, include_observation=True)
    if chain[-1][1] != "directory":
        raise ValueError(f"来源枚举位置不是既有普通目录：{target}")
    attributes = target.lstat()
    if (
        entity_type_from_attributes(target, attributes) != "directory"
        or type(attributes.st_dev) is not int
        or attributes.st_dev < 0
        or type(attributes.st_ino) is not int
        or attributes.st_ino <= 0
    ):
        raise ValueError(f"来源目录缺少可靠身份：{target}")
    return target, attributes


class SourceEnumeration:
    """本任务唯一目录名称快照；属性只为实际需要的入口取得。

    名称、属性属于固定来源获取，不是执行资格。
    返回成员列表允许调用方复用同一懒获取槽位；
    不保存DirEntry，避免把Windows零设备／编号当可靠对象身份。
    """

    def __init__(self):
        self._directories = {}
        self.directory_reads = 0
        self.entry_attribute_reads = 0

    def entries(self, folder, *, check_cancel=None):
        if check_cancel is not None and not callable(check_cancel):
            raise TypeError("来源枚举取消检查必须可调用")
        if check_cancel is not None:
            check_cancel()
        target, attributes = _directory_attributes(Path(folder))
        key = identity(target)
        previous = self._directories.get(key)
        if previous is not None:
            revision, members = previous
            if _revision(attributes) != revision:
                raise ValueError(
                    f"共享来源目录身份或结构前提变化，禁止重新枚举接受新成员：{target}"
                )
            return members

        members = []
        with os.scandir(target) as entries:
            for entry in entries:
                if check_cancel is not None:
                    check_cancel()
                members.append((entry.name, str(target / entry.name), None))
        _, finished = _directory_attributes(target)
        if _revision(finished) != _revision(attributes):
            raise ValueError(f"来源目录在名称枚举期间变化：{target}")
        if check_cancel is not None:
            check_cancel()
        self._directories[key] = (_revision(finished), members)
        self.directory_reads += 1
        return members

    def attributes(self, members, number):
        """只使用不跟随的路径属性；没有可靠编号时不得假装已固定身份。"""
        name, spelling, attributes = members[number]
        if attributes is None:
            path = Path(spelling)
            # 名称枚举与首次属性获取是不同边界。
            # 先拒绝后来出现的链接父链，不沿新父链探查子项。
            checked_chain(path.parent)
            attributes = path.lstat()
            members[number] = (name, spelling, attributes)
            self.entry_attribute_reads += 1
        return attributes
    def recheck(self):
        """选择交互的新边界只比较原快照，不再次枚举接受新成员。"""
        for key, (revision, members) in self._directories.items():
            target, attributes = _directory_attributes(Path(key))
            if _revision(attributes) != revision:
                raise ValueError(
                    "来源预扫描目录身份或结构变化，必须重新启动任务："
                    f"{target}"
                )