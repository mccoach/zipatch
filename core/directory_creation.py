# -*- coding: utf-8 -*-

"""固定必要目录的纯计划投影、创建身份及正常结果复用。

不观察现场建立新创建授权，不拥有工作或内部保护账本。
所属流程明确提供正式步骤登记和适用的步骤前复核。
"""

from types import MappingProxyType

from core.file_observation import (
    DirectoryObservation, FileObservation, ObjectIdentity, TreeObservation,
)
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult,
    execute_filesystem_change, prepare_directory_creation,
)
from core.recovery_paths import checked_chain, identity


def fixed_directory_observations(observed, *, include_target=False):
    """只投影固定原链中的缺失位置；不查询磁盘或创建目录。"""
    if not isinstance(observed, (FileObservation, TreeObservation)):
        raise TypeError("必要目录计划必须消费正式文件或完整树观察")
    if type(include_target) is not bool:
        raise TypeError("包含目标目录开关必须是布尔值")
    chain = observed.chain if include_target else observed.chain[:-1]
    return tuple(
        FileObservation(
            path, observed.chain[:index + 1], "absent",
            None, None, None, None, None, None,
        )
        for index, (path, kind) in enumerate(chain)
        if kind == "absent"
    )


class DirectoryCreationState:
    """一个执行范围内正常创建目录身份的唯一拥有者。

    身份来自正式正常创建结果，不以后来存在性补认所有权。
    不保存第二份步骤账本，不决定失败后清理或回滚政策。
    """

    def __init__(self):
        self._created = {}

    @property
    def created(self):
        return MappingProxyType(self._created)

    def accept(self, receipt):
        if (
            not isinstance(receipt, FilesystemChangeResult)
            or receipt.kind != "create_directory"
            or not receipt.succeeded
            or not isinstance(receipt.created_directory, DirectoryObservation)
            or receipt.created_directory.path != receipt.target
        ):
            raise RuntimeError("目录身份必须来自正式正常创建结果")
        key = identity(receipt.target)
        if key in self._created:
            raise ValueError("同一执行范围不得重复登记目录创建")
        self._created[key] = receipt.created_directory

    def require(self, path):
        """核对已登记的同一目录；不扫描后代，不接受同名替代对象。"""
        expected = self._created.get(identity(path))
        if expected is None:
            raise ValueError(f"目录没有本次正常创建事实：{path}")
        target, chain = checked_chain(
            expected.path, include_observation=True,
        )
        if chain[-1][1] != "directory":
            raise ValueError(f"本次创建目录存在性或类型变化：{target}")
        attributes = target.lstat()
        if (
            ObjectIdentity(attributes.st_dev, attributes.st_ino)
            != expected.object_identity
        ):
            raise ValueError(f"本次创建目录身份变化：{target}")
        return expected

    def recheck(self, observed=None):
        """检查全部或当前对象实际使用的本次创建父目录。"""
        positions = (
            None if observed is None else
            {identity(path) for path, _ in observed.chain[:-1]}
        )
        for key, expected in self._created.items():
            if positions is None or key in positions:
                self.require(expected.path)

    def recheck_parents(self, observed):
        """原父链只接受原类型，或本次已确认创建的同一普通目录。"""
        _, current = checked_chain(
            observed.path.parent, include_observation=True,
        )
        initial = dict(observed.chain[:-1])
        for path, kind in current:
            expected = initial[path]
            if expected == kind:
                continue
            if (
                expected == "absent" and kind == "directory"
                and identity(path) in self._created
            ):
                continue
            raise ValueError(f"必要父目录偏离固定计划：{path}")
        self.recheck(observed)

    def ensure_all(self, expectations, *, record_step, stage, before_step=None):
        """依序执行固定计划；只有本次正常创建结果可以复用。

        步骤先登记再接受身份；明确失败同样登记一次。
        任一异常停止，不重试、不从现场补造剩余计划。
        """
        expectations = tuple(expectations)
        if not callable(record_step) or not callable(stage):
            raise TypeError("目录执行必须提供正式登记和阶段能力")
        if before_step is not None and not callable(before_step):
            raise TypeError("目录步骤前复核必须可调用")
        # 纯资格校验先于第一步修改，避免非法后项造成可避免的部分执行。
        seen = set()
        for expected in expectations:
            prepare_directory_creation(expected)
            key = identity(expected.path)
            if key in seen:
                raise ValueError("同一必要目录计划包含重复位置")
            seen.add(key)

        for expected in expectations:
            stage(expected.path, "固定目录创建或正常结果复用")
            if before_step is not None:
                before_step()
            self.recheck(expected)
            if identity(expected.path) in self._created:
                self.require(expected.path)
                continue
            try:
                receipt = execute_filesystem_change(
                    prepare_directory_creation(expected),
                )
            except FilesystemChangeError as error:
                record_step(error.result)
                stage(error.result.target, error.result.phase)
                raise
            record_step(receipt)
            self.accept(receipt)
