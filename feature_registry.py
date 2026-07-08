# -*- coding: utf-8 -*-


def get_feature_registry():
    """
    功能注册表。

    主窗口不直接知道每个功能怎么创建，
    只通过这里拿到 key、标题、面板类。
    """

    from panels.scan_panel import ScanPanel
    from panels.merge_panel import MergePanel
    from panels.restore_panel import RestorePanel
    from panels.patch_panel import PatchPanel

    return {
        "scan": {
            "title": "路径全景扫描",
            "panel_class": ScanPanel,
            "default_order": 10,
        },
        "merge": {
            "title": "文件代码合并",
            "panel_class": MergePanel,
            "default_order": 20,
        },
        "restore": {
            "title": "代码拆分还原",
            "panel_class": RestorePanel,
            "default_order": 30,
        },
        "patch": {
            "title": "修改包执行器",
            "panel_class": PatchPanel,
            "default_order": 40,
        },
    }
