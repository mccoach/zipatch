# -*- coding: utf-8 -*-

import re
from pathlib import Path

from core.constants import ENCODINGS_TO_TRY


def read_text_with_fallback_encodings(file_path):
    path = Path(file_path)

    for enc in ENCODINGS_TO_TRY:
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue

    raise ValueError(f"无法使用任何指定编码读取文件：{file_path}")


def read_text_content_for_merge(file_path, read_error_message, cannot_read_message):
    path = Path(file_path)

    for enc in ENCODINGS_TO_TRY:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except Exception as e:
            return f"{read_error_message}: {e}"

    return f"{cannot_read_message}，内容略"


def read_text_auto(path: Path):
    data = Path(path).read_bytes()

    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc), enc
        except Exception:
            pass

    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def write_text_utf8(path: Path, text: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def parse_list_text(text, normalize_ext=False):
    """
    把用户输入的排除名单解析成列表。
    支持：
    - 换行
    - 英文逗号
    - 中文逗号
    - 空格
    """
    if not text:
        return []

    parts = re.split(r"[\s,，]+", text)
    result = []
    seen = set()

    for item in parts:
        value = item.strip()

        if not value:
            continue

        if normalize_ext and not value.startswith("."):
            value = "." + value

        if value not in seen:
            result.append(value)
            seen.add(value)

    return result


def get_text_value(text_widget):
    return text_widget.get("1.0", "end").rstrip("\n")


def set_text_value(text_widget, value):
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value or "")


def replace_text_keep_undo(text_widget, value):
    try:
        text_widget.edit_separator()
    except Exception:
        pass

    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value or "")

    try:
        text_widget.edit_separator()
    except Exception:
        pass


def split_lines_keep_text(text: str):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
