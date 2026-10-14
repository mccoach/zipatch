# -*- coding: utf-8 -*-

"""收藏的稳定分区顺序；单一数组同时保存分类内顺序与置顶状态。"""


def order_favorites(entries):
    """置顶区在前；两个分区各自保持输入相对顺序。"""
    return sorted(entries, key=lambda entry: not entry.get("pinned", False))