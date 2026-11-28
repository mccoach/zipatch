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
    encode_content_reference, encode_source_line, encode_structure_line,
    validate_markers,
)


SPOOL_MEMORY_BYTES = 1024 * 1024
READ_CHARACTERS = 64 * 1024
TOKEN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


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

    def _validate_append_path(self, path):
        if self._closed or self._token is not None:
            raise ValueError("容器已经关闭或固定标识，不能追加材料")
        if (
            type(path) is not str or not path
            or any(ord(character) < 32 for character in path)
        ):
            raise ValueError("容器记录必须具有有效完整路径")

    def add_block(
        self, path, body, *, kind="file", specification=None, mtime=None,
        origin_path=None,
    ):
        self._validate_append_path(path)
        if type(body) is not str:
            raise TypeError("容器正文必须是字符串")
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
        self._records.append((
            path, structure, mtime, offset, len(body), source, None,
        ))

    def add_reference(self, path, reference_index):
        """保留一个正式文件路径，不生成正文暂存或空代码块。"""
        self._validate_append_path(path)
        reference = encode_content_reference(reference_index)
        if reference_index > len(self._records):
            raise ValueError("正文引用目标不存在或不是此前记录")
        target = self._records[reference_index - 1]
        if target[1] is not None or target[6] is not None:
            raise ValueError("正文引用必须直接指向真实文件正文")
        self._records.append((
            path, None, None, None, None, None, reference,
        ))

    def _used_tokens(self):
        """一次收集全部两位冲突，不为每个候选重新扫描正文。

        独立说明字段分别检查；暂存流按原有连续布局检查，
        包括读取块边界及相邻正文记录之间的组合。
        不重新获取来源，不将不同说明字段拼成新的冲突。
        """
        used = set()
        alphabet = frozenset(TOKEN_ALPHABET)

        def collect(text, previous=""):
            for character in text:
                if previous in alphabet and character in alphabet:
                    used.add(previous + character)
                previous = character
            return previous

        for text in (
            self.start_label, self.end_label, self.preamble, self.ending,
        ):
            collect(text)
        for path, structure, mtime, _, _, source, reference in self._records:
            for text in (
                path, structure or "", mtime or "", source or "",
                reference or "",
            ):
                collect(text)

        self._stream.flush()
        self._stream.seek(0)
        previous = ""
        while True:
            chunk = self._stream.read(READ_CHARACTERS)
            if not chunk:
                return used
            previous = collect(chunk, previous)

    def _fix_token(self):
        if self._closed:
            raise ValueError("容器已经关闭")
        if self._token is not None:
            return self._token

        used = self._used_tokens()
        size = len(TOKEN_ALPHABET)
        combinations = size * size
        first = secrets.randbelow(combinations)
        for offset in range(combinations):
            number = (first + offset) % combinations
            candidate = (
                TOKEN_ALPHABET[number // size]
                + TOKEN_ALPHABET[number % size]
            )
            if candidate not in used:
                container_marker_lines(
                    candidate, self.start_label, self.end_label,
                )
                self._token = candidate
                return candidate
        raise ValueError(
            "全部3844种两位容器标识均与内容冲突；"
            "本次未取得安全边界，请缩小合并范围后重新生成"
        )

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
        for number, (
            path, structure, mtime, offset, length, source, reference,
        ) in enumerate(self._records, 1):
            yield (
                FILE_SECTION_START_TEMPLATE.format(index=number)
                + token + STRUCTURAL_NEWLINE
            )
            yield path + STRUCTURAL_NEWLINE
            if reference is not None:
                yield reference + STRUCTURAL_NEWLINE
                continue
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