# -*- coding: utf-8 -*-

"""来源业务的完整路径复核入口；检查算法由共同路径核心拥有。

正文遗漏只由所属合并任务登记，普通正文版本仍由文件摘要负责。
每次调用是独立新边界，不跨交互或任务缓存成功结论。
"""

from core.link_paths import recheck_fixed_paths


def recheck_source_paths(accesses, *, omitted_targets=()):
    """核对完整原入口；遗漏普通正文仍保留原目录及链接依赖。"""
    recheck_fixed_paths(
        accesses, policy="complete", omitted_targets=omitted_targets,
    )