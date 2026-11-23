# -*- coding: utf-8 -*-

"""一次来源任务的固定普通子树枚举决定，不持久化、不授予正文访问。"""

from core.link_paths import LinkPathObservation, recheck_link_path
from core.recovery_paths import identity


class EnumerationAuthorization:
    """同一明确入口及完整观察可以复用本任务已取得的枚举决定。

    权限语义统一为指定实际普通目录及其普通子树的元信息枚举；
    不跟随途中其他链接，不读取正文，不改变来源。
    不同名义入口不能因为实际目录相同而隐藏其访问依赖。
    """

    def __init__(self, authorize):
        if authorize is not None and not callable(authorize):
            raise TypeError("来源枚举授权必须可调用")
        self._authorize = authorize
        self._approved = {}

    def select(self, accesses, selector):
        """一次展示已知完整枚举范围，返回未选名义前缀，不进行枚举。"""
        from core.work_selection import selected_indices

        if not callable(selector):
            raise TypeError("枚举范围选择必须可调用")
        unique = {}
        for access in accesses:
            if (
                not isinstance(access, LinkPathObservation)
                or not access.follow_final_link
                or access.entry_link is not None
                or access.kind != "directory"
            ):
                raise TypeError("枚举选择必须消费实际普通目录访问观察")
            key = identity(access.nominal_path)
            previous = unique.setdefault(key, access)
            if previous != access:
                raise ValueError("同一枚举前缀在集中准备期间变化")
            recheck_link_path(access)
        fixed = tuple(unique.values())
        if not fixed:
            return ()
        retained = set(selected_indices(
            selector(fixed), len(fixed), description="实际目录枚举范围选择",
        ))
        # 只复核被选择范围；未选范围不成为后续任务依赖。
        for number, access in enumerate(fixed):
            if number in retained:
                recheck_link_path(access)
                self._approved[identity(access.nominal_path)] = access
        return tuple(
            identity(access.nominal_path)
            for number, access in enumerate(fixed) if number not in retained
        )

    def request(self, access, title, description):
        if (
            not isinstance(access, LinkPathObservation)
            or not access.follow_final_link
            or access.entry_link is not None
            or access.kind != "directory"
        ):
            raise TypeError("枚举授权必须消费固定的实际普通目录访问观察")
        key = identity(access.nominal_path)
        previous = self._approved.get(key)
        if previous is not None and previous != access:
            raise ValueError("同一枚举入口的固定观察变化，禁止复用旧授权")
        recheck_link_path(access)
        if previous is not None:
            return True
        if self._authorize is None:
            return False
        decision = self._authorize(title, description)
        if type(decision) is not bool:
            raise TypeError("来源枚举授权必须返回明确布尔值")
        if not decision:
            return False
        # 交互是新的时间边界；变化后不能登记授权，也不能开始枚举。
        recheck_link_path(access)
        self._approved[key] = access
        return True