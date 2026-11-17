# -*- coding: utf-8 -*-

"""已获取bytes的用途解码；不读取、发布文件或操作Tk控件。"""

from core.constants import ENCODINGS_TO_TRY


class TextDecodingError(ValueError):
    """正式严格候选全部失败，不代表程序缺陷或文件类型认证。"""


def decode_import_text_bytes(source_bytes, *, path, preserve_newlines=False):
    """普通导入的纯解码政策；消费已有bytes，不读取或发布文件。"""
    if not isinstance(source_bytes, bytes):
        raise TypeError("普通导入解码必须消费不可变bytes")
    if type(preserve_newlines) is not bool:
        raise TypeError("保留导入换行开关必须是布尔值")
    for encoding in ENCODINGS_TO_TRY:
        try:
            content = source_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not preserve_newlines:
            content = content.replace("\r\n", "\n").replace("\r", "\n")
        return content, encoding
    raise ValueError(f"无法使用任何指定编码读取文件：{path}")


def decode_text_bytes(source_bytes, *, source_description, path):
    """明确BOM决定编码；无标记文件沿用原严格候选，不转换换行。"""
    if not isinstance(source_bytes, bytes):
        raise TypeError("严格文本解码必须消费不可变bytes")

    # UTF-32小端标记与UTF-16小端共享前缀，必须先排除，
    # 避免将尚不支持的UTF-32误读成含NUL的UTF-16正文。
    if source_bytes.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        raise TextDecodingError(
            f"{source_description}使用尚不支持的UTF-32编码标记：{path}\n"
            "本次不猜测其他编码，不生成或发布损坏正文。"
        )

    if source_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            # utf-16消费BOM并确定字节序，不把标记写入源码正文。
            return source_bytes.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as error:
            raise TextDecodingError(
                f"{source_description}带有UTF-16编码标记，但正文无法严格解码："
                f"{path}\n{error}\n"
                "明确编码的损坏正文不再尝试其他编码，"
                "不使用替代字符，不生成或发布损坏正文。"
            ) from error

    encodings = ("utf-8-sig", "utf-8", "gb18030", "gbk")
    for encoding in encodings:
        try:
            return source_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise TextDecodingError(
        f"{source_description}无法严格解码：{path}\n"
        f"已尝试编码：{'、'.join(encodings)}。\n"
        "UTF-16仅在具有明确BOM编码标记时支持。\n"
        "本次不使用替代字符，不生成或发布损坏正文。"
        "请核对文件编码及是否适用于文本处理；"
        "解码成功也不代表已经确认文件是正常文本。"
    )
