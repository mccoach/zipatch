# -*- coding: utf-8 -*-

"""完整正文换行转换，不负责路径、配置、定位或提交。"""


def normalize_newlines(text, newline):
    if newline not in ("\n", "\r\n"):
        raise ValueError("目标换行必须是 LF 或 CRLF")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if newline == "\n" else normalized.replace("\n", "\r\n")