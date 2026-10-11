# -*- coding: utf-8 -*-


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


def read_text_content_for_merge(
    file_path, read_error_message, cannot_read_message, *, strict=False,
):
    """读取一次字节；按需读取故障传播，常规合并保留正文提示语义。"""
    path = Path(file_path)
    try:
        source_bytes = path.read_bytes()
    except OSError as error:
        if strict:
            raise OSError(f"无法读取按需合并源文件：{path}\n{error}") from error
        return f"{read_error_message}: {error}"

    for encoding in ENCODINGS_TO_TRY:
        try:
            content = source_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        # 与原 Path.read_text 的通用换行行为一致。
        return content.replace("\r\n", "\n").replace("\r", "\n")

    if strict:
        raise ValueError(f"无法使用指定编码读取按需合并源文件：{path}")
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


def get_text_value(text_widget):
    return text_widget.get("1.0", "end").rstrip("\n")


def set_text_value(text_widget, value):
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value or "")


def replace_text_keep_undo(text_widget, value):
    """整体替换是一项撤销操作，不让自动分隔拆开删除和插入。"""
    automatic_separators = text_widget.cget("autoseparators")
    text_widget.edit_separator()
    text_widget.configure(autoseparators=False)
    try:
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", value or "")
    finally:
        text_widget.edit_separator()
        text_widget.configure(autoseparators=automatic_separators)


def replace_text_preserve_view(text_widget, value):
    """同步替换展示内容；不刷新事件循环，不产生撤销历史。"""
    first, last = text_widget.yview()
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value or "")
    if last >= 0.999:
        text_widget.see("end")
    else:
        text_widget.yview_moveto(first)


def split_lines_keep_text(text: str):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
