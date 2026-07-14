# -*- coding: utf-8 -*-


CONTENT_OPS = {
    "append_text",
    "replace_exact",
    "replace_between",
}

REPLACE_OPS = {
    "replace_exact",
    "replace_between",
}

DUAL_PATH_OPS = {
    "rename_file",
    "move_file",
    "copy_file",
    "rename_dir",
    "move_dir",
    "copy_dir",
}

PATH_ONLY_OPS = {
    "delete_file",
    "delete_dir",
    "rename_file",
    "move_file",
    "copy_file",
    "rename_dir",
    "move_dir",
    "copy_dir",
    "create_dir",
}


def parse_required_positive_int(value, name="整数"):
    if value is None:
        raise ValueError(f"{name} 必须显式声明")

    try:
        number = int(str(value).strip())
    except Exception:
        raise ValueError(f"{name} 非法：{value}")

    if number < 1:
        raise ValueError(f"{name} 必须大于等于 1：{value}")

    return number