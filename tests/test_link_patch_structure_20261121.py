# -*- coding: utf-8 -*-

"""正式修改包入口结构授权、复制移动删除及失败回滚集成测试。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import execute_filesystem_change, prepare_link_creation
from core.link_paths import (
    LinkSpecification, inspect_link_path, specification_from_link,
)
from core.protection_projection import state_from_observation
from core.recovery_protection import (
    RecoveryProtectionSession, read_recovery_evidence,
)
from services.patch_executor import PatchExecutionError
from services.patch_service import (
    apply_backup_restore, apply_patch, confirm_backup_restore,
    preview_backup_restore, preview_patch,
)


@unittest.skipUnless(os.name == "nt", "正式链接结构业务使用Windows")
class PatchLinkStructureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_patch_structure_",
        )
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.private = self.outside / "private.bin"
        self.private.write_bytes(b"outside-private")
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, kind="file_symlink"):
        if kind == "junction":
            receipt = execute_filesystem_change(prepare_link_creation(
                observe_file(entry),
                LinkSpecification("junction", "\\\\?\\" + str(target), False),
            ))
            self.assertTrue(receipt.succeeded)
        else:
            os.symlink(
                target, entry, target_is_directory=kind == "directory_symlink",
            )
        self.links.append((entry, kind != "file_symlink"))
        return inspect_link_path(entry, follow_final_link=False)

    def package(self, operations):
        boundary = "ZIPATCH_BOUNDARY_TEST_STRUCTURE_20261121093000_Q4M7R2"
        lines = [f'<<ZIPATCH_V3 boundary="{boundary}">>']
        for number, operation in enumerate(operations, 1):
            kind = operation["op"]
            head = f'---OP {kind} id="op{number:03d}"'
            if "if_exists" in operation:
                head += f' if_exists="{operation["if_exists"]}"'
            if "count" in operation:
                head += f' count="{operation["count"]}"'
            lines.append(head)
            for field, marker in (
                ("path", "PATH"), ("new_path", "NEW_PATH"),
                ("old", "OLD"), ("new", "NEW"), ("content", "CONTENT"),
            ):
                if field in operation:
                    lines.extend((f"---{marker}", operation[field], boundary))
            lines.append("---END_OP")
        lines.append("<</ZIPATCH_V3>>")
        return "\n".join(lines)

    def preview(self, operations, *, structural=None, content=None):
        from services.patch_file_plan import TEXT_WRITE_OPS

        def decide(scopes, root):
            decisions = []
            for scope in scopes:
                if not scope.requires_authorization:
                    decisions.append("authorize")
                    continue
                callback = (
                    content if scope.operation["op"] in TEXT_WRITE_OPS else structural
                )
                if callback is None:
                    raise ValueError("测试没有授予对应固定范围")
                decisions.append(callback(
                    scope.operation,
                    scope.accesses[0]
                    if scope.operation["op"] in TEXT_WRITE_OPS else scope.observations,
                    root,
                ))
            return None if "cancel" in decisions else tuple(decisions)

        return preview_patch(
            None, self.project, self.package(operations),
            allow_delete=True, backup_dir=str(self.backups),
            normalize_text_newlines=False,
            authorize_patch_scope=(
                decide if structural is not None or content is not None else None
            ),
        )

    def approved(self, operations):
        preview = self.preview(
            operations, structural=lambda operation, observations, root: "authorize",
        )
        self.assertFalse(preview.has_errors, preview.preview_text)
        return preview

    def restore(self, backup):
        preview = preview_backup_restore(
            backup, str(self.backups),
            backup_project_root=str(self.project),
        )
        confirm_backup_restore(preview)
        return apply_backup_restore(preview)

    def observe_tree(self, path):
        return observe_tree(
            path, include_bytes=True, include_mtime=True, include_links=True,
        )

    def guard_external_body(self):
        original_open = Path.open

        def guarded(path, *args, **keywords):
            if path == self.outside or self.outside in path.parents:
                raise AssertionError("结构业务不得读取根外引用目标正文")
            return original_open(path, *args, **keywords)

        return patch("pathlib.Path.open", new=guarded)

    def test_three_kinds_copy_delete_and_restore_saved_definition(self):
        for kind, target, operation_family in (
            ("file_symlink", self.private, "file"),
            ("directory_symlink", self.outside, "dir"),
            ("junction", self.outside, "dir"),
        ):
            with self.subTest(kind=kind):
                source = self.project / ("source-" + kind)
                destination = self.project / ("copy-" + kind)
                original = self.link(source, target, kind)
                specification = specification_from_link(original.entry_link)
                self.links.append((destination, kind != "file_symlink"))
                with self.guard_external_body():
                    copied = apply_patch(self.approved([{
                        "op": "copy_" + operation_family,
                        "path": source.name, "new_path": destination.name,
                    }]))
                    actual = inspect_link_path(destination, follow_final_link=False)
                    self.assertEqual(
                        specification_from_link(actual.entry_link), specification,
                    )
                    self.assertEqual(
                        inspect_link_path(source, follow_final_link=False), original,
                    )
                    evidence = read_recovery_evidence(copied.backup_root)
                    self.assertEqual(len(evidence.document.value["objects"]), 1)
                    self.assertEqual(
                        evidence.document.value["objects"][0]["original"]["kind"],
                        "absent",
                    )
                    deleted = apply_patch(self.approved([{
                        "op": "delete_" + operation_family,
                        "path": destination.name,
                    }]))
                    self.assertFalse(os.path.lexists(destination))
                    self.restore(deleted.backup_root)
                    restored = inspect_link_path(destination, follow_final_link=False)
                    self.assertEqual(
                        specification_from_link(restored.entry_link), specification,
                    )
                self.assertEqual(self.private.read_bytes(), b"outside-private")

    def test_three_kinds_native_move_and_active_restore(self):
        for kind, target, family in (
            ("file_symlink", self.private, "file"),
            ("directory_symlink", self.outside, "dir"),
            ("junction", self.outside, "dir"),
        ):
            with self.subTest(kind=kind):
                source = self.project / ("old-" + kind)
                destination = self.project / ("new-" + kind)
                original = self.link(source, target, kind)
                self.links.append((destination, kind != "file_symlink"))
                with self.guard_external_body():
                    preview = self.approved([{
                        "op": "rename_" + family,
                        "path": source.name, "new_path": destination.name,
                    }])
                    self.assertEqual(
                        preview.execution.plan.actions[0].name_move.mechanism,
                        "native",
                    )
                    result = apply_patch(preview)
                    actual = inspect_link_path(destination, follow_final_link=False)
                    self.assertFalse(os.path.lexists(source))
                    self.assertEqual(actual.object_identity, original.object_identity)
                    self.assertEqual(
                        specification_from_link(actual.entry_link),
                        specification_from_link(original.entry_link),
                    )
                    self.restore(result.backup_root)
                    self.assertTrue(os.path.lexists(source))
                    self.assertFalse(os.path.lexists(destination))
                    restored = inspect_link_path(source, follow_final_link=False)
                    self.assertEqual(
                        specification_from_link(restored.entry_link),
                        specification_from_link(original.entry_link),
                    )
                self.assertEqual(self.private.read_bytes(), b"outside-private")

    def test_mixed_tree_formal_copy_move_delete_and_restore(self):
        source = self.project / "tree"
        source.mkdir()
        (source / "empty").mkdir()
        body = source / "body.bin"
        body.write_bytes(b"\x00ordinary")
        modified = 1700000000000000000
        os.utime(body, ns=(modified, modified))
        self.link(source / "middle", self.private)
        self.link(source / "first", "middle")
        self.link(source / "dangling", "missing")
        self.link(source / "folder", self.outside, "directory_symlink")
        self.link(source / "joined", self.outside, "junction")
        original = self.observe_tree(source)
        desired = state_from_observation(original)
        copied_path = self.project / "copied"
        moved_path = self.project / "moved"
        for relative, observed in original.links:
            self.links.extend((
                (copied_path / relative, observed.kind != "file_symlink"),
                (moved_path / relative, observed.kind != "file_symlink"),
            ))
        with self.guard_external_body():
            apply_patch(self.approved([{
                "op": "copy_dir", "path": "tree", "new_path": "copied",
            }]))
            copied = self.observe_tree(copied_path)
            self.assertEqual(state_from_observation(copied), desired)
            self.assertEqual(copied.file_times, original.file_times)
            self.assertEqual(
                dict(copied.links)["first"].entry_link.direct_target,
                copied_path / "middle",
            )
            moved = apply_patch(self.approved([{
                "op": "move_dir", "path": "copied", "new_path": "moved",
            }]))
            self.assertFalse(os.path.lexists(copied_path))
            moved_tree = self.observe_tree(moved_path)
            self.assertEqual(state_from_observation(moved_tree), desired)
            self.assertEqual(
                dict(moved_tree.files)["body.bin"].object_identity,
                dict(copied.files)["body.bin"].object_identity,
            )
            self.restore(moved.backup_root)
            self.assertFalse(os.path.lexists(moved_path))
            self.assertEqual(
                state_from_observation(self.observe_tree(copied_path)), desired,
            )
            deleted = apply_patch(self.approved([{
                "op": "delete_dir", "path": "copied",
            }]))
            self.assertFalse(os.path.lexists(copied_path))
            self.restore(deleted.backup_root)
            self.assertEqual(
                state_from_observation(self.observe_tree(copied_path)), desired,
            )
            self.assertEqual(state_from_observation(self.observe_tree(source)), desired)
        self.assertEqual(self.private.read_bytes(), b"outside-private")

    def test_unapproved_mixed_tree_and_entry_remain_blocked(self):
        scope = self.project / "tree"
        scope.mkdir()
        self.link(scope / "alias", self.private)
        original = self.observe_tree(scope)
        with self.guard_external_body():
            preview = self.preview([{"op": "delete_dir", "path": "tree"}])
        self.assertTrue(preview.has_errors)
        self.assertIsNone(preview.execution.plan)
        self.assertIsNone(preview.execution.protection)
        from core.file_observation import recheck_tree

        # 必要只读观察可以改变atime；它不是正文版本或入口结构事实。
        # 正式复核仍检查完整结构、普通正文、身份、链接数量、
        # 链接原定义和正式文件mtime，不通过整份stat相等绑定读取副作用。
        current = recheck_tree(original, check_mtime=True)
        for original_members, current_members in (
            (original.directories, current.directories),
            (original.files, current.files),
        ):
            for (relative, before), (position, after) in zip(
                original_members, current_members,
            ):
                self.assertEqual(position, relative)
                self.assertEqual(after.attributes.st_mode, before.attributes.st_mode)
                self.assertEqual(
                    after.attributes.st_mtime_ns, before.attributes.st_mtime_ns,
                )
                self.assertEqual(
                    after.attributes.st_ctime_ns, before.attributes.st_ctime_ns,
                )

    def test_structure_local_skip_prunes_source_and_protection(self):
        entry = self.project / "alias"
        self.link(entry, "missing")
        ordinary = self.project / "ordinary"
        ordinary.write_bytes(b"before")
        preview = self.preview([
            {"op": "delete_file", "path": "alias"},
            {"op": "replace_exact", "path": "ordinary",
             "old": "before", "new": "after", "count": "1"},
        ], structural=lambda operation, observations, root: "skip")
        self.assertFalse(preview.has_errors, preview.preview_text)
        self.assertEqual(
            tuple(member.target for member in preview.execution.projection.objects),
            (ordinary,),
        )
        entry.unlink()
        os.symlink("changed", entry)
        result = apply_patch(preview)
        self.assertEqual(ordinary.read_bytes(), b"after")
        self.assertEqual(os.readlink(entry), "changed")
        self.assertEqual(len(result.unit_results), 2)
        self.assertEqual(result.unit_results[0].status, "skipped")

    def test_joint_body_access_and_link_removal_conflict_is_rejected(self):
        body = self.project / "body"
        body.write_bytes(b"before")
        entry = self.project / "alias"
        self.link(entry, body)
        preview = self.preview([
            {"op": "replace_exact", "path": "alias",
             "old": "before", "new": "after", "count": "1"},
            {"op": "delete_file", "path": "body"},
        ], content=lambda operation, access, root: "authorize",
            structural=lambda operation, observations, root: "authorize")
        self.assertTrue(preview.has_errors)
        self.assertIsNone(preview.execution.plan)
        self.assertEqual(body.read_bytes(), b"before")
        self.assertTrue(os.path.lexists(entry))

    def test_copy_overwrite_preserves_old_hardlink_peer_body(self):
        source = self.project / "source"
        source.write_bytes(b"copied")
        destination = self.project / "destination"
        destination.write_bytes(b"old-shared")
        peer = self.root / "peer"
        os.link(destination, peer)
        old_identity = observe_file(destination).object_identity
        result = apply_patch(self.approved([{
            "op": "copy_file", "path": "source",
            "new_path": "destination", "if_exists": "overwrite",
        }]))
        self.assertEqual(destination.read_bytes(), b"copied")
        self.assertEqual(peer.read_bytes(), b"old-shared")
        self.assertEqual(observe_file(peer).object_identity, old_identity)
        self.assertNotEqual(observe_file(destination).object_identity, old_identity)
        self.assertEqual(result.unit_results[0].results[0].mechanism, "atomic_replace")

    def test_link_copy_overwrite_changes_only_named_entry(self):
        source = self.project / "source-link"
        original = self.link(source, "missing")
        destination = self.project / "destination"
        destination.write_bytes(b"old-shared")
        peer = self.root / "peer"
        os.link(destination, peer)
        self.links.append((destination, False))
        result = apply_patch(self.approved([{
            "op": "copy_file", "path": "source-link",
            "new_path": "destination", "if_exists": "overwrite",
        }]))
        actual = inspect_link_path(destination, follow_final_link=False)
        self.assertEqual(
            specification_from_link(actual.entry_link),
            specification_from_link(original.entry_link),
        )
        self.assertEqual(peer.read_bytes(), b"old-shared")
        self.restore(result.backup_root)
        self.assertFalse(destination.is_symlink())
        self.assertEqual(destination.read_bytes(), b"old-shared")
        self.assertEqual(peer.read_bytes(), b"old-shared")

    def test_retarget_after_structure_preview_stops_before_work(self):
        entry = self.project / "alias"
        self.link(entry, "first")
        preview = self.approved([{"op": "delete_file", "path": "alias"}])
        entry.unlink()
        os.symlink("second", entry)
        with self.assertRaises(PatchExecutionError) as caught:
            apply_patch(preview)
        self.assertEqual(caught.exception.results, ())
        self.assertEqual(os.readlink(entry), "second")
        self.assertFalse(tuple(self.backups.iterdir()))

    def test_failed_completed_fact_rolls_back_link_copy_without_second_backup(self):
        source = self.project / "source"
        original = self.link(source, "missing")
        destination = self.project / "destination"
        self.links.append((destination, False))
        preview = self.approved([{
            "op": "copy_file", "path": "source", "new_path": "destination",
        }])
        with patch.object(
            RecoveryProtectionSession, "complete",
            side_effect=OSError("injected completed fact failure"),
        ):
            with self.assertRaises(PatchExecutionError) as caught:
                apply_patch(preview)
        self.assertEqual(caught.exception.rollback_status, "completed")
        self.assertFalse(os.path.lexists(destination))
        self.assertEqual(
            inspect_link_path(source, follow_final_link=False), original,
        )
        self.assertEqual(len(tuple(self.backups.iterdir())), 1)
        evidence = read_recovery_evidence(preview.execution.backup_root)
        self.assertEqual(
            evidence.document.value["objects"][0]["original"]["kind"], "absent",
        )

    def test_unknown_link_creation_failure_keeps_scene_and_original_backup(self):
        source = self.project / "source"
        self.link(source, "missing")
        destination = self.project / "destination"
        self.links.append((destination, False))
        preview = self.approved([{
            "op": "copy_file", "path": "source", "new_path": "destination",
        }])
        original_extract = specification_from_link

        def fail_destination(definition):
            if definition.entry_path == destination:
                raise OSError("injected creation verification failure")
            return original_extract(definition)

        with patch(
            "core.filesystem_changes.specification_from_link",
            side_effect=fail_destination,
        ):
            with self.assertRaises(PatchExecutionError) as caught:
                apply_patch(preview)
        self.assertEqual(caught.exception.rollback_status, "failed")
        self.assertTrue(os.path.lexists(destination))
        self.assertTrue(preview.execution.backup_root.is_dir())
        read_recovery_evidence(preview.execution.backup_root)
        self.assertTrue(preview.execution.consumed)
        with self.assertRaises(ValueError):
            apply_patch(preview)
        self.assertTrue(os.path.lexists(destination))

    def test_structure_parent_link_requires_explicit_actual_scope_and_restores(self):
        parent = self.project / "parent"
        original = self.link(parent, self.outside, "directory_symlink")
        operation = {"op": "delete_file", "path": "parent/private.bin"}
        rejected = self.preview([operation])
        self.assertTrue(rejected.has_errors)
        self.assertIsNone(rejected.execution.plan)
        self.assertEqual(self.private.read_bytes(), b"outside-private")

        accepted = self.approved([operation])
        self.assertEqual(
            accepted.execution.projection.objects[0].target, self.private,
        )
        result = apply_patch(accepted)
        self.assertFalse(self.private.exists())
        self.assertEqual(
            inspect_link_path(parent, follow_final_link=False), original,
        )
        self.restore(result.backup_root)
        self.assertEqual(self.private.read_bytes(), b"outside-private")
        self.assertEqual(
            inspect_link_path(parent, follow_final_link=False), original,
        )


if __name__ == "__main__":
    unittest.main()