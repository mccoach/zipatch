# -*- coding: utf-8 -*-

"""已获取正文的暂存、动态标识避撞及声明式容器生成。

不读取来源文件、不解析嵌套容器、不发布成果。
正文暂存超过内存阈值后转磁盘；退出时关闭自身暂存。
"""

import secrets
from tempfile import SpooledTemporaryFile

from core.constants import FILE_SECTION_START_TEMPLATE
from core.merged_format import (
    MTIME_PREFIX, STRUCTURAL_NEWLINE,
    container_marker_lines, encode_container_declaration,
    encode_source_line, encode_structure_line, validate_markers,
)


SPOOL_MEMORY_BYTES = 1024 * 1024
READ_CHARACTERS = 64 * 1024


class MergedContainer:
    """一次收集、一份正文暂存；封装标识固定后不再接受新材料。"""

    def __init__(self, start_label, end_label, *, preamble="", ending=""):
        validate_markers(start_label, end_label)
        if type(preamble) is not str or type(ending) is not str:
            raise TypeError("容器附加文本必须是字符串")
        self.start_label = start_label
        self.end_label = end_label
        self.preamble = preamble
        self.ending = ending
        self._stream = SpooledTemporaryFile(
            max_size=SPOOL_MEMORY_BYTES,
            mode="w+", encoding="utf-8", newline="",
        )
        self._records = []
        self._token = None
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        self._closed = True
        self._stream.close()

    def add_block(
        self, path, body, *, kind="file", specification=None, mtime=None,
        origin_path=None,
    ):
        if self._closed or self._token is not None:
            raise ValueError("容器已经关闭或固定标识，不能追加材料")
        if (
            type(path) is not str or not path
            or any(ord(character) < 32 for character in path)
            or type(body) is not str
        ):
            raise ValueError("容器块必须具有有效完整路径和字符串正文")
        if mtime is not None and type(mtime) is not str:
            raise TypeError("容器文件时间必须是已准备的字符串")
        structure = None
        if kind != "file":
            if body != "" or mtime is not None:
                raise ValueError("结构块不得携带正文或文件时间")
            structure = encode_structure_line(
                kind, specification, entry_path=path,
            )
        elif specification is not None:
            raise ValueError("普通文件不得携带链接定义")
        offset = self._stream.tell()
        written = self._stream.write(body)
        if written != len(body):
            raise OSError("容器正文暂存没有完整写入")
        source = None if origin_path is None else encode_source_line(origin_path)
        self._records.append((path, structure, mtime, offset, len(body), source))

    def _contains_token(self, token):
        if any(token in text for text in (
            self.start_label, self.end_label, self.preamble, self.ending,
        )):
            return True
        for path, structure, mtime, _, _, source in self._records:
            if any(token in text for text in (
                path, structure or "", mtime or "", source or "",
            )):
                return True
        self._stream.flush()
        self._stream.seek(0)
        tail = ""
        while True:
            chunk = self._stream.read(READ_CHARACTERS)
            if not chunk:
                return False
            combined = tail + chunk
            if token in combined:
                return True
            tail = combined[-(len(token) - 1):]

    def _fix_token(self):
        if self._closed:
            raise ValueError("容器已经关闭")
        if self._token is None:
            while True:
                alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                combinations = len(alphabet) ** 2
                first = secrets.randbelow(combinations)
                for offset in range(combinations):
                    number = (first + offset) % combinations
                    candidate = (
                        alphabet[number // len(alphabet)]
                        + alphabet[number % len(alphabet)]
                    )
                    # 两位标识仍须避让全部正文、路径、结构和附加文本；
                    # 随机只决定搜索起点，不代替完整避撞。
                    container_marker_lines(
                        candidate, self.start_label, self.end_label,
                    )
                    if not self._contains_token(candidate):
                        self._token = candidate
                        break
                if self._token is None:
                    raise ValueError(
                        "全部3844种两位容器标识均与内容冲突；"
                        "本次未取得安全边界，请缩小合并范围后重新生成"
                    )
                break
        return self._token

    def text_chunks(self):
        """逐块输出原正文；每块额外分隔不抵扣原来的尾换行。"""
        token = self._fix_token()
        start, end, container_end = container_marker_lines(
            token, self.start_label, self.end_label,
        )
        yield encode_container_declaration(
            token, self.start_label, self.end_label,
        ) + STRUCTURAL_NEWLINE
        yield self.preamble
        for number, (path, structure, mtime, offset, length, source) in enumerate(
            self._records, 1,
        ):
            yield (
                FILE_SECTION_START_TEMPLATE.format(index=number)
                + token + STRUCTURAL_NEWLINE
            )
            yield path + STRUCTURAL_NEWLINE
            if source is not None:
                yield source + STRUCTURAL_NEWLINE
            if structure is not None:
                yield structure + STRUCTURAL_NEWLINE
            elif mtime is not None:
                yield MTIME_PREFIX + mtime + STRUCTURAL_NEWLINE
            yield start + STRUCTURAL_NEWLINE
            self._stream.seek(offset)
            remaining = length
            while remaining:
                chunk = self._stream.read(min(remaining, READ_CHARACTERS))
                if not chunk:
                    raise OSError("容器正文暂存提前结束")
                remaining -= len(chunk)
                yield chunk
            yield STRUCTURAL_NEWLINE + end + STRUCTURAL_NEWLINE
        yield self.ending
        yield container_end + STRUCTURAL_NEWLINE