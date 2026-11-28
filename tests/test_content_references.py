# -*- coding: utf-8 -*-

"""正文引用的严格容器及真实硬链接合并至普通文件拆分回归。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.exclusion_rules import prepare_exclusions
from core.merged_container import MergedContainer
from core.merged_format import (
    CONTENT_REFERENCE_PREFIX, container_marker_lines,
    decode_container_declaration, parse_file_blocks, parse_merge_blocks,
    validate_markers,
)
from services.merge_service import merge_files
from services.restore_service import RestoreTask, prepare_restore


class ContentReferenceTests(unittest.TestCase):
    def container(self, body="original\r\n\n"):
        with MergedContainer("--begin--", "--end--") as container:
            container.add_block("a.py", body)
            container.add_reference("b.py", 1)
            container.add_reference("c.py", 1)
            return "".join(container.text_chunks())

    def test_reference_has_no_code_markers_and_resolves_exact_body(self):
        text = self.container()
        token, start_label, end_label = decode_container_declaration(
            text.splitlines()[0],
        )
        start, end, _ = container_marker_lines(token, start_label, end_label)
        self.assertEqual(text.splitlines().count(start), 1)
        self.assertEqual(text.splitlines().count(end), 1)
        self.assertEqual(text.count(CONTENT_REFERENCE_PREFIX + "1"), 2)
        blocks = parse_merge_blocks(text)
        self.assertEqual(
            [(block.path, block.body, block.reference_index) for block in blocks],
            [
                ("a.py", "original\r\n\n", None),
                ("b.py", "original\r\n\n", 1),
                ("c.py", "original\r\n\n", 1),
            ],
        )
        self.assertEqual(parse_file_blocks(text), [
            ("a.py", "original\r\n\n"),
            ("b.py", "original\r\n\n"),
            ("c.py", "original\r\n\n"),
        ])

    def test_real_empty_body_is_distinct_from_reference_record(self):
        blocks = parse_merge_blocks(self.container(""))
        self.assertEqual([block.body for block in blocks], ["", "", ""])
        self.assertEqual(
            [block.reference_index for block in blocks], [None, 1, 1],
        )

    def test_invalid_reference_number_and_reference_chain_are_rejected(self):
        text = self.container()
        for value in ("0", "-1", "01", "4", "1.0", "true", "9" * 100):
            with self.subTest(value=value):
                changed = text.replace(
                    CONTENT_REFERENCE_PREFIX + "1",
                    CONTENT_REFERENCE_PREFIX + value,
                    1,
                )
                with self.assertRaises(ValueError):
                    parse_merge_blocks(changed)
        changed = text.rsplit(CONTENT_REFERENCE_PREFIX + "1", 1)
        with self.assertRaisesRegex(ValueError, "引用链"):
            parse_merge_blocks(
                changed[0] + CONTENT_REFERENCE_PREFIX + "2" + changed[1],
            )

    def test_writer_rejects_nonexistent_structure_and_reference_targets(self):
        with MergedContainer("--begin--", "--end--") as container:
            with self.assertRaises(ValueError):
                container.add_reference("missing.py", 1)
            container.add_block("folder", "", kind="directory")
            with self.assertRaises(ValueError):
                container.add_reference("a.py", 1)
            container.add_block("b.py", "body")
            container.add_reference("c.py", 2)
            with self.assertRaises(ValueError):
                container.add_reference("d.py", 3)

    def test_reference_prefix_cannot_be_start_marker(self):
        with self.assertRaises(ValueError):
            validate_markers(CONTENT_REFERENCE_PREFIX + "begin", "--end--")

    def test_nested_container_reference_is_not_parsed_as_outer_record(self):
        nested = self.container()
        with MergedContainer("--outer-start--", "--outer-end--") as container:
            container.add_block("nested.txt", nested)
            container.add_reference("nested-copy.txt", 1)
            text = "".join(container.text_chunks())
        blocks = parse_merge_blocks(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual([block.body for block in blocks], [nested, nested])

    def test_truncated_container_still_rejects_reference_records(self):
        text = self.container()
        text = text[:text.rfind("ZIPATCH_MERGED_END:")]
        with self.assertRaises(ValueError):
            parse_merge_blocks(text)


@unittest.skipUnless(os.name == "nt", "正式来源与恢复使用Windows路径观察")
class HardlinkReferenceIntegrationTests(unittest.TestCase):
    def test_merge_reads_one_body_and_split_creates_both_independent_files(self):
        with TemporaryDirectory(prefix="ZipatchReference_") as folder:
            root = Path(folder)
            source = root / "source"
            target = root / "target"
            backup = root / "backup"
            for directory in (source, target, backup):
                directory.mkdir()
            first = source / "a.py"
            second = source / "b.py"
            first.write_bytes(b"original\r\nbody\n")
            os.link(first, second)
            output = root / "merged.txt"
            from services.merge_service import read_text_content_for_merge
            with patch(
                "services.merge_service.read_text_content_for_merge",
                wraps=read_text_content_for_merge,
            ) as read_body:
                result = merge_files(
                    mode="directory", relative_base_folder="",
                    directory_list_text=str(source), list_text="",
                    output_file=str(output),
                    exclusions=prepare_exclusions("", ""),
                    preamble_text="", ending_text="",
                    code_header_line="--begin--", code_footer_line="--end--",
                    record_file_mtime=False,
                    select_source_links=lambda nodes:
                    tuple("authorize" for _ in nodes),
                )
            self.assertEqual(read_body.call_count, 1)
            self.assertEqual(result.total_files, 1)
            self.assertEqual(result.reference_count, 1)
            self.assertEqual(result.record_count, 2)
            blocks = parse_merge_blocks(output.read_bytes().decode("utf-8"))
            self.assertEqual([block.path for block in blocks], [
                str(first), str(second),
            ])
            self.assertEqual(
                [block.reference_index for block in blocks], [None, 1],
            )
            plan = prepare_restore(
                None, str(target), "overwrite",
                source_text=output.read_bytes().decode("utf-8"),
                target_mode="directory", backup_dir=str(backup),
            )
            task = RestoreTask(plan)
            try:
                restored = task.execute()
            finally:
                task.invalidate()
            self.assertTrue(restored.completed)
            destinations = [
                target / path.drive[0].upper() / Path(*path.parts[1:])
                for path in (first, second)
            ]
            self.assertEqual(
                [path.read_bytes() for path in destinations],
                [b"original\r\nbody\n", b"original\r\nbody\n"],
            )
            self.assertNotEqual(
                destinations[0].stat().st_ino,
                destinations[1].stat().st_ino,
            )
            self.assertEqual(first.stat().st_ino, second.stat().st_ino)
            self.assertEqual(first.read_bytes(), b"original\r\nbody\n")

    def test_reference_body_remains_available_when_canonical_target_is_skipped(self):
        with TemporaryDirectory(prefix="ZipatchReferenceSkip_") as folder:
            root = Path(folder)
            target = root / "target"
            backup = root / "backup"
            target.mkdir()
            backup.mkdir()
            (target / "a.py").write_bytes(b"keep existing")
            with MergedContainer("--begin--", "--end--") as container:
                container.add_block("a.py", "container body\r\n")
                container.add_reference("b.py", 1)
                text = "".join(container.text_chunks())
            task = RestoreTask(prepare_restore(
                None, str(target), "skip", source_text=text,
                backup_dir=str(backup), target_mode="directory",
            ))
            try:
                result = task.execute()
            finally:
                task.invalidate()
            self.assertTrue(result.completed)
            self.assertEqual((target / "a.py").read_bytes(), b"keep existing")
            self.assertEqual(
                (target / "b.py").read_bytes(), b"container body\r\n",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)