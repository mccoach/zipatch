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
    """严格解码已有字节；正式业务与只读详情共用失败契约。"""
    if not isinstance(source_bytes, bytes):
        raise TypeError("严格文本解码必须消费不可变bytes")
    encodings = ("utf-8-sig", "utf-8", "gb18030", "gbk")
    for encoding in encodings:
        try:
            return source_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise TextDecodingError(
        f"{source_description}无法严格解码：{path}\n"
        f"已尝试编码：{'、'.join(encodings)}。\n"
        "本次不使用替代字符，不生成或发布损坏正文。"
        "请核对文件编码及是否适用于文本处理；"
        "解码成功也不代表已经确认文件是正常文本。"
    )
