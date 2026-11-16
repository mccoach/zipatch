# -*- coding: utf-8 -*-

"""独立执行范围的追加账本、活动前缀归档和失败现场。

结果解释由所属核心提供；不识别业务名称，不授予文件系统权限。
不同实例隔离工作、内部材料及其子步骤，不合并所有权。
"""


class ExecutionLedger:
    """正式步骤保持原引用；活动步骤退出后只归档一次。"""

    def __init__(self, results_view):
        if not callable(results_view):
            raise TypeError("执行账本必须具有明确的活动结果视图")
        self._results_view = results_view
        self._results = ()
        self._active = None
        self._active_results = ()
        self.scene = None

    def _validate_results(self, receipts):
        """通用结构约束；拥有具体结果语义的子类增加成员约束。"""
        if type(receipts) is not tuple:
            raise TypeError("步骤结果必须是原步骤对象元组")

    def _view(self, prepared):
        results = self._results_view(prepared)
        self._validate_results(results)
        return results

    @property
    def results(self):
        if self._active is None:
            return self._results
        current = self._view(self._active)
        previous = self._active_results
        if len(current) < len(previous) or any(
            actual is not accepted
            for actual, accepted in zip(current, previous)
        ):
            raise ValueError("活动组合步骤不是原结果的追加前缀")
        self._active_results = current
        return (*self._results, *current)

    @property
    def active_change(self):
        return self._active if self._active is not None else self.scene

    def append_results(self, receipts):
        """所属核心交付正式结果；活动组合不得另行重复登记子步骤。"""
        self._validate_results(receipts)
        if self._active is not None and self._view(self._active):
            raise ValueError("复合工作步骤由活动账本归档，禁止另行追加")
        self._results = (*self._results, *receipts)

    def execute_active(self, prepared, executor, *, stage, **keywords):
        """退出时归档可靠前缀；完整性失败也必须结束活动状态。

        最后视图非法时只保留此前已接受的原引用前缀，不补认新结果。
        完整性异常自然传播；若执行器也失败，其异常上下文仍保留。
        失败现场仅供诊断，不贡献第二份步骤或重新执行资格。
        """
        if self._active is not None:
            raise RuntimeError("执行范围的活动动作不能重入")
        if prepared is None:
            raise TypeError("活动执行必须具有正式准备对象")
        if not callable(executor) or not callable(stage):
            raise TypeError("活动执行必须提供明确执行及阶段能力")
        self._view(prepared)
        self._active = prepared
        self._active_results = ()
        completed = False
        archived = False
        try:
            result = executor(prepared, **keywords)
            completed = True
            return result
        finally:
            try:
                results = self.results
                self._results = results
                archived = True
            finally:
                if not archived:
                    self._results = (*self._results, *self._active_results)
                self._active = None
                self._active_results = ()
                if completed and archived:
                    self.scene = None
                else:
                    self.scene = prepared
                    stage(prepared.current, prepared.phase)
