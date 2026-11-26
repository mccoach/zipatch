# -*- coding: utf-8 -*-

"""来源节点通断、后台边界、容器路径及正式扫描合并拆分回归。

真实文件系统操作仅发生在临时目录。生产发布及恢复不mock。
Windows软链接权限不足明确跳过，不登记该场景已验证。
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.merged_container import MergedContainer
from core.merged_format import (
    SOURCE_PREFIX, encode_source_line, parse_merge_blocks, validate_source_line,
)
from services.demand_rules import inspect_demand_rules
from services.merge_service import merge_files
from services.scan_service import panoramic_scan
from services.source_traversal import SourceTraversal, SourceSelectionCancelled


class SourceContainerTests(unittest.TestCase):
    def test_origin_is_ignored_and_body_newlines_are_exact(self):
        bodies = ("", "text", "text\n", "text\r\n\r\n", "a\rb")
        with MergedContainer("--begin--", "--end--") as container:
            for number, body in enumerate(bodies):
                container.add_block(
                    f"nominal/file{number}.txt", body,
                    origin_path=f"actual/file{number}.txt",
                )
            text = "".join(container.text_chunks())
        blocks = parse_merge_blocks(text)
        self.assertEqual(
            [(block.path, block.body) for block in blocks],
            [(f"nominal/file{number}.txt", body)
             for number, body in enumerate(bodies)],
        )
        self.assertEqual(text.count(SOURCE_PREFIX), len(bodies))

    def test_actual_path_line_is_plain_text_and_not_body_metadata(self):
        path = "E:\\资料\\file [1].txt"
        self.assertEqual(encode_source_line(path), "实际路径：" + path)
        validate_source_line("实际路径：" + path)
        for line in (
            "实际路径：",
            "实际路径：a\nb",
            "实际路径：a\tb",
            "其他路径：a",
        ):
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    validate_source_line(line)

        body = "实际路径：这是源码正文，不是容器说明\n"
        with MergedContainer("--begin--", "--end--") as container:
            container.add_block("nominal.txt", body, origin_path=path)
            text = "".join(container.text_chunks())
        blocks = parse_merge_blocks(text)
        self.assertEqual(
            [(block.path, block.body) for block in blocks],
            [("nominal.txt", body)],
        )
        self.assertIn("实际路径：" + path, text)


@unittest.skipUnless(os.name == "nt", "真实来源链接测试使用Windows")
class WindowsSourceNodeTests(unittest.TestCase):
    def symlink(self, path, target, *, directory):
        try:
            os.symlink(str(target), path, target_is_directory=directory)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("缺少Windows软链接创建权限，本场景未验证")
            raise

    def fixture(self, root, *, junction=False):
        source = root / "source"
        actual = root / "actual"
        inner = root / "inner"
        for directory in (source, actual, inner):
            directory.mkdir()
        (inner / "empty").mkdir()
        payload = root / "payload.txt"
        payload.write_bytes(b"original\r\nbody\n")
        file_link = inner / "code.txt"
        self.symlink(file_link, payload, directory=False)
        second = actual / "nested"
        self.symlink(second, inner, directory=True)
        first = source / "linked"
        if junction:
            from core.filesystem_changes import _create_junction_entry
            _create_junction_entry(first, actual, "\\\\?\\" + str(actual))
        else:
            self.symlink(first, actual, directory=True)
        return source, actual, inner, payload, first, second, file_link

    def merge(self, source, output, selector, *, mode="directory", text=""):
        return merge_files(
            mode=mode, relative_base_folder=str(source),
            directory_list_text=str(source), list_text=text,
            output_file=str(output), exclusions=prepare_exclusions("", ""),
            preamble_text="", ending_text="",
            code_header_line="--begin--", code_footer_line="--end--",
            record_file_mtime=False, select_source_links=selector,
        )

    def test_all_nodes_merge_nominal_body_and_final_origin(self):
        for junction in (False, True):
            with self.subTest(junction=junction):
                with TemporaryDirectory(prefix="Zipatch_nodes_all_") as folder:
                    root = Path(folder)
                    source, actual, inner, payload, first, second, final = (
                        self.fixture(root, junction=junction)
                    )
                    decisions = []

                    def select(nodes):
                        decisions.extend(node.definition.entry_path for node in nodes)
                        return tuple("authorize" for node in nodes)

                    output = root / "merged.txt"
                    result = self.merge(source, output, select)
                    text = output.read_bytes().decode("utf-8")
                    blocks = parse_merge_blocks(text)
                    nominal = first / "nested" / "code.txt"
                    files = [block for block in blocks if block.kind == "file"]
                    self.assertEqual(
                        [(block.path, block.body) for block in files],
                        [(str(nominal), "original\r\nbody\n")],
                    )
                    self.assertIn(encode_source_line(str(payload)), text)
                    self.assertTrue(all(block.kind == "file" for block in blocks))
                    self.assertNotIn(
                        str(first / "nested" / "empty"),
                        [block.path for block in blocks],
                    )
                    self.assertEqual(len(decisions), 3)
                    self.assertEqual(len(set(decisions)), 3)
                    self.assertEqual(sum(dict(result.link_counts).values()), 3)
                    self.assertEqual(dict(result.entry_counts)["file"], 1)
                    self.assertEqual(payload.read_bytes(), b"original\r\nbody\n")

    def test_outer_denial_prescans_definitions_but_never_reads_body(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_outer_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            original_readlink = os.readlink
            original_open = Path.open
            visited, calls = [], []

            def readlink(path, *args, **kwargs):
                visited.append(Path(path))
                return original_readlink(path, *args, **kwargs)

            def guarded(path, *args, **kwargs):
                if Path(path) == payload:
                    raise AssertionError("未选路线不得读取正文")
                return original_open(path, *args, **kwargs)

            def select(nodes):
                calls.append(nodes)
                return tuple("skip" for node in nodes)

            output = root / "merged.txt"
            with (
                patch("core.link_paths.os.readlink", side_effect=readlink),
                patch.object(Path, "open", guarded),
            ):
                result = self.merge(source, output, select)
            text = output.read_bytes().decode("utf-8")
            self.assertEqual(parse_merge_blocks(text), [])
            self.assertIn(str(first), text)
            self.assertNotIn(str(first / "nested"), text)
            self.assertTrue({first, second, final}.issubset(visited))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(calls[0]), 3)
            self.assertEqual(result.total_files, 0)

    def test_inner_denial_cancels_descendants_after_one_complete_scan(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_inner_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            selected = []

            def select(nodes):
                selected.extend(node.definition.entry_path for node in nodes)
                values = []
                for node in nodes:
                    parent_enabled = (
                        node.parent_index is None
                        or values[node.parent_index] == "authorize"
                    )
                    values.append(
                        "authorize"
                        if parent_enabled and node.definition.entry_path != second
                        else "skip"
                    )
                return tuple(values)

            output = root / "merged.txt"
            self.merge(source, output, select)
            text = output.read_bytes().decode("utf-8")
            self.assertEqual(parse_merge_blocks(text), [])
            self.assertIn(str(first / "nested"), text)
            self.assertNotIn(str(first / "nested" / "code.txt"), text)
            self.assertEqual(selected, [first, second, final])
            self.assertEqual(payload.read_bytes(), b"original\r\nbody\n")

    def test_repeated_rules_share_nodes_and_one_complete_decision(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_rules_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            decisions = []
            calls = []

            def select(nodes):
                calls.append(nodes)
                decisions.extend(node.definition.entry_path for node in nodes)
                values = []
                for node in nodes:
                    parent_enabled = (
                        node.parent_index is None
                        or values[node.parent_index] == "authorize"
                    )
                    values.append(
                        "authorize"
                        if parent_enabled and node.definition.entry_path != second
                        else "skip"
                    )
                return tuple(values)

            output = root / "merged.txt"
            result = self.merge(
                source, output, select, mode="list",
                text="linked\\**\\*.txt\nlinked\\nested\\code.txt",
            )
            self.assertTrue(result.output_result.succeeded)
            self.assertEqual(result.total_files, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(decisions.count(second), 1)
            self.assertEqual(decisions.count(final), 1)

    def test_background_stops_at_first_link_for_exact_and_wildcard(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_background_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            original = os.readlink

            def readlink(path, *args, **kwargs):
                if Path(path) in (second, final):
                    raise AssertionError("后台不得读取首个待授权节点之后的定义")
                return original(path, *args, **kwargs)

            with patch("core.link_paths.os.readlink", side_effect=readlink):
                results = inspect_demand_rules(
                    (
                        (1, "linked\\nested\\code.txt"),
                        (2, "linked\\nested\\*.txt"),
                    ),
                    str(source), Event(),
                )
            self.assertTrue(results[1].requires_authorization)
            self.assertTrue(results[2].requires_authorization)
            self.assertFalse(results[1].issue)
            self.assertFalse(results[2].issue)

    def test_cancel_stops_and_does_not_publish(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_cancel_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            output = root / "merged.txt"
            with self.assertRaises(SourceSelectionCancelled):
                self.merge(source, output, lambda nodes: None, mode="list",
                           text="linked\\**\\*.txt")
            self.assertFalse(output.exists())
            self.assertEqual(payload.read_bytes(), b"original\r\nbody\n")

    def test_fixed_parent_drift_is_rejected_before_new_link_read(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_drift_") as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "code.txt").write_bytes(b"old")
            traversal = SourceTraversal()
            traversal.resolve(source)
            relocated = root / "relocated"
            source.rename(relocated)
            external = root / "external"
            external.mkdir()
            (external / "code.txt").write_bytes(b"external")
            self.symlink(source, external, directory=True)
            with patch(
                "core.link_paths._read_definition",
                side_effect=AssertionError("不得读取变化后父链的新链接定义"),
            ):
                with self.assertRaises(ValueError):
                    traversal.resolve(source / "code.txt")

    def test_scan_dates_and_newlines_do_not_request_second_authorization(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_scan_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            output = root / "scan.txt"
            decisions = []

            def select(nodes):
                decisions.extend(node.definition.entry_path for node in nodes)
                return tuple("authorize" for node in nodes)

            result = panoramic_scan(
                str(source), str(output), prepare_exclusions("", ""),
                "", "", include_size=True, include_date=True,
                select_source_links=select,
                authorize_scan_scope=lambda title, description:
                self.fail("节点选择后不得额外询问目标元信息确认"),
            )
            raw = output.read_bytes()
            text = raw.decode("utf-8")
            self.assertNotIn(b"\r\r\n", raw)
            self.assertIn("文件夹自身修改时间", text)
            self.assertIn(str(first / "nested" / "code.txt"), text)
            self.assertEqual(result.file_count, 1)
            self.assertEqual(result.link_count, 3)
            self.assertEqual(len(decisions), 3)

    def test_plain_source_never_requests_link_selection(self):
        with TemporaryDirectory(prefix="Zipatch_nodes_plain_") as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_bytes(b"plain")

            def forbidden(nodes):
                self.fail("全普通来源不得弹出链接选择")

            result = self.merge(source, root / "merged.txt", forbidden)
            self.assertEqual(dict(result.entry_counts)["file"], 1)
            self.assertEqual(sum(dict(result.link_counts).values()), 0)

    def test_split_uses_nominal_path_not_origin(self):
        from services.restore_service import RestoreTask, prepare_restore

        with TemporaryDirectory(prefix="Zipatch_nodes_split_") as folder:
            root = Path(folder)
            target = root / "target"
            target.mkdir()
            backup = root / "backup"
            backup.mkdir()
            unrelated = root / "origin.txt"
            unrelated.write_bytes(b"must stay")
            with MergedContainer("--begin--", "--end--") as container:
                container.add_block(
                    "sub/a.txt", "restored\r\n", origin_path=str(unrelated),
                )
                text = "".join(container.text_chunks())
            plan = prepare_restore(
                None, str(target), "overwrite", source_text=text,
                backup_dir=str(backup), target_mode="directory",
            )
            task = RestoreTask(plan)
            try:
                result = task.execute()
            finally:
                task.invalidate()
            self.assertTrue(result.completed)
            self.assertEqual((target / "sub" / "a.txt").read_bytes(), b"restored\r\n")
            self.assertEqual(unrelated.read_bytes(), b"must stay")


@unittest.skipUnless(os.name == "nt", "文件来源及链接契约使用Windows")
class FileOnlyMergeTests(WindowsSourceNodeTests):
    """复用真实链接现场，并增加本轮用户显示及最终文件契约。"""

    def test_multiple_directory_aliases_do_not_generate_directory_blocks(self):
        with TemporaryDirectory(prefix="Zipatch_file_alias_") as folder:
            root = Path(folder)
            source = root / "source"
            library = root / "library"
            source.mkdir()
            library.mkdir()
            (library / "empty").mkdir()
            (library / "a.txt").write_bytes(b"a")
            self.symlink(source / "first", library, directory=True)
            self.symlink(source / "second", library, directory=True)
            output = root / "merged.txt"
            result = self.merge(
                source, output,
                lambda nodes: tuple("authorize" for node in nodes),
            )
            blocks = parse_merge_blocks(output.read_bytes().decode("utf-8"))
            self.assertEqual(len(blocks), 1)
            self.assertTrue(all(block.kind == "file" for block in blocks))
            self.assertEqual(blocks[0].body, "a")
            self.assertEqual(result.total_files, 1)
            self.assertEqual(sum(dict(result.link_counts).values()), 2)

    def test_wildcard_matching_output_rejects_without_overwrite(self):
        with TemporaryDirectory(prefix="Zipatch_list_output_") as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            output = source / "merged.txt"
            output.write_bytes(b"old result")
            (source / "a.txt").write_bytes(b"a")
            with self.assertRaisesRegex(ValueError, "输出"):
                self.merge(
                    source, output, None, mode="list", text="*.txt",
                )
            self.assertEqual(output.read_bytes(), b"old result")

    def test_directory_mode_still_skips_own_output(self):
        with TemporaryDirectory(prefix="Zipatch_directory_output_") as folder:
            source = Path(folder)
            output = source / "merged.txt"
            output.write_bytes(b"old result")
            (source / "a.txt").write_bytes(b"a")
            self.merge(source, output, None)
            blocks = parse_merge_blocks(output.read_bytes().decode("utf-8"))
            self.assertEqual(
                [(Path(block.path).name, block.body) for block in blocks],
                [("a.txt", "a")],
            )

    def test_plain_terminal_directory_under_link_requires_wildcard(self):
        with TemporaryDirectory(prefix="Zipatch_terminal_directory_") as folder:
            root = Path(folder)
            source = root / "source"
            library = root / "library"
            source.mkdir()
            library.mkdir()
            (library / "ordinary").mkdir()
            (library / "ordinary" / "a.txt").write_bytes(b"a")
            self.symlink(source / "linked", library, directory=True)
            output = root / "merged.txt"
            with self.assertRaisesRegex(
                ValueError,
                "文件列表不能直接选择普通文件夹，请使用文件夹列表或文件通配符",
            ):
                self.merge(
                    source, output,
                    lambda nodes: tuple("authorize" for node in nodes),
                    mode="list", text="linked\\ordinary",
                )
            self.assertFalse(output.exists())

    def test_explicit_directory_link_outputs_only_final_files(self):
        with TemporaryDirectory(prefix="Zipatch_exact_directory_link_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            output = root / "merged.txt"
            self.merge(
                source, output,
                lambda nodes: tuple("authorize" for node in nodes),
                mode="list", text="linked",
            )
            blocks = parse_merge_blocks(output.read_bytes().decode("utf-8"))
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].kind, "file")
            self.assertEqual(blocks[0].path, str(first / "nested" / "code.txt"))

    def test_hidden_actual_path_does_not_disable_authorization(self):
        with TemporaryDirectory(prefix="Zipatch_hide_actual_") as folder:
            root = Path(folder)
            source, actual, inner, payload, first, second, final = self.fixture(root)
            calls = []

            def select(nodes):
                calls.extend(node.definition.entry_path for node in nodes)
                return tuple("authorize" for node in nodes)

            output = root / "merged.txt"
            result = merge_files(
                mode="directory", relative_base_folder=str(source),
                directory_list_text=str(source), list_text="",
                output_file=str(output), exclusions=prepare_exclusions("", ""),
                preamble_text="", ending_text="",
                code_header_line="--begin--", code_footer_line="--end--",
                record_file_mtime=False, show_actual_path=False,
                select_source_links=select,
            )
            text = output.read_bytes().decode("utf-8")
            blocks = parse_merge_blocks(text)
            self.assertNotIn(SOURCE_PREFIX, text)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].path, str(first / "nested" / "code.txt"))
            self.assertEqual(len(calls), 3)
            self.assertEqual(sum(dict(result.link_counts).values()), 3)

            scan_output = root / "scan.txt"
            panoramic_scan(
                str(source), str(scan_output), prepare_exclusions("", ""),
                "", "", show_actual_path=False,
                select_source_links=select,
            )
            scan_text = scan_output.read_bytes().decode("utf-8")
            self.assertNotIn("实际路径：", scan_text)
            self.assertIn(str(first / "nested" / "code.txt"), scan_text)
            self.assertEqual(len(calls), 6)

    def test_default_display_is_enabled(self):
        from core.constants import DEFAULT_CONFIG

        self.assertIs(DEFAULT_CONFIG["scan"]["show_actual_path"], True)
        self.assertIs(DEFAULT_CONFIG["merge"]["show_actual_path"], True)
        self.assertEqual(
            encode_source_line("actual/file.txt"),
            '实际路径：actual/file.txt',
        )


if __name__ == "__main__":
    unittest.main()