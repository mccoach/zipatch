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

NON_CONTENT_OPS = {
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

OP_ALLOWED_HEAD_PARAMS = {
    "write_file": {"id", "if_exists"},
    "append_text": {"id"},
    "replace_between": {"id"},
    "replace_exact": {"id", "count"},
    "delete_file": {"id"},
    "delete_dir": {"id"},
    "rename_file": {"id", "if_exists"},
    "move_file": {"id", "if_exists"},
    "copy_file": {"id", "if_exists"},
    "rename_dir": {"id", "if_exists"},
    "move_dir": {"id", "if_exists"},
    "copy_dir": {"id", "if_exists"},
    "create_dir": {"id", "if_exists"},
}

OP_REQUIRED_TEXT_BLOCKS = {
    "write_file": {"path", "content"},
    "append_text": {"path", "content"},
    "replace_between": {
        "path",
        "start_marker",
        "end_marker",
        "content",
    },
    "replace_exact": {"path", "old", "new"},
    "delete_file": {"path"},
    "delete_dir": {"path"},
    "rename_file": {"path", "new_path"},
    "move_file": {"path", "new_path"},
    "copy_file": {"path", "new_path"},
    "rename_dir": {"path", "new_path"},
    "move_dir": {"path", "new_path"},
    "copy_dir": {"path", "new_path"},
    "create_dir": {"path"},
}


def parse_required_positive_int(value, name="整数"):
    if value is None:
        raise ValueError(f"{name} 必须显式声明")

    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{name} 非法：{value}")

    if number < 1:
        raise ValueError(f"{name} 必须大于等于 1：{value}")

    return number