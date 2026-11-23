# -*- coding: utf-8 -*-

"""固定计划提前拒绝及真实原生名称移动的最终观察回归。

只使用临时文件；不mock名称改变、成果核验或文件正文读取。
观察计数只验证本轮明确删除的同边界重复目标树获取。
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import core.file_observation as file_observation
from core.content_publication import ContentPublicationOptions
from core.file_observation import observe_file, observe_tree
from core.fixed_tree_generation import FixedTreeGeneration, FixedTreeMember
from core.name_moves import (
    execute_observed_name_move, prepare_observed_name_move,
)


class TreePlanPreflightTests(unittest.TestCase):
    def reject_options(self, options):
        with TemporaryDirectory(prefix="Zipatch_tree_preflight_") as folder:
            target = Path(folder) / "target"
            receipts = []
            with self.assertRaises((TypeError, ValueError)):
                FixedTreeGeneration(
                    observe_file(target),
                    (
                        FixedTreeMember("", "directory"),
                        FixedTreeMember(
                            "a.txt", "file", content=b"a", options=options,
                        ),
                    ),
                    recheck_context=lambda: None,
                    record_step=receipts.append,
                    stage=lambda target, phase: None,
                )
            self.assertEqual(observe_file(target).kind, "absent")
            self.assertEqual(receipts, [])

    def test_invalid_file_option_types_are_rejected_before_directory_creation(self):
        for options in (
            ContentPublicationOptions(mtime_ns="not an integer"),
            ContentPublicationOptions(durable_publication=1),
            ContentPublicationOptions(file_mode=-1),
            ContentPublicationOptions(check_target_mtime=1),
        ):
            with self.subTest(options=options):
                self.reject_options(options)

    def test_access_time_without_modification_time_is_rejected_before_creation(self):
        self.reject_options(ContentPublicationOptions(atime_ns=1))


@unittest.skipUnless(os.name == "nt", "真实名称移动使用本项目Windows路径观察")
class NativeMoveFinalObservationTests(unittest.TestCase):
    def test_native_directory_target_is_observed_once_at_completion(self):
        with TemporaryDirectory(prefix="Zipatch_native_tree_final_") as folder:
            root = Path(folder)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "empty").mkdir()
            (source / "a.txt").write_bytes(b"original\r\n")
            before = observe_tree(
                source, include_bytes=True, include_mtime=True,
            )
            move = prepare_observed_name_move(before, observe_file(target))
            self.assertEqual(move.mechanism, "native")
            original_observer = file_observation.observe_tree

            with patch(
                "core.file_observation.observe_tree",
                wraps=original_observer,
            ) as observed_trees:
                result = execute_observed_name_move(move)

            self.assertTrue(result.succeeded)
            target_reads = [
                call
                for call in observed_trees.call_args_list
                if Path(call.args[0]) == target
            ]
            self.assertEqual(len(target_reads), 1)
            missing, actual = move.final_observations
            self.assertEqual(missing.path, source)
            self.assertEqual(missing.kind, "absent")
            self.assertEqual(actual.path, target)
            self.assertEqual(actual.content_records, before.content_records)
            self.assertEqual(actual.file_times, before.file_times)
            self.assertEqual(
                tuple(
                    (position, member.object_identity)
                    for position, member in actual.directories
                ),
                tuple(
                    (position, member.object_identity)
                    for position, member in before.directories
                ),
            )
            self.assertEqual((target / "a.txt").read_bytes(), b"original\r\n")
            self.assertEqual(len(move.results), 1)
            self.assertTrue(move.results[0].succeeded)
            self.assertEqual(move.current, None)
            self.assertEqual(move.phase, "completed")

    def test_native_file_returns_target_chain_and_preserves_identity(self):
        with TemporaryDirectory(prefix="Zipatch_native_file_final_") as folder:
            root = Path(folder)
            source = root / "source.txt"
            target_parent = root / "destination"
            target_parent.mkdir()
            target = target_parent / "target.txt"
            source.write_bytes(b"original")
            before = observe_file(
                source, include_bytes=True, include_digest=True,
                include_mtime=True,
            )
            move = prepare_observed_name_move(before, observe_file(target))
            result = execute_observed_name_move(move)
            self.assertTrue(result.succeeded)
            missing, actual = move.final_observations
            self.assertEqual(missing.kind, "absent")
            self.assertEqual(actual.path, target)
            self.assertEqual(
                tuple(path for path, _ in actual.chain),
                (*reversed(target.parents), target),
            )
            self.assertEqual(actual.object_identity, before.object_identity)
            self.assertEqual(actual.digest, before.digest)
            self.assertEqual(actual.mtime_ns, before.mtime_ns)
            self.assertEqual(actual.source_bytes, before.source_bytes)
            self.assertEqual(len(move.results), 1)
            self.assertTrue(move.results[0].succeeded)


if __name__ == "__main__":
    unittest.main()