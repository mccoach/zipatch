# -*- coding: utf-8 -*-

"""四类对象授权及修改包整包执行政策的隔离Windows回归。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.merged_container import MergedContainer
from core.merged_format import parse_merge_blocks
from services.merge_service import merge_files
from services.scan_service import panoramic_scan
from services.patch_executor import PatchExecutor
from services.restore_service import prepare_restore
from services.link_authorization import (
    content_selection_groups, collect_link_nodes, project_link_selection,
)


@unittest.skipUnless(os.name == "nt", "正式链接业务使用Windows")
class HardlinkAuthorizationTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "source"
        source.mkdir()
        first = source / "a.py"
        second = source / "b.py"
        first.write_bytes(b"original\n")
        os.link(first, second)
        return source, first, second

    def test_merge_lists_each_hardlink_and_reads_only_selected_route(self):
        with TemporaryDirectory(prefix="ZipatchHardlinkSelect_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            calls = []

            def select(nodes):
                calls.append(nodes)
                self.assertEqual([node.kind for node in nodes], [
                    "file_hardlink", "file_hardlink",
                ])
                return ("skip", "authorize")

            output = root / "merged.txt"
            result = merge_files(
                "directory", "", str(source), "", str(output),
                prepare_exclusions("", ""), "", "", "--begin--", "--end--",
                record_file_mtime=False, select_source_links=select,
            )
            self.assertEqual(len(calls), 1)
            blocks = parse_merge_blocks(output.read_bytes().decode("utf-8"))
            self.assertEqual([block.path for block in blocks], [str(second)])
            self.assertEqual(result.total_files, 1)
            self.assertEqual(first.read_bytes(), b"original\n")

    def test_scan_hardlink_selection_never_reads_body(self):
        with TemporaryDirectory(prefix="ZipatchHardlinkScan_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            original_open = Path.open

            def guarded(path, *args, **kwargs):
                if Path(path) in (first, second):
                    raise AssertionError("扫描不得读取来源正文")
                return original_open(path, *args, **kwargs)

            output = root / "scan.txt"
            with patch.object(Path, "open", guarded):
                result = panoramic_scan(
                    str(source), str(output), prepare_exclusions("", ""),
                    "", "", select_source_links=lambda nodes:
                    ("authorize", "skip"),
                )
            self.assertEqual(result.file_count, 1)
            text = output.read_bytes().decode("utf-8")
            self.assertIn(str(first), text)

    def operation(self):
        return {
            "id": "replace_one", "op": "replace_exact",
            "path": "a.py", "count": "1",
            "old": "original", "new": "changed",
        }

    def test_patch_preview_does_not_authorize_and_denial_stops_whole_package(self):
        with TemporaryDirectory(prefix="ZipatchHardlinkPatch_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            calls = []

            def deny(nodes, project):
                calls.append(nodes)
                self.assertTrue(any(node.kind == "file_hardlink" for node in nodes))
                return False

            executor = PatchExecutor(
                None, source, normalize_text_newlines=False,
                backup_dir=str(root / "backup"),
                authorize_patch_scope=deny,
            )
            result = executor.preview({
                "version": "3.0", "operations": [self.operation()],
            })
            self.assertFalse(result[2])
            self.assertEqual(calls, [])
            with self.assertRaisesRegex(ValueError, "整包授权已拒绝"):
                executor.apply()
            self.assertEqual(len(calls), 1)
            self.assertTrue(executor.consumed)
            self.assertFalse((root / "backup").exists())
            self.assertEqual(first.read_bytes(), b"original\n")
            self.assertEqual(second.read_bytes(), b"original\n")

    def test_patch_permission_is_requested_once_after_successful_preview(self):
        with TemporaryDirectory(prefix="ZipatchHardlinkPatchAllow_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            calls = []
            executor = PatchExecutor(
                None, source, normalize_text_newlines=False,
                backup_dir=str(root / "backup"),
                authorize_patch_scope=lambda nodes, project:
                calls.append(nodes) or True,
            )
            preview = executor.preview({
                "version": "3.0", "operations": [self.operation()],
            })
            self.assertFalse(preview[2])
            self.assertEqual(calls, [])
            executor.apply()
            self.assertEqual(len(calls), 1)
            self.assertEqual(first.read_bytes(), b"changed\n")
            self.assertEqual(second.read_bytes(), b"changed\n")

    def test_split_conflicting_authorization_cancels_shared_body_work(self):
        with TemporaryDirectory(prefix="ZipatchHardlinkSplit_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            with MergedContainer("--begin--", "--end--") as container:
                container.add_block(str(first), "new\n")
                container.add_reference(str(second), 1)
                text = "".join(container.text_chunks())
            plan = prepare_restore(
                None, "", "overwrite", source_text=text,
                target_mode="in_place", backup_dir=str(root / "backup"),
                select_split_scope=lambda candidates: ("skip", "authorize"),
            )
            self.assertEqual(plan.entries, ())
            self.assertIsNone(plan.backup_root)
            self.assertEqual(first.read_bytes(), b"original\n")

    def test_count_change_during_source_selection_rejects(self):
        with TemporaryDirectory(prefix="ZipatchHardlinkCount_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)

            def select(nodes):
                os.link(first, root / "external.py")
                return ("authorize",) * len(nodes)

            with self.assertRaisesRegex(ValueError, "链接数量"):
                merge_files(
                    "directory", "", str(source), "", str(root / "merged.txt"),
                    prepare_exclusions("", ""), "", "", "--begin--", "--end--",
                    record_file_mtime=False, select_source_links=select,
                )
            self.assertFalse((root / "merged.txt").exists())

    def test_shared_group_projection_keeps_ordinary_work(self):
        with TemporaryDirectory(prefix="ZipatchHardlinkProjection_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            ordinary = source / "ordinary.py"
            ordinary.write_bytes(b"ordinary")
            observations = tuple((observe_file(path),) for path in (
                first, second, ordinary,
            ))
            groups = content_selection_groups(
                observations, (observations[0][0].object_identity,),
            )
            nodes = collect_link_nodes(
                ((), (), ()), observations, ("write", "write", "write"),
            )
            self.assertEqual(len(nodes), 2)
            decisions = project_link_selection(
                nodes, ("skip", "authorize"), 3, groups,
            )
            self.assertEqual(decisions, ("skip", "skip", "authorize"))


if __name__ == "__main__":
    unittest.main(verbosity=2)