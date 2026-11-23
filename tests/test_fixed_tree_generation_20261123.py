# -*- coding: utf-8 -*-

"""唯一固定结构生成核心及普通树复制适配的定向回归。"""

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.content_publication import (
    ContentPublicationError, ContentPublicationOptions,
    ContentPublicationResult,
)
from core.execution_state import ExecutionState
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import (
    execute_tree_copy, prepare_tree_copy, TreeCopyError,
)
from core.fixed_tree_generation import FixedTreeGeneration, FixedTreeMember


class FixedTreeGenerationTests(unittest.TestCase):
    def make_generation(self, target, members, *, state=None, check=None):
        receipts = []
        stages = []
        generation = FixedTreeGeneration(
            observe_file(target), tuple(members),
            state=state,
            recheck_context=check or (lambda: None),
            record_step=receipts.append,
            stage=lambda target, phase: stages.append((target, phase)),
        )
        return generation, receipts, stages

    def test_empty_directory_is_generated_once(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            generation, receipts, stages = self.make_generation(
                target, (FixedTreeMember("", "directory"),),
            )
            actual = generation.execute()
            self.assertEqual(actual.content_records, (("", "directory", ""),))
            self.assertEqual(len(receipts), 1)
            self.assertTrue(receipts[0].succeeded)
            self.assertIs(generation.final_observation, actual)
            self.assertEqual(stages[-1], (None, "completed"))
            with self.assertRaises(ValueError):
                generation.execute()

    def test_fixed_bytes_and_source_share_one_generation_chain(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.txt"
            source.write_bytes(b"source\r\n")
            observed = observe_file(
                source, include_bytes=True, include_digest=True, include_mtime=True,
            )
            target = root / "target"
            generation, receipts, _ = self.make_generation(target, (
                FixedTreeMember("sub/raw.txt", "file", content=b"raw\n"),
                FixedTreeMember("", "directory"),
                FixedTreeMember("sub", "directory"),
                FixedTreeMember(
                    "copied.txt", "file", source=observed,
                    options=ContentPublicationOptions(mtime_ns=observed.mtime_ns),
                ),
            ))
            actual = generation.execute()
            self.assertEqual((target / "copied.txt").read_bytes(), b"source\r\n")
            self.assertEqual((target / "sub/raw.txt").read_bytes(), b"raw\n")
            self.assertEqual(len(receipts), 4)
            self.assertEqual(len(generation.results), 4)
            self.assertTrue(all(receipt.succeeded for receipt in receipts))
            self.assertEqual(
                dict(actual.file_times)["copied.txt"], observed.mtime_ns,
            )
            self.assertTrue(all(
                published is accepted
                for published, accepted in zip(receipts, generation.results)
            ))

    def test_borrowed_state_records_each_step_once(self):
        with TemporaryDirectory() as folder:
            state = ExecutionState()
            generation, receipts, _ = self.make_generation(
                Path(folder) / "target",
                (
                    FixedTreeMember("", "directory"),
                    FixedTreeMember("a.txt", "file", content=b"a"),
                ),
                state=state,
            )
            generation.execute()
            self.assertEqual(len(state.results), 2)
            self.assertEqual(len(receipts), 2)
            self.assertTrue(all(
                left is right for left, right in zip(state.results, receipts)
            ))

    def test_invalid_members_are_rejected_before_creation(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            with self.assertRaises(ValueError):
                self.make_generation(target, (
                    FixedTreeMember("", "directory"),
                    FixedTreeMember("missing/a.txt", "file", content=b"a"),
                ))
            self.assertFalse(target.exists())

    def test_external_member_stops_generation_without_removing_it(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "target"

            def check():
                if target.exists():
                    external = target / "external.txt"
                    if not external.exists():
                        external.write_bytes(b"external")

            generation, receipts, _ = self.make_generation(
                target,
                (
                    FixedTreeMember("", "directory"),
                    FixedTreeMember("a.txt", "file", content=b"a"),
                ),
                check=check,
            )
            with self.assertRaises(ValueError):
                generation.execute()
            self.assertEqual((target / "external.txt").read_bytes(), b"external")
            self.assertFalse((target / "a.txt").exists())
            self.assertEqual(len(receipts), 1)
            self.assertTrue(receipts[0].succeeded)

    def test_failed_publication_keeps_real_prefix_and_cleans_only_staging(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            generation, receipts, _ = self.make_generation(target, (
                FixedTreeMember("", "directory"),
                FixedTreeMember("a.txt", "file", content=b"a"),
                FixedTreeMember("b.txt", "file", content=b"b"),
            ))

            def fail(artifact):
                raise ContentPublicationError(ContentPublicationResult(
                    artifact.expected.path, artifact.mechanism,
                    "完整成果及固定前提复核", False, "not_attempted",
                    False, False, None, "not_required",
                    error=ValueError("fixed publication rejected"),
                ))

            with patch("core.fixed_content_execution.publish_content", side_effect=fail):
                with self.assertRaises(ContentPublicationError):
                    generation.execute()
            self.assertEqual(len(receipts), 2)
            self.assertTrue(receipts[0].succeeded)
            self.assertFalse(receipts[1].succeeded)
            self.assertFalse((target / "a.txt").exists())
            self.assertFalse((target / "b.txt").exists())
            self.assertEqual(tuple(target.iterdir()), ())
            with self.assertRaises(ValueError):
                generation.execute()


class TreeCopyAdapterTests(unittest.TestCase):
    def test_ordinary_tree_copy_preserves_contents_times_and_results(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "empty").mkdir()
            (source / "a.txt").write_bytes(b"original\n")
            observed = observe_tree(
                source, include_bytes=True, include_mtime=True,
            )
            target = root / "target"
            copy = prepare_tree_copy(observed, observe_file(target))
            result = execute_tree_copy(copy)
            self.assertTrue(result.succeeded)
            self.assertEqual(copy.final_observation.content_records, observed.content_records)
            self.assertEqual(copy.final_observation.file_times, observed.file_times)
            self.assertEqual((target / "a.txt").read_bytes(), b"original\n")
            self.assertTrue((target / "empty").is_dir())
            self.assertTrue(all(receipt.succeeded for receipt in copy.results))
            self.assertEqual(
                {
                    position: digest
                    for position, kind, digest
                    in copy.final_observation.content_records
                    if kind == "file"
                },
                {"a.txt": hashlib.sha256(b"original\n").hexdigest()},
            )

    def test_source_drift_is_wrapped_before_target_creation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_bytes(b"original")
            observed = observe_tree(
                source, include_bytes=True, include_mtime=True,
            )
            target = root / "target"
            copy = prepare_tree_copy(observed, observe_file(target))
            (source / "a.txt").write_bytes(b"changed")
            with self.assertRaises(TreeCopyError):
                execute_tree_copy(copy)
            self.assertFalse(target.exists())
            self.assertEqual(copy.results, ())
            self.assertIsNone(copy.final_observation)


if __name__ == "__main__":
    unittest.main()