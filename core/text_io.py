# -*- coding: utf-8 -*-


from contextlib import nullcontext
from pathlib import Path


from core.atomic_file import atomic_text_writer
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
    """读取一次字节；按名单合并读取故障传播，按目录合并保留正文提示语义。"""
    path = Path(file_path)
    try:
        source_bytes = path.read_bytes()
    except OSError as error:
        if strict:
            raise OSError(f"无法读取按名单合并源文件：{path}\n{error}") from error
        return f"{read_error_message}: {error}"

    for encoding in ENCODINGS_TO_TRY:
        try:
            content = source_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        # 与原 Path.read_text 的通用换行行为一致。
        return content.replace("\r\n", "\n").replace("\r", "\n")

    if strict:
        raise ValueError(f"无法使用指定编码读取按名单合并源文件：{path}")
    return f"{cannot_read_message}，内容略"


def read_text_auto(path: Path):
    data = Path(path).read_bytes()

    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def write_text_utf8(path: Path, text: str):
    """提交完整 UTF-8 正文，保持原有不转换换行、不补末尾换行契约。"""
    with atomic_text_writer(path, newline="") as stream:
        stream.write(text)


def get_text_value(text_widget):
    return text_widget.get("1.0", "end").rstrip("\n")


def set_text_value(text_widget, value):
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", value or "")


def replace_text_keep_undo(text_widget, value):
    """整体替换及所属自动处理同次完成，不制造相同正文的撤销历史。"""
    final = value or ""
    editing = getattr(text_widget, "_list_text_editing", None)
    if editing is not None:
        from core.rule_lists import clean_rule_quotes
        final = clean_rule_quotes(final)
        if editing.complete() == final:
            return
    elif text_widget.get("1.0", "end-1c") == final:
        return
    with (
        editing.edit_group(prepared_text=final)
        if editing is not None else nullcontext()
    ):
        automatic = text_widget.cget("autoseparators")
        text_widget.edit_separator()
        text_widget.configure(autoseparators=False)
        try:
            text_widget.replace("1.0", "end-1c", final)
        finally:
            text_widget.edit_separator()
            text_widget.configure(autoseparators=automatic)


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
