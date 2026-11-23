# -*- coding: utf-8 -*-

"""修改包正式正文消费者的范围授权、跳过裁剪及身份回归。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.link_paths import inspect_link_path
from core.recovery_protection import read_recovery_evidence
from services.patch_executor import PatchExecutor, PatchExecutionError


@unittest.skipUnless(os.name == "nt", "修改包链接正文业务使用Windows")
class PatchLinkContentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_patch_link_content_",
        )
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.target = self.root / "outside.txt"
        self.target.write_bytes(b"before")
        self.entry = self.project / "alias.txt"
        os.symlink(self.target, self.entry)
        self.links = [(self.entry, False)]

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def executor(self, decide=None):
        def package_decision(scopes, root):
            decisions = tuple(
                decide(scope.operation, scope.accesses[0], root)
                if scope.requires_authorization else "authorize"
                for scope in scopes
            )
            return None if "cancel" in decisions else decisions

        return PatchExecutor(
            None, self.project, backup_dir=str(self.backups),
            normalize_text_newlines=False,
            authorize_patch_scope=package_decision if decide is not None else None,
        )

    def operation(self, identifier="edit001", path="alias.txt"):
        return {
            "op": "replace_exact", "id": identifier, "path": path,
            "old": "before", "new": "after", "count": "1",
        }

    def preview(self, executor, operations):
        return executor.preview({
            "version": "3.0", "operations": operations,
        })

    def test_unapproved_content_access_does_not_read_external_body(self):
        original_open = Path.open

        def guarded_open(path, *args, **keywords):
            if path == self.target:
                raise AssertionError("没有本地授权不得读取实际正文")
            return original_open(path, *args, **keywords)

        executor = self.executor()
        with patch("pathlib.Path.open", new=guarded_open):
            result = self.preview(executor, [self.operation()])
        self.assertTrue(result[2])
        self.assertIsNone(executor.plan)
        self.assertEqual(self.target.read_bytes(), b"before")
        self.assertTrue(os.path.lexists(self.entry))

    def test_authorized_external_hardlink_edit_preserves_entry_and_identity(self):
        peer = self.root / "peer.txt"
        os.link(self.target, peer)
        identifier = self.target.lstat().st_ino
        definition = os.readlink(self.entry)
        decisions = []

        def decide(operation, access, root):
            self.assertEqual(self.target.read_bytes(), b"before")
            self.assertEqual(access.actual_path, self.target)
            self.assertEqual(access.nominal_path, self.entry)
            self.assertEqual(root, self.project)
            decisions.append(operation["id"])
            return "authorize"

        executor = self.executor(decide)
        result = self.preview(executor, [self.operation()])
        self.assertFalse(result[2], result[0])
        self.assertIn("项目根外", result[0])
        self.assertEqual(decisions, ["edit001"])
        self.assertEqual(
            tuple(member.target for member in executor.projection.objects),
            (self.target,),
        )
        executor.apply()
        self.assertEqual(self.target.read_bytes(), b"after")
        self.assertEqual(peer.read_bytes(), b"after")
        self.assertEqual(self.target.lstat().st_ino, identifier)
        self.assertEqual(os.readlink(self.entry), definition)
        self.assertEqual(executor.results[0].mechanism, "in_place")
        evidence = read_recovery_evidence(executor.backup_root)
        self.assertEqual(
            Path(evidence.document.value["objects"][0]["target"]),
            self.target,
        )
        self.assertEqual(
            evidence.materials["o000001"].source_bytes, b"before",
        )

    def test_local_skip_prunes_body_protection_and_access_dependency(self):
        ordinary = self.project / "ordinary.txt"
        ordinary.write_bytes(b"before")
        executor = self.executor(lambda operation, access, root: "skip")
        original_open = Path.open

        def guarded_open(path, *args, **keywords):
            if path == self.target:
                raise AssertionError("范围跳过不得读取实际正文")
            return original_open(path, *args, **keywords)

        with patch("pathlib.Path.open", new=guarded_open):
            result = self.preview(executor, [
                self.operation("skip001"),
                self.operation("ordinary001", "ordinary.txt"),
            ])
        self.assertFalse(result[2], result[0])
        self.assertEqual(
            tuple(member.target for member in executor.projection.objects),
            (ordinary,),
        )
        self.assertTrue(executor.plan.actions[0].skip_reason)
        self.assertFalse(executor.plan.actions[0].execute)
        # 已裁剪依赖：被跳过入口后来改指向不阻止无关固定操作。
        self.entry.unlink()
        os.symlink("different.txt", self.entry)
        executor.apply()
        self.assertEqual(ordinary.read_bytes(), b"after")
        self.assertEqual(self.target.read_bytes(), b"before")
        self.assertEqual(os.readlink(self.entry), "different.txt")
        self.assertEqual(len(executor.results), 1)

    def test_authorization_does_not_bypass_invalid_old_text(self):
        executor = self.executor(lambda operation, access, root: "authorize")
        operation = self.operation()
        operation["old"] = "not-present"
        result = self.preview(executor, [operation])
        self.assertTrue(result[2])
        self.assertIsNone(executor.plan)
        self.assertIsNone(executor.protection)
        self.assertEqual(self.target.read_bytes(), b"before")

    def test_retarget_after_preview_stops_before_body_mutation(self):
        executor = self.executor(lambda operation, access, root: "authorize")
        result = self.preview(executor, [self.operation()])
        self.assertFalse(result[2], result[0])
        other = self.root / "other.txt"
        other.write_bytes(b"other")
        self.entry.unlink()
        os.symlink(other, self.entry)
        with self.assertRaises(PatchExecutionError):
            executor.apply()
        self.assertEqual(executor.results, ())
        self.assertEqual(self.target.read_bytes(), b"before")
        self.assertEqual(other.read_bytes(), b"other")

    def test_directory_link_parent_keeps_nominal_and_actual_edit_scope(self):
        outside = self.root / "outside-folder"
        outside.mkdir()
        body = outside / "body.txt"
        body.write_bytes(b"before")
        parent = self.project / "linked-folder"
        os.symlink(outside, parent, target_is_directory=True)
        self.links.append((parent, True))
        original = inspect_link_path(parent, follow_final_link=False)
        executor = self.executor(lambda operation, access, root: "authorize")
        result = self.preview(executor, [
            self.operation(path="linked-folder/body.txt"),
        ])
        self.assertFalse(result[2], result[0])
        self.assertEqual(executor.projection.objects[0].target, body)
        executor.apply()
        self.assertEqual(body.read_bytes(), b"after")
        self.assertEqual(
            inspect_link_path(parent, follow_final_link=False), original,
        )


if __name__ == "__main__":
    unittest.main()
