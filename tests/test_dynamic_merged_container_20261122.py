# -*- coding: utf-8 -*-

"""声明位置、嵌套隔离、自动避撞、正文尾部及正式合并回归。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from core.merged_container import MergedContainer
from core.merged_format import (
    CONTAINER_PREFIX, decode_container_declaration,
    parse_merge_blocks,
)
from services.merge_service import merge_files


class DynamicMergedContainerTests(unittest.TestCase):
    def container(self, body, *, preamble="", ending="", labels=None):
        start, end = labels or (CODE_HEADER_LINE, CODE_FOOTER_LINE)
        with MergedContainer(
            start, end, preamble=preamble, ending=ending,
        ) as container:
            container.add_block("source.py", body)
            return "".join(container.text_chunks())

    def test_exact_body_and_all_tail_newline_cases(self):
        for body in ("", "body", "body\n", "body\r", "body\r\n", "body\n\n", "😀\r\n尾\r"):
            with self.subTest(body=body):
                text = self.container(body)
                self.assertTrue(text.startswith(CONTAINER_PREFIX))
                blocks = parse_merge_blocks(text)
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0].body, body)

    def test_nested_containers_and_old_markers_are_only_body(self):
        inner = self.container("inner\r\n\r\n", labels=("内层开始", "内层结束"))
        body = (
            "---第1个文件---\r\nfake.py\r\n"
            "---以下是源代码---\r\nold body\r\n---源代码结束---\r\n"
            + inner
        )
        outer = self.container(body, labels=("外层开始", "外层结束"))
        blocks = parse_merge_blocks(outer)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].path, "source.py")
        self.assertEqual(blocks[0].body, body)
        third = self.container(outer)
        self.assertEqual(parse_merge_blocks(third)[0].body, outer)

    def test_token_collision_regenerates_without_modifying_body(self):
        first = "a" * 32
        second = "b" * 32
        body = "源码\n---源代码结束--- [" + first + "]\n尾"
        with patch(
            "core.merged_container.secrets.token_hex",
            side_effect=(first, second),
        ) as random:
            text = self.container(body)
        self.assertEqual(random.call_count, 2)
        token, _, _ = decode_container_declaration(text.split("\r\n", 1)[0])
        self.assertEqual(token, second)
        self.assertEqual(parse_merge_blocks(text)[0].body, body)

    def test_collision_in_extra_text_and_chunk_boundary_is_checked(self):
        first = "c" * 32
        second = "d" * 32
        body = "x" * (64 * 1024 - 7) + first + "tail"
        with patch(
            "core.merged_container.secrets.token_hex",
            side_effect=(first, second),
        ):
            text = self.container(body)
        self.assertEqual(parse_merge_blocks(text)[0].body, body)
        with patch(
            "core.merged_container.secrets.token_hex",
            side_effect=(first, second),
        ):
            text = self.container("body", preamble=first + "\r\n")
        self.assertEqual(
            decode_container_declaration(text.split("\r\n", 1)[0])[0],
            second,
        )

    def test_preamble_fake_sections_and_nested_declarations_are_not_blocks(self):
        fake = (
            "---第1个文件---\r\nfake.py\r\n"
            + self.container("nested")
        )
        text = self.container("real", preamble=fake, ending=fake)
        blocks = parse_merge_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].body, "real")

    def test_missing_footer_truncation_and_appended_second_package_are_rejected(self):
        text = self.container("body")
        without_end = text[:text.rfind("ZIPATCH_MERGED_END:")]
        for bad in (
            without_end,
            text[:text.index(CODE_FOOTER_LINE + " [", text.index("source.py"))],
            text + self.container("second"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_merge_blocks(bad)

    def test_declaration_must_be_first_line_and_strict(self):
        text = self.container("body")
        declaration, rest = text.split("\r\n", 1)
        for bad in (
            "\r\n" + text,
            "说明\r\n" + text,
            declaration.replace('"version":1', '"version":2') + "\r\n" + rest,
            declaration.replace('"version":1', '"version":1,"version":1') + "\r\n" + rest,
            declaration[:-1] + ',"extra":0}\r\n' + rest,
            "---第1个文件---\nsource.py\n---以下是源代码---\nbody\n---源代码结束---",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_merge_blocks(bad)

    def test_spooled_body_survives_disk_rollover(self):
        body = ("中文😀\r\n尾\n" * 120000) + "no-final-newline"
        with MergedContainer(CODE_HEADER_LINE, CODE_FOOTER_LINE) as container:
            container.add_block("large.py", body)
            text = "".join(container.text_chunks())
        self.assertEqual(parse_merge_blocks(text)[0].body, body)

    def test_writer_closes_spool_on_failure(self):
        container = MergedContainer(CODE_HEADER_LINE, CODE_FOOTER_LINE)
        with self.assertRaises(ValueError):
            with container:
                container.add_block("source.py", "body")
                raise ValueError("injected")
        self.assertTrue(container._stream.closed)


class DynamicMergedContainerServiceTests(unittest.TestCase):
    def test_formal_list_merge_remerges_previous_container_as_exact_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            first = source / "first.py"
            body = "正文\r\n---源代码结束---\n\n"
            first.write_bytes(body.encode("utf-8"))
            inner_path = root / "inner.txt"
            outer_path = root / "outer.txt"
            arguments = dict(
                mode="list", source_folder=str(source),
                exclusions=prepare_exclusions("", ""),
                preamble_text="", ending_text="",
                code_header_line=CODE_HEADER_LINE,
                code_footer_line=CODE_FOOTER_LINE,
                record_file_mtime=False,
            )
            merge_files(
                list_text=str(first), output_file=str(inner_path),
                **arguments,
            )
            with inner_path.open("r", encoding="utf-8", newline="") as stream:
                inner = stream.read()
            self.assertEqual(parse_merge_blocks(inner)[0].body, body)
            merge_files(
                list_text=str(inner_path), output_file=str(outer_path),
                **arguments,
            )
            with outer_path.open("r", encoding="utf-8", newline="") as stream:
                outer = stream.read()
            self.assertEqual(parse_merge_blocks(outer)[0].body, inner)
            self.assertEqual(first.read_bytes(), body.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()