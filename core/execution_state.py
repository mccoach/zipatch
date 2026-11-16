# -*- coding: utf-8 -*-

"""共同工作账本及正常创建目录身份。

追加、活动前缀归档和失败现场继承唯一账本算法。
本类型只增加正式工作步骤约束及任务目录身份职责。
"""

from core.content_publication import ContentPublicationResult
from core.directory_creation import DirectoryCreationState
from core.execution_ledger import ExecutionLedger
from core.filesystem_changes import (
    FilesystemChangeError, FilesystemChangeResult,
    execute_filesystem_change,
)
from core.name_moves import NativeNameMoveResult


def active_work_results(active):
    """正式组合的步骤视图；只解包，不复制或重建步骤事实。"""
    from core.directory_file_replacement import PreparedDirectoryFileReplacement
    from core.filesystem_changes import (
        PreparedTreeCopy, PreparedTreeRemoval, tree_execution_results,
    )
    from core.fixed_content_execution import FixedContentExecution
    from core.name_moves import PreparedNameMove

    if active is None or isinstance(active, FixedContentExecution):
        # 文件执行即时登记发布结果，不再次贡献同一步骤。
        return ()
    if isinstance(active, (PreparedTreeCopy, PreparedTreeRemoval)):
        return tree_execution_results(active)
    if isinstance(active, (PreparedNameMove, PreparedDirectoryFileReplacement)):
        return active.results
    raise TypeError("活动工作必须是正式文件或复合执行核心")


class ExecutionState(ExecutionLedger):
    """任务工作账本增加工作类型约束；辅助输出不进入本实例。"""

    def __init__(self):
        super().__init__(active_work_results)
        self.directories = DirectoryCreationState()

    @property
    def created_directories(self):
        return self.directories.created

    def _validate_results(self, receipts):
        super()._validate_results(receipts)
        if any(
            not isinstance(receipt, (
                ContentPublicationResult, FilesystemChangeResult,
                NativeNameMoveResult,
            ))
            for receipt in receipts
        ):
            raise TypeError("工作账本只接受正式工作步骤")

    def record_step(self, receipt):
        self.append_results((receipt,))

    def execute_step(self, prepared, *, stage):
        """单步骤登记正常或明确失败结果，程序缺陷保留原现场。"""
        self.scene = prepared
        try:
            receipt = execute_filesystem_change(prepared)
        except FilesystemChangeError as error:
            self.record_step(error.result)
            stage(error.result.target, error.result.phase)
            raise
        self.record_step(receipt)
        self.scene = None
        return receipt

    def accept_created_directory(self, receipt):
        """显式工作目录创建必须先进入本任务正式账本。"""
        if not any(receipt is member for member in self._results):
            raise RuntimeError("创建目录登记缺少本任务正式正常步骤")
        self.directories.accept(receipt)

    def recheck_created_directories(self, observed=None):
        self.directories.recheck(observed)

    def recheck_parents(self, observed):
        self.directories.recheck_parents(observed)
