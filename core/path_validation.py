# -*- coding: utf-8 -*-

"""正式路径输入清洗及修改包相对路径限制。"""

from pathlib import Path


def strip_outer_quotes(value):
    value = (value or "").strip()

    while len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()

    return value


def normalize_windows_display_path(value):
    """
    Windows 路径输入框的显示层清洗。

    只做 UI 文本清洗：
    - 去外层引号；
    - 去首尾空白；
    - 将 / 替换为 \\。

    不解析绝对路径；
    不检查存在性；
    不拼接 base_dir；
    不展开为完整路径。
    """
    return strip_outer_quotes(value).replace("/", "\\")


def normalize_rel_path(rel_path: str):
    """
    修改包执行器专用：标准化相对路径，禁止路径逃逸。

    这是修改包内相对路径安全规则的唯一入口：
    - path 必须是非空字符串；
    - 路径原文来自 boundary 文本块，不擅自清除首尾空白；
    - 禁止绝对路径；
    - 禁止 Windows 盘符路径；
    - 禁止 .. 路径穿越；
    - 内部统一使用 / 作为协议路径分隔符。
    """
    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError("path 必须是非空字符串")

    rel_path = rel_path.replace("\\", "/")

    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise ValueError(f"不允许绝对路径：{rel_path}")

    if len(rel_path) >= 2 and rel_path[1] == ":":
        raise ValueError(f"不允许 Windows 盘符绝对路径：{rel_path}")

    if "*" in rel_path or "?" in rel_path:
        raise ValueError(f"不允许路径包含通配符：{rel_path}")

    parts = Path(rel_path).parts

    if any(part == ".." for part in parts):
        raise ValueError(f"不允许路径穿越 '..'：{rel_path}")

    return rel_path


def safe_join(root: Path, rel_path: str):
    """
    修改包执行器专用：把相对路径安全拼接到项目根目录下。

    在任何链接解析前检查原路径链，拒绝链接、特殊实体和项目越界。
    """
    from core.recovery_paths import project_path
    return project_path(root, rel_path)
