# -*- coding: utf-8 -*-

"""普通文件内容指纹与备份选址；不再提供旧 manifest 恢复能力。"""

import hashlib
import os
from pathlib import Path

from core.path_validation import strip_outer_quotes
from core.recovery_paths import checked_chain


def file_content_sha256(path: Path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_backup_base_dir(project_root, backup_dir):
    # 备份目录沿用 UI 输入的去外围空白、去外层引号与变量展开语义。
    # 不先 normpath：link/../backup 必须仍暴露经过 link 的原路径链。
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
