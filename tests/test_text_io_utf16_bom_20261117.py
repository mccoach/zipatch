# -*- coding: utf-8 -*-

"""明确UTF-16标记、严格失败及原有解码契约的回归测试。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.text_io import TextDecodingError, decode_text_bytes
from services.merge_service import read_text_content_for_merge


class StrictTextBomTests(unittest.TestCase):
    def decode(self, raw):
        return decode_text_bytes(
            raw, source_description="测试正文", path="sample.log",
        )

    def test_utf16_both_byte_orders_preserve_exact_body(self):
        body = "test ... ok\r\n中文\n尾行\r\n\r\n"
        for bom, encoding in (
            (b"\xff\xfe", "utf-16-le"),
            (b"\xfe\xff", "utf-16-be"),
        ):
            with self.subTest(encoding=encoding):
                text, selected = self.decode(bom + body.encode(encoding))
                self.assertEqual(text, body)
                self.assertEqual(selected, "utf-16")

    def test_bom_only_is_empty_text(self):
        for raw in (b"\xff\xfe", b"\xfe\xff"):
            with self.subTest(raw=raw):
                self.assertEqual(self.decode(raw), ("", "utf-16"))

    def test_damaged_utf16_does_not_fall_back(self):
        for raw in (
            b"\xff\xfeA",
            b"\xfe\xffA",
            b"\xff\xfe\x00\xd8",
            b"\xfe\xff\xd8\x00",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(TextDecodingError) as caught:
                    self.decode(raw)
                self.assertIsInstance(
                    caught.exception.__cause__, UnicodeDecodeError,
                )

    def test_utf32_is_not_misclassified_as_utf16(self):
        for raw in (
            b"\xff\xfe\x00\x00A\x00\x00\x00",
            b"\x00\x00\xfe\xff\x00\x00\x00A",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(TextDecodingError):
                    self.decode(raw)

    def test_existing_utf8_and_chinese_candidates(self):
        body = "中文正文\r\n尾行\n"
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            with self.subTest(encoding=encoding):
                text, _ = self.decode(body.encode(encoding))
                self.assertEqual(text, body)
        self.assertEqual(self.decode(b"")[0], "")

    def test_invalid_bytes_remain_a_strict_failure(self):
        with self.assertRaises(TextDecodingError):
            self.decode(b"\xff")

    def test_merge_reads_utf16_without_changing_source_bytes(self):
        body = "test_example ... ok\r\n\r\nFAILED (failures=1)\r\n"
        raw = b"\xff\xfe" + body.encode("utf-16-le")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tests.log"
            path.write_bytes(raw)
            self.assertEqual(read_text_content_for_merge(path), body)
            self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
