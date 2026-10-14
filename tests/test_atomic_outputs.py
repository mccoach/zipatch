# -*- coding: utf-8 -*-

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.atomic_file import atomic_text_writer
from core.exclusion_rules import prepare_exclusions
from services.merge_service import merge_demand_files, merge_project_files
from services.scan_service import panoramic_scan


class FailingTemporaryStream:
    """以真实临时文件注入具体 I/O 故障，不替换正式业务生成流程。"""

    def __init__(self, stream, stage, error):
        self.stream = stream
        self.stage = stage
        self.error = error

    @property
    def name(self):
        return self.stream.name

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        self.stream.close()
        if self.stage == "close" and error_type is None:
            raise self.error
        return False

    def write(self, content):
        if self.stage == "write":
            self.stream.write(content[:1])
            raise self.error
        return self.stream.write(content)

    def flush(self):
        if self.stage == "flush":
            raise self.error
        return self.stream.flush()

    def fileno(self):
        return self.stream.fileno()


class AtomicTextWriterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.target = self.folder / "result.txt"

    def temporary_files(self):
        return list(self.folder.glob(".result.txt.*.tmp"))

    def assert_target_state(self, existing):
        if existing:
            self.assertEqual(self.target.read_bytes(), b"OLD")
        else:
            self.assertFalse(self.target.exists())
        self.assertEqual(self.temporary_files(), [])

    def test_old_target_remains_visible_until_successful_commit(self):
        self.target.write_bytes(b"OLD")
        with atomic_text_writer(self.target) as stream:
            temporary = Path(stream.name)
            self.assertEqual(temporary.parent, self.target.parent)
            self.assertNotEqual(temporary, self.target)
            stream.write("NEW\n")
            self.assertEqual(self.target.read_bytes(), b"OLD")
        self.assertEqual(self.target.read_bytes(), b"NEW" + os.linesep.encode())
        self.assertFalse(temporary.exists())
        self.assertEqual(self.temporary_files(), [])

    def test_new_target_is_not_published_during_generation(self):
        with atomic_text_writer(self.target) as stream:
            stream.write("NEW")
            self.assertFalse(self.target.exists())
        self.assertEqual(self.target.read_bytes(), b"NEW")

    def test_unique_names_do_not_overwrite_unrelated_temporary_file(self):
        unrelated = self.folder / ".result.txt.reserved.tmp"
        unrelated.write_bytes(b"UNRELATED")
        with atomic_text_writer(self.target) as first:
            first_path = Path(first.name)
            first.write("FIRST")
        with atomic_text_writer(self.target) as second:
            second_path = Path(second.name)
            second.write("SECOND")
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(unrelated.read_bytes(), b"UNRELATED")
        self.assertEqual(self.target.read_bytes(), b"SECOND")

    def test_generation_failure_preserves_existing_or_absent_target(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.target.unlink(missing_ok=True)
                if existing:
                    self.target.write_bytes(b"OLD")
                with self.assertRaisesRegex(OSError, "generation failed"):
                    with atomic_text_writer(self.target) as stream:
                        stream.write("PARTIAL")
                        raise OSError("generation failed")
                self.assert_target_state(existing)

    def verify_stream_failure(self, stage):
        original_factory = tempfile.NamedTemporaryFile
        for existing in (False, True):
            with self.subTest(stage=stage, existing=existing):
                self.target.unlink(missing_ok=True)
                if existing:
                    self.target.write_bytes(b"OLD")
                original_error = OSError(f"{stage} failed")

                def create_stream(**kwargs):
                    return FailingTemporaryStream(
                        original_factory(**kwargs), stage, original_error,
                    )

                with (
                    patch(
                        "core.atomic_file.NamedTemporaryFile",
                        side_effect=create_stream,
                    ),
                    patch("core.atomic_file.os.replace") as replace,
                ):
                    with self.assertRaises(OSError) as caught:
                        with atomic_text_writer(self.target) as stream:
                            stream.write("NEW")
                self.assertIs(caught.exception, original_error)
                replace.assert_not_called()
                self.assert_target_state(existing)

    def test_write_failure_does_not_publish_partial_content(self):
        self.verify_stream_failure("write")

    def test_flush_failure_does_not_publish_content(self):
        self.verify_stream_failure("flush")

    def test_close_failure_does_not_publish_content(self):
        self.verify_stream_failure("close")

    def test_temporary_creation_failure_preserves_old_target(self):
        self.target.write_bytes(b"OLD")
        with patch(
            "core.atomic_file.NamedTemporaryFile",
            side_effect=PermissionError("temporary creation denied"),
        ):
            with self.assertRaises(PermissionError):
                with atomic_text_writer(self.target):
                    self.fail("generation must not start")
        self.assert_target_state(True)

    def test_keyboard_interrupt_does_not_publish_partial_content(self):
        self.target.write_bytes(b"OLD")
        with self.assertRaises(KeyboardInterrupt):
            with atomic_text_writer(self.target) as stream:
                stream.write("PARTIAL")
                raise KeyboardInterrupt()
        self.assert_target_state(True)

    def test_fsync_failure_preserves_existing_or_absent_target(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.target.unlink(missing_ok=True)
                if existing:
                    self.target.write_bytes(b"OLD")
                with patch(
                    "core.atomic_file.os.fsync",
                    side_effect=OSError("sync failed"),
                ):
                    with self.assertRaisesRegex(OSError, "sync failed"):
                        with atomic_text_writer(self.target) as stream:
                            stream.write("NEW")
                self.assert_target_state(existing)

    def test_replace_failure_preserves_existing_or_absent_target(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.target.unlink(missing_ok=True)
                if existing:
                    self.target.write_bytes(b"OLD")
                with patch(
                    "core.atomic_file.os.replace",
                    side_effect=PermissionError("replace denied"),
                ):
                    with self.assertRaisesRegex(PermissionError, "replace denied"):
                        with atomic_text_writer(self.target) as stream:
                            stream.write("NEW")
                self.assert_target_state(existing)

    def test_close_precedes_replace_and_replace_occurs_once(self):
        original_replace = os.replace
        observed = []

        def replace(source, destination):
            self.assertTrue(stream.closed)
            observed.append((Path(source), Path(destination)))
            return original_replace(source, destination)

        with patch("core.atomic_file.os.replace", side_effect=replace):
            with atomic_text_writer(self.target) as stream:
                stream.write("NEW")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0].parent, self.target.parent)
        self.assertEqual(observed[0][1], self.target)

    def test_cleanup_failure_does_not_hide_original_exception(self):
        self.target.write_bytes(b"OLD")
        original_error = PermissionError("replace denied")
        with (
            patch("core.atomic_file.os.replace", side_effect=original_error),
            patch(
                "core.atomic_file.Path.unlink",
                side_effect=PermissionError("cleanup denied"),
            ),
            patch("core.atomic_file.sys.stderr") as stderr,
        ):
            with self.assertRaises(PermissionError) as caught:
                with atomic_text_writer(self.target) as stream:
                    stream.write("NEW")
        self.assertIs(caught.exception, original_error)
        self.assertEqual(self.target.read_bytes(), b"OLD")
        reported = "".join(
            call.args[0] for call in stderr.write.call_args_list
        )
        self.assertIn("cleanup denied", reported)
        for temporary in self.temporary_files():
            temporary.unlink()

    def test_target_directory_is_rejected_before_generation(self):
        self.target.mkdir()
        child = self.target / "keep.txt"
        child.write_bytes(b"KEEP")
        with self.assertRaises(IsADirectoryError):
            with atomic_text_writer(self.target):
                self.fail("generation must not start")
        self.assertTrue(self.target.is_dir())
        self.assertEqual(child.read_bytes(), b"KEEP")
        self.assertEqual(self.temporary_files(), [])

    def test_missing_parent_is_created(self):
        target = self.folder / "nested" / "result.txt"
        with atomic_text_writer(target) as stream:
            stream.write("NEW")
        self.assertEqual(target.read_bytes(), b"NEW")

    def test_existing_basic_permissions_are_preserved(self):
        self.target.write_bytes(b"OLD")
        requested_mode = 0o640 if os.name != "nt" else stat.S_IREAD | stat.S_IWRITE
        self.target.chmod(requested_mode)
        before = stat.S_IMODE(self.target.stat().st_mode)
        with atomic_text_writer(self.target) as stream:
            stream.write("NEW")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), before)
        self.assertEqual(self.target.read_bytes(), b"NEW")

    def test_permission_preparation_failure_preserves_old_target(self):
        self.target.write_bytes(b"OLD")
        with patch(
            "core.atomic_file.os.chmod",
            side_effect=PermissionError("permission preparation denied"),
        ):
            with self.assertRaises(PermissionError):
                with atomic_text_writer(self.target) as stream:
                    stream.write("NEW")
        self.assert_target_state(True)

    def test_windows_readonly_target_is_not_overwritten(self):
        if os.name != "nt":
            self.skipTest("Windows 只读属性须在 Windows 验证")
        self.target.write_bytes(b"OLD")
        self.target.chmod(stat.S_IREAD)
        try:
            with self.assertRaises(PermissionError):
                with atomic_text_writer(self.target):
                    self.fail("generation must not start")
            self.assert_target_state(True)
        finally:
            self.target.chmod(stat.S_IREAD | stat.S_IWRITE)


class AtomicBusinessOutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.source = self.folder / "project"
        self.source.mkdir()
        self.first = self.source / "a.py"
        self.second = self.source / "b.py"
        self.first.write_text("A", encoding="utf-8")
        self.second.write_text("B", encoding="utf-8")
        self.output = self.folder / "result.txt"

    def run_business(self, mode, log_func=None, preamble=""):
        if mode == "scan":
            return panoramic_scan(
                self.source, self.output, prepare_exclusions("", ""),
                preamble, "ENDING", log_func=log_func,
            )
        arguments = {
            "output_file": self.output,
            "preamble_text": preamble,
            "ending_text": "ENDING",
            "code_header_line": "HEADER",
            "code_footer_line": "FOOTER",
            "log_func": log_func,
        }
        if mode == "regular":
            return merge_project_files(
                source_folder=self.source,
                exclusions=prepare_exclusions("", ""),
                **arguments,
            )
        return merge_demand_files(
            file_paths=(str(self.second), str(self.first)),
            **arguments,
        )

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.folder.glob(".result.txt.*.tmp")), [])

    def test_three_businesses_preserve_format_order_and_counts(self):
        for mode in ("scan", "regular", "demand"):
            with self.subTest(mode=mode):
                self.output.write_bytes(b"OLD")
                with (
                    patch(
                        "services.scan_service.current_timestamp_text",
                        return_value="STAMP",
                    ),
                    patch(
                        "services.merge_service.current_timestamp_text",
                        return_value="STAMP",
                    ),
                ):
                    result = self.run_business(mode, preamble="PREAMBLE\n")
                if mode == "scan":
                    expected = (
                        "STAMP\n\nPREAMBLE\n\n"
                        "统计：文件夹总数: 0，文件总数: 2\n\n"
                        f"1. {self.first}\n2. {self.second}\n\nENDING\n"
                    )
                    self.assertEqual(result.file_count, 2)
                    self.assertEqual(result.dir_count, 0)
                else:
                    ordered = (
                        (self.first, "A"), (self.second, "B")
                    ) if mode == "regular" else (
                        (self.second, "B"), (self.first, "A")
                    )
                    expected = "STAMP\n\nPREAMBLE\n\n"
                    for number, (path, content) in enumerate(ordered, 1):
                        expected += (
                            f"---第{number}个文件---\n{path}\n"
                            f"HEADER\n{content}\nFOOTER\n"
                        )
                    expected += "\nENDING\n"
                    self.assertEqual(result.total_files, 2)
                self.assertEqual(
                    self.output.read_bytes(),
                    expected.replace("\n", os.linesep).encode("utf-8"),
                )
                self.assertEqual(result.output_file, str(self.output.resolve()))
                self.assert_no_temporary_files()

    def test_three_businesses_propagate_commit_failure_without_success_log(self):
        for mode in ("scan", "regular", "demand"):
            with self.subTest(mode=mode):
                self.output.write_bytes(b"OLD")
                logs = []
                with patch(
                    "core.atomic_file.os.replace",
                    side_effect=PermissionError("replace denied"),
                ):
                    with self.assertRaises(PermissionError):
                        self.run_business(mode, log_func=logs.append)
                self.assertEqual(self.output.read_bytes(), b"OLD")
                self.assertFalse(any("完成：" in message for message in logs))
                self.assert_no_temporary_files()

    def test_three_businesses_reject_encoding_loss_and_preserve_old_output(self):
        for mode in ("scan", "regular", "demand"):
            with self.subTest(mode=mode):
                self.output.write_bytes(b"OLD")
                with self.assertRaises(UnicodeEncodeError):
                    self.run_business(mode, preamble="\ud800")
                self.assertEqual(self.output.read_bytes(), b"OLD")
                self.assert_no_temporary_files()

    def test_three_businesses_keep_old_output_during_final_commit(self):
        original_replace = os.replace
        for mode in ("scan", "regular", "demand"):
            with self.subTest(mode=mode):
                self.output.write_bytes(b"OLD")
                observed = []

                def commit(source, target):
                    self.assertEqual(self.output.read_bytes(), b"OLD")
                    prepared = Path(source).read_text(encoding="utf-8")
                    self.assertTrue(prepared.endswith("ENDING\n"))
                    observed.append(prepared)
                    return original_replace(source, target)

                with patch("core.atomic_file.os.replace", side_effect=commit):
                    self.run_business(mode)
                self.assertEqual(len(observed), 1)
                self.assert_no_temporary_files()

    def test_demand_late_source_read_failure_preserves_old_output(self):
        self.output.write_bytes(b"OLD")
        original_reader = Path.read_bytes

        def read_source(path):
            if path == self.first:
                raise PermissionError("late source denied")
            return original_reader(path)

        with patch("core.text_io.Path.read_bytes", new=read_source):
            with self.assertRaisesRegex(OSError, "late source denied"):
                self.run_business("demand")
        self.assertEqual(self.output.read_bytes(), b"OLD")
        self.assert_no_temporary_files()

    def test_regular_read_failure_hint_remains_a_completed_output(self):
        self.output.write_bytes(b"OLD")
        original_reader = Path.read_bytes

        def read_source(path):
            if path == self.second:
                raise PermissionError("source denied")
            return original_reader(path)

        with patch("core.text_io.Path.read_bytes", new=read_source):
            result = self.run_business("regular")
        text = self.output.read_text(encoding="utf-8")
        self.assertEqual(result.total_files, 2)
        self.assertIn("*** 读取文件时发生错误: source denied", text)
        self.assertTrue(text.endswith("ENDING\n"))
        self.assert_no_temporary_files()

    def test_scan_late_metadata_failure_preserves_old_output(self):
        self.output.write_bytes(b"OLD")
        original_stat = os.stat

        def query_attributes(path, *args, **kwargs):
            if os.fspath(path) == str(self.second):
                raise PermissionError("metadata denied")
            return original_stat(path, *args, **kwargs)

        with patch("services.scan_service.os.stat", side_effect=query_attributes):
            with self.assertRaisesRegex(PermissionError, "metadata denied"):
                panoramic_scan(
                    self.source, self.output, prepare_exclusions("", ""),
                    "", "", include_size=True, include_date=True,
                )
        self.assertEqual(self.output.read_bytes(), b"OLD")
        self.assert_no_temporary_files()

    def test_scan_and_regular_do_not_select_current_temporary_output(self):
        for mode in ("scan", "regular"):
            with self.subTest(mode=mode):
                self.output = self.source / "result.txt"
                self.output.write_bytes(b"OLD")
                result = self.run_business(mode)
                text = self.output.read_text(encoding="utf-8")
                self.assertNotIn(str(self.output), text)
                self.assertNotIn(".result.txt.", text)
                count = result.file_count if mode == "scan" else result.total_files
                self.assertEqual(count, 2)
                self.assertEqual(
                    list(self.source.glob(".result.txt.*.tmp")), [],
                )

    def test_demand_output_selection_is_rejected_before_staging(self):
        self.output.write_bytes(b"OLD")
        with patch("core.atomic_file.NamedTemporaryFile") as temporary:
            with self.assertRaisesRegex(ValueError, "输出文件被实际选中"):
                merge_demand_files(
                    (str(self.output),), self.output,
                    "", "", "HEADER", "FOOTER",
                )
        temporary.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"OLD")


if __name__ == "__main__":
    unittest.main()
