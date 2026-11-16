# -*- coding: utf-8 -*-

"""统一退出清理和目录转文件成果交付；只使用临时文件。"""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from core.content_publication import (
    ContentPublicationError, cleanup_content_artifact,
    prepare_content_artifact, publish_content,
)
from core.directory_file_replacement import (
    execute_directory_file_replacement, prepare_directory_file_replacement,
)
from core.file_observation import observe_file, observe_tree
from core.protection_projection import FixedEffect, state_from_bytes


class ContentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "target.txt"

    def tearDown(self):
        self.temporary.cleanup()

    def test_unattempted_staging_is_removed_without_changing_target(self):
        self.target.write_bytes(b"original")
        artifact = prepare_content_artifact(
            observe_file(self.target, include_digest=True), b"desired",
        )
        self.assertTrue(cleanup_content_artifact(artifact))
        self.assertEqual(artifact._lifecycle, "disposed")
        self.assertFalse(artifact.path.exists())
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertTrue(cleanup_content_artifact(artifact))

    def test_completed_publication_is_not_removed(self):
        artifact = prepare_content_artifact(observe_file(self.target), b"desired")
        receipt = publish_content(artifact)
        self.assertTrue(cleanup_content_artifact(artifact))
        self.assertTrue(receipt.succeeded)
        self.assertEqual(self.target.read_bytes(), b"desired")

    def test_failed_hardlink_attempt_retains_same_complete_emergency(self):
        self.target.write_bytes(b"original")
        alias = self.root / "alias.txt"
        os.link(self.target, alias)
        artifact = prepare_content_artifact(
            observe_file(self.target, include_digest=True), b"desired",
        )
        self.target.write_bytes(b"external")
        with self.assertRaises(ContentPublicationError):
            publish_content(artifact)
        self.assertFalse(cleanup_content_artifact(artifact))
        self.assertEqual(artifact.path.read_bytes(), b"desired")
        self.assertEqual(alias.read_bytes(), b"external")
        self.assertEqual(artifact._lifecycle, "attempted")

    def test_replaced_staging_is_not_deleted_and_cleanup_reports_failure(self):
        artifact = prepare_content_artifact(observe_file(self.target), b"desired")
        owned = self.root / "owned-staging"
        artifact.path.rename(owned)
        artifact.path.write_bytes(b"external")
        diagnostics = io.StringIO()
        with contextlib.redirect_stderr(diagnostics):
            self.assertFalse(cleanup_content_artifact(artifact))
        self.assertEqual(artifact.path.read_bytes(), b"external")
        self.assertEqual(owned.read_bytes(), b"desired")
        self.assertIn("清理未完成", diagnostics.getvalue())

    def test_directory_replacement_delivers_final_verified_observation(self):
        source = self.root / "source.txt"
        source.write_bytes(b"desired")
        self.target.mkdir()
        (self.target / "old.txt").write_bytes(b"old")
        before = observe_tree(
            self.target, include_bytes=True, include_mtime=True,
        )
        observed_source = observe_file(
            source, include_bytes=True, include_digest=True, include_mtime=True,
        )
        prepared = prepare_directory_file_replacement(
            FixedEffect(before, state_from_bytes(b"desired")),
            observed_source, mtime_ns=observed_source.mtime_ns,
            durable_publication=False,
        )
        self.assertIsNone(prepared.final_observation)
        result = execute_directory_file_replacement(prepared)
        actual = prepared.final_observation
        self.assertTrue(result.succeeded)
        self.assertEqual(actual.path, self.target)
        self.assertEqual(actual.digest, observed_source.digest)
        self.assertEqual(actual.mtime_ns, observed_source.mtime_ns)
        self.assertEqual(
            (actual.object_identity.device, actual.object_identity.inode),
            prepared.artifact.identity,
        )
        self.assertEqual(actual.link_count, 1)
        self.assertEqual(self.target.read_bytes(), b"desired")
        self.assertEqual(source.read_bytes(), b"desired")


if __name__ == "__main__":
    unittest.main()
