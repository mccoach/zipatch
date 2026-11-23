# -*- coding: utf-8 -*-

"""整包元信息、统一决定、实际别名、父链结构和回滚依赖集成。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file
from core.filesystem_changes import execute_filesystem_change, prepare_link_creation
from core.link_paths import LinkSpecification, inspect_link_path
from core.recovery_protection import RecoveryProtectionSession, read_recovery_evidence
from services.patch_executor import PatchExecutionError
from services.patch_service import apply_patch, preview_patch


@unittest.skipUnless(os.name == "nt", "整包范围业务使用Windows")
class PatchPackageScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, kind="directory_symlink"):
        if kind == "junction":
            receipt = execute_filesystem_change(prepare_link_creation(
                observe_file(entry),
                LinkSpecification("junction", "\\\\?\\" + str(target), False),
            ))
            self.assertTrue(receipt.succeeded)
        else:
            os.symlink(target, entry, target_is_directory=kind != "file_symlink")
        self.links.append((entry, kind != "file_symlink"))
        return inspect_link_path(entry, follow_final_link=False)

    def package(self, operations):
        boundary = "ZIPATCH_BOUNDARY_PACKAGE_SCOPE_TEST_20261121110000_A8K4"
        lines = [f'<<ZIPATCH_V3 boundary="{boundary}">>']
        for number, operation in enumerate(operations, 1):
            head = f'---OP {operation["op"]} id="op{number:03d}"'
            for name in ("count", "if_exists"):
                if name in operation:
                    head += f' {name}="{operation[name]}"'
            lines.append(head)
            for field in ("path", "new_path", "old", "new", "content"):
                if field in operation:
                    lines.extend((f"---{field.upper()}", operation[field], boundary))
            lines.append("---END_OP")
        lines.append("<</ZIPATCH_V3>>")
        return "\n".join(lines)

    def preview(self, operations, decider):
        return preview_patch(
            None, self.project, self.package(operations),
            allow_delete=True, normalize_text_newlines=False,
            backup_dir=str(self.backups), authorize_patch_scope=decider,
            authorize_patch_enumeration=lambda title, description: True,
        )

    def edit(self, path, old="before", new="after"):
        return {
            "op": "replace_exact", "path": path,
            "old": old, "new": new, "count": "1",
        }

    def test_complete_metadata_precedes_any_plain_body_read(self):
        tree = self.project / "tree"
        tree.mkdir()
        ordinary = tree / "ordinary"
        ordinary.write_bytes(b"before")
        self.link(tree / "leaf", self.outside)
        another = self.project / "another"
        another.write_bytes(b"before")
        authorized = False
        opened = []
        original_open = Path.open
        calls = []

        def guarded(path, *args, **keywords):
            if path in (ordinary, another):
                self.assertTrue(authorized, "授权前不能读取普通正文")
                opened.append(path)
            return original_open(path, *args, **keywords)

        def decide(scopes, root):
            nonlocal authorized
            self.assertEqual(len(scopes), 2)
            self.assertEqual(opened, [])
            self.assertEqual(
                {relative for relative, _ in scopes[0].observations[0].files},
                {"ordinary"},
            )
            calls.append(scopes)
            authorized = True
            return ("skip", "authorize")

        with patch("pathlib.Path.open", new=guarded):
            result = self.preview([
                {"op": "delete_dir", "path": "tree"},
                self.edit("another"),
            ], decide)
        self.assertFalse(result.has_errors, result.preview_text)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(ordinary, opened)
        self.assertEqual(
            tuple(member.target for member in result.execution.projection.objects),
            (another,),
        )
        apply_patch(result)
        self.assertTrue(tree.is_dir())
        self.assertEqual(ordinary.read_bytes(), b"before")
        self.assertEqual(another.read_bytes(), b"after")

    def test_two_nominal_content_aliases_share_one_actual_file_unit(self):
        body = self.outside / "body"
        body.write_bytes(b"first second")
        self.link(self.project / "one", body, "file_symlink")
        self.link(self.project / "two", body, "file_symlink")
        result = self.preview([
            self.edit("one", "first", "FIRST"),
            self.edit("two", "second", "SECOND"),
        ], lambda scopes, root: ("authorize", "authorize"))
        self.assertFalse(result.has_errors, result.preview_text)
        self.assertEqual(len(result.execution.execution_units), 1)
        self.assertEqual(len(result.execution.projection.objects), 1)
        applied = apply_patch(result)
        self.assertEqual(body.read_bytes(), b"FIRST SECOND")
        self.assertEqual(len(applied.unit_results[0].results), 1)

    def test_actual_alias_overlapping_old_ranges_reject_entire_package(self):
        body = self.outside / "body"
        body.write_bytes(b"before")
        self.link(self.project / "one", body, "file_symlink")
        self.link(self.project / "two", body, "file_symlink")
        result = self.preview([
            self.edit("one"), self.edit("two"),
        ], lambda scopes, root: ("authorize", "authorize"))
        self.assertTrue(result.has_errors)
        self.assertIsNone(result.execution.plan)
        self.assertEqual(body.read_bytes(), b"before")
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_directory_symlink_and_junction_parent_structure_copy(self):
        body = self.outside / "body"
        body.write_bytes(b"before")
        for kind in ("directory_symlink", "junction"):
            with self.subTest(kind=kind):
                parent = self.project / kind
                original = self.link(parent, self.outside, kind)
                destination = self.project / ("copy-" + kind)
                result = self.preview([{
                    "op": "copy_file", "path": kind + "/body",
                    "new_path": destination.name,
                }], lambda scopes, root: ("authorize",))
                self.assertFalse(result.has_errors, result.preview_text)
                self.assertEqual(
                    result.execution.plan.actions[0].expectations[0].path, body,
                )
                apply_patch(result)
                self.assertEqual(destination.read_bytes(), b"before")
                self.assertEqual(body.read_bytes(), b"before")
                self.assertEqual(
                    inspect_link_path(parent, follow_final_link=False), original,
                )

    def test_access_dependency_and_entry_removal_conflict(self):
        body = self.outside / "body"
        body.write_bytes(b"before")
        self.link(self.project / "alias", body, "file_symlink")
        result = self.preview([
            self.edit("alias"),
            {"op": "delete_file", "path": "alias"},
        ], lambda scopes, root: ("authorize", "authorize"))
        self.assertTrue(result.has_errors)
        self.assertEqual(body.read_bytes(), b"before")

    def test_skipped_alias_dependency_is_not_rechecked(self):
        body = self.outside / "body"
        body.write_bytes(b"before")
        alias = self.project / "alias"
        self.link(alias, body, "file_symlink")
        ordinary = self.project / "ordinary"
        ordinary.write_bytes(b"before")
        result = self.preview([
            self.edit("alias"), self.edit("ordinary"),
        ], lambda scopes, root: ("skip", "authorize"))
        self.assertFalse(result.has_errors, result.preview_text)
        alias.unlink()
        os.symlink("different", alias)
        apply_patch(result)
        self.assertEqual(body.read_bytes(), b"before")
        self.assertEqual(ordinary.read_bytes(), b"after")

    def test_parent_retarget_blocks_execution_before_backup_or_work(self):
        body = self.outside / "body"
        body.write_bytes(b"before")
        parent = self.project / "parent"
        self.link(parent, self.outside)
        result = self.preview([
            self.edit("parent/body"),
        ], lambda scopes, root: ("authorize",))
        self.assertFalse(result.has_errors, result.preview_text)
        other = self.root / "other"
        other.mkdir()
        (other / "body").write_bytes(b"external")
        os.rmdir(parent)
        os.symlink(other, parent, target_is_directory=True)
        with self.assertRaises(PatchExecutionError) as caught:
            apply_patch(result)
        self.assertEqual(caught.exception.results, ())
        self.assertEqual(body.read_bytes(), b"before")
        self.assertEqual((other / "body").read_bytes(), b"external")
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_fact_failure_restores_actual_body_with_original_access_dependency(self):
        body = self.outside / "body"
        body.write_bytes(b"before")
        parent = self.project / "parent"
        original = self.link(parent, self.outside)
        result = self.preview([
            self.edit("parent/body"),
        ], lambda scopes, root: ("authorize",))
        self.assertFalse(result.has_errors, result.preview_text)
        with patch.object(
            RecoveryProtectionSession, "complete",
            side_effect=OSError("injected completed fact failure"),
        ):
            with self.assertRaises(PatchExecutionError) as caught:
                apply_patch(result)
        self.assertEqual(caught.exception.rollback_status, "completed")
        self.assertEqual(body.read_bytes(), b"before")
        self.assertEqual(
            inspect_link_path(parent, follow_final_link=False), original,
        )
        self.assertEqual(len(tuple(self.backups.iterdir())), 1)
        read_recovery_evidence(result.execution.backup_root)

    def test_retarget_after_work_stops_rollback_without_touching_new_target(self):
        body = self.outside / "body"
        body.write_bytes(b"before")
        parent = self.project / "parent"
        self.link(parent, self.outside)
        other = self.root / "other"
        other.mkdir()
        external = other / "body"
        external.write_bytes(b"external")
        result = self.preview([
            self.edit("parent/body"),
        ], lambda scopes, root: ("authorize",))
        self.assertFalse(result.has_errors, result.preview_text)

        def change_dependency_and_fail(session, unit_id, results):
            os.rmdir(parent)
            os.symlink(other, parent, target_is_directory=True)
            raise OSError("injected dependency change after completed work")

        with patch.object(
            RecoveryProtectionSession, "complete", new=change_dependency_and_fail,
        ):
            with self.assertRaises(PatchExecutionError) as caught:
                apply_patch(result)
        self.assertEqual(caught.exception.rollback_status, "failed")
        self.assertEqual(body.read_bytes(), b"after")
        self.assertEqual(external.read_bytes(), b"external")
        read_recovery_evidence(result.execution.backup_root)
        self.assertTrue(result.execution.consumed)

    def test_plain_sibling_edits_keep_shared_parent_identity(self):
        folder = self.project / "ordinary"
        folder.mkdir()
        first = folder / "first"
        second = folder / "second"
        first.write_bytes(b"before")
        second.write_bytes(b"before")
        parent_identity = inspect_link_path(
            folder, follow_final_link=False,
        ).object_identity
        result = self.preview([
            self.edit("ordinary/first"),
            self.edit("ordinary/second"),
        ], lambda scopes, root: ("authorize", "authorize"))
        self.assertFalse(result.has_errors, result.preview_text)
        self.assertEqual(len(result.execution.projection.objects), 2)
        applied = apply_patch(result)
        self.assertEqual(first.read_bytes(), b"after")
        self.assertEqual(second.read_bytes(), b"after")
        self.assertEqual(
            inspect_link_path(folder, follow_final_link=False).object_identity,
            parent_identity,
        )
        self.assertTrue(all(
            unit.work_completed for unit in applied.unit_results
        ))

    def test_new_file_parent_creation_does_not_conflict_with_existing_ancestor(self):
        result = self.preview([{
            "op": "write_file", "path": "new/nested/body",
            "content": "created", "if_exists": "error",
        }], lambda scopes, root: ("authorize",))
        self.assertFalse(result.has_errors, result.preview_text)
        project_identity = inspect_link_path(
            self.project, follow_final_link=False,
        ).object_identity
        applied = apply_patch(result)
        self.assertEqual(
            (self.project / "new" / "nested" / "body").read_bytes(),
            b"created",
        )
        self.assertEqual(
            inspect_link_path(
                self.project, follow_final_link=False,
            ).object_identity,
            project_identity,
        )
        self.assertTrue(applied.unit_results[0].work_completed)
        evidence = read_recovery_evidence(applied.backup_root)
        self.assertEqual(
            evidence.document.value["objects"][0]["original"]["kind"],
            "absent",
        )

    def test_other_actual_range_cannot_remove_nominal_access_parent(self):
        route = self.project / "route"
        route.mkdir()
        body = self.outside / "body"
        body.write_bytes(b"before")
        original = self.link(route / "link", self.outside)
        self.link(self.project / "bridge", self.project)
        result = self.preview([
            self.edit("route/link/body"),
            {"op": "delete_dir", "path": "bridge/route"},
        ], lambda scopes, root: ("authorize", "authorize"))
        self.assertTrue(result.has_errors)
        self.assertIsNone(result.execution.plan)
        self.assertIn(
            "名义访问依赖与工作改变范围冲突", result.preview_text,
        )
        self.assertEqual(body.read_bytes(), b"before")
        self.assertEqual(
            inspect_link_path(route / "link", follow_final_link=False),
            original,
        )
        self.assertEqual(tuple(self.backups.iterdir()), ())

    def test_cancelled_complete_scope_never_reads_any_body(self):
        body = self.project / "body"
        body.write_bytes(b"before")
        original_open = Path.open

        def guarded(path, *args, **keywords):
            if path == body:
                raise AssertionError("取消整包不得读取工作正文")
            return original_open(path, *args, **keywords)

        with patch("pathlib.Path.open", new=guarded):
            result = self.preview([self.edit("body")], lambda scopes, root: None)
        self.assertTrue(result.has_errors)
        self.assertIsNone(result.execution.plan)
        self.assertEqual(tuple(self.backups.iterdir()), ())


if __name__ == "__main__":
    unittest.main()
