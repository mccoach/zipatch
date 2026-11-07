# -*- coding: utf-8 -*-

"""修改及恢复的备份选址政策；不读取正文或实现内容指纹。"""

import os
from pathlib import Path

from core.path_validation import strip_outer_quotes
from core.recovery_paths import checked_chain


def resolve_backup_base_dir(project_root, backup_dir):
    # 保留UI输入的外围清洗和变量展开；检查之前不消除原路径导航。
    selected = strip_outer_quotes(backup_dir)
    if not selected:
        raise ValueError("备份目录不能为空")
    expanded = os.path.expandvars(os.path.expanduser(selected))
    expanded = expanded.replace("\\", os.path.sep).replace("/", os.path.sep)
    root = checked_chain(project_root)
    candidate = Path(expanded)
    if os.name == "nt" and candidate.drive and not candidate.root:
        raise ValueError(f"不支持依赖驱动器当前目录的备份路径：{backup_dir}")
    if not candidate.is_absolute():
        candidate = root / candidate
    return checked_chain(candidate)
