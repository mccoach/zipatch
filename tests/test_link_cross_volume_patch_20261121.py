# -*- coding: utf-8 -*-

"""正式修改包的跨卷链接固定组合、保护恢复及失败前缀测试。

设备观察替换明确选择跨卷机制；真实对象仍在隔离临时目录。
该测试证明跨卷生产分支及正式协调，不冒充真实双卷实测。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file
from core.filesystem_changes import (
    execute_filesystem_change, prepare_link_creation,
)
from core.link_paths import (
    LinkSpecification, inspect_link_path, specification_from_link,
)
from core.recovery_protection import (
    RecoveryProtectionSession, read_recovery_evidence,
)
from services.patch_executor import PatchExecutionError
from services.patch_service import (
    apply_backup_restore, apply_patch, confirm_backup_restore,
    preview_backup_restore, preview_patch,
)


@unittest.skipUnless(os.name == "nt", "正式跨卷链接组合使用Windows")
class CrossVolumePatchLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_cross_volume_patch_",
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

    def link(self, entry, target, kind):
        if kind == "junction":
            result = execute_filesystem_change(prepare_link_creation(
                observe_file(entry),
                LinkSpecification(
                    "junction", "\\\\?\\" + str(target), False,
                ),
            ))
            self.assertTrue(result.succeeded)
        else:
            os.symlink(
                target, entry,
                target_is_directory=kind == "directory_symlink",
            )
        self.links.append((entry, kind != "file_symlink"))
        return inspect_link_path(entry, follow_final_link=False)

    def cases(self):
        return (
            ("file_symlink", self.private, "file"),
            ("directory_symlink", self.outside, "dir"),
            ("junction", self.outside, "dir"),
        )

    def preview_move(self, source, destination, family, *, overwrite=False):
        boundary = "ZIPATCH_BOUNDARY_CROSS_VOLUME_TEST_20261121103500_K8M4"
        policy = "overwrite" if overwrite else "error"
        package = "\n".join((
            f'<<ZIPATCH_V3 boundary="{boundary}">>',
            f'---OP move_{family} id="move001" if_exists="{policy}"',
            "---PATH", source.relative_to(self.project).as_posix(), boundary,
            "---NEW_PATH",
            destination.relative_to(self.project).as_posix(), boundary,
            "---END_OP", "<</ZIPATCH_V3>>",
        ))
        result = preview_patch(
            None, self.project, package,
            backup_dir=str(self.backups),
            normalize_text_newlines=False,
            authorize_patch_scope=lambda scopes, root:
            tuple("authorize" for scope in scopes),
        )
        self.assertFalse(result.has_errors, result.preview_text)
        self.assertEqual(
            result.execution.plan.actions[0].name_move.mechanism,
            "copy_remove",
        )
        return result

    def device_observation(self, source):
        # 只替换正式设备观察，不修改计划实例或执行机制字段。
        return patch(
            "core.name_moves._destination_device",
            return_value=source.lstat().st_dev + 1,
        )

    def guard_external_body(self):
        original = Path.open

        def guarded(path, *arguments, **keywords):
            if path == self.outside or self.outside in path.parents:
                raise AssertionError("入口移动及恢复不得读取引用目标正文")
            return original(path, *arguments, **keywords)

        return patch("pathlib.Path.open", new=guarded)

    def restore(self, backup):
        preview = preview_backup_restore(
            str(backup), str(self.backups),
            backup_project_root=str(self.project),
        )
        confirm_backup_restore(preview)
        result = apply_backup_restore(preview)
        self.assertTrue(result.complete_original_state)
        return result

    def test_three_kinds_formal_cross_volume_move_and_active_restore(self):
        for kind, target, family in self.cases():
            with self.subTest(kind=kind):
                source = self.project / ("source-" + kind)
                destination = self.project / ("destination-" + kind)
                original = self.link(source, target, kind)
                specification = specification_from_link(original.entry_link)
                self.links.append((destination, kind != "file_symlink"))

                with self.guard_external_body():
                    with self.device_observation(source):
                        preview = self.preview_move(
                            source, destination, family,
                        )
                        result = apply_patch(preview)

                    self.assertFalse(os.path.lexists(source))
                    actual = inspect_link_path(
                        destination, follow_final_link=False,
                    )
                    self.assertEqual(
                        specification_from_link(actual.entry_link),
                        specification,
                    )
                    self.assertNotEqual(
                        actual.object_identity, original.object_identity,
                    )
                    receipts = result.unit_results[0].results
                    self.assertEqual(
                        tuple(receipt.kind for receipt in receipts),
                        ("create_link", "remove_link"),
                    )
                    self.assertTrue(all(
                        receipt.succeeded for receipt in receipts
                    ))
                    evidence = read_recovery_evidence(result.backup_root)
                    self.assertEqual(
                        evidence.document.value["units"][0]["fact"]["state"],
                        "completed",
                    )
                    self.restore(result.backup_root)
                    restored = inspect_link_path(
                        source, follow_final_link=False,
                    )
                    self.assertEqual(
                        specification_from_link(restored.entry_link),
                        specification,
                    )
                    self.assertFalse(os.path.lexists(destination))

                self.assertEqual(
                    self.private.read_bytes(), b"outside-private",
                )

    def test_file_link_overwrite_preserves_old_target_hardlink_peer(self):
        source = self.project / "source-link"
        original = self.link(source, "missing.txt", "file_symlink")
        destination = self.project / "destination"
        destination.write_bytes(b"old-shared")
        peer = self.root / "peer"
        os.link(destination, peer)
        identifier = observe_file(destination).object_identity
        self.links.append((destination, False))

        with self.device_observation(source):
            preview = self.preview_move(
                source, destination, "file", overwrite=True,
            )
            result = apply_patch(preview)

        self.assertFalse(os.path.lexists(source))
        actual = inspect_link_path(destination, follow_final_link=False)
        self.assertEqual(
            specification_from_link(actual.entry_link),
            specification_from_link(original.entry_link),
        )
        self.assertEqual(peer.read_bytes(), b"old-shared")
        self.assertEqual(observe_file(peer).object_identity, identifier)
        self.assertEqual(
            tuple(receipt.kind for receipt in result.unit_results[0].results),
            ("remove_file", "create_link", "remove_link"),
        )
        self.restore(result.backup_root)
        self.assertEqual(destination.read_bytes(), b"old-shared")
        self.assertEqual(peer.read_bytes(), b"old-shared")
        self.assertTrue(os.path.lexists(source))

    def test_relative_definition_is_not_rewritten_for_new_parent(self):
        source_folder = self.project / "first"
        destination_folder = self.project / "second"
        source_folder.mkdir()
        destination_folder.mkdir()
        source = source_folder / "alias"
        destination = destination_folder / "alias"
        original = self.link(source, "body.txt", "file_symlink")
        self.links.append((destination, False))

        with self.device_observation(source):
            result = apply_patch(self.preview_move(
                source, destination, "file",
            ))

        actual = inspect_link_path(destination, follow_final_link=False)
        self.assertEqual(actual.entry_link.raw_target, "body.txt")
        self.assertEqual(
            actual.entry_link.direct_target,
            destination_folder / "body.txt",
        )
        self.assertNotEqual(
            actual.entry_link.direct_target,
            original.entry_link.direct_target,
        )
        self.assertFalse((source_folder / "body.txt").exists())
        self.assertFalse((destination_folder / "body.txt").exists())
        self.restore(result.backup_root)
        self.assertEqual(os.readlink(source), "body.txt")
        self.assertFalse(os.path.lexists(destination))

    def test_three_kinds_fact_failure_rolls_back_without_second_backup(self):
        for kind, target, family in self.cases():
            with self.subTest(kind=kind):
                source = self.project / ("fact-source-" + kind)
                destination = self.project / ("fact-destination-" + kind)
                original = self.link(source, target, kind)
                self.links.append((destination, kind != "file_symlink"))
                before_tasks = set(self.backups.iterdir())

                with self.guard_external_body():
                    with self.device_observation(source):
                        preview = self.preview_move(
                            source, destination, family,
                        )
                        with patch.object(
                            RecoveryProtectionSession, "complete",
                            side_effect=OSError("injected completed fact failure"),
                        ):
                            with self.assertRaises(PatchExecutionError) as caught:
                                apply_patch(preview)

                    self.assertEqual(
                        caught.exception.rollback_status, "completed",
                    )
                    self.assertFalse(os.path.lexists(destination))
                    restored = inspect_link_path(
                        source, follow_final_link=False,
                    )
                    self.assertEqual(
                        specification_from_link(restored.entry_link),
                        specification_from_link(original.entry_link),
                    )
                    self.assertEqual(
                        set(self.backups.iterdir()) - before_tasks,
                        {preview.execution.backup_root},
                    )
                    read_recovery_evidence(preview.execution.backup_root)

                self.assertEqual(
                    self.private.read_bytes(), b"outside-private",
                )

    def test_source_removal_failure_rolls_back_confirmed_destination(self):
        for kind, target, family in self.cases():
            with self.subTest(kind=kind):
                source = self.project / ("remove-source-" + kind)
                destination = self.project / ("remove-destination-" + kind)
                original = self.link(source, target, kind)
                self.links.append((destination, kind != "file_symlink"))
                original_unlink = Path.unlink
                original_rmdir = Path.rmdir

                def guarded_unlink(path, *arguments, **keywords):
                    if path == source:
                        raise PermissionError("injected source unlink failure")
                    return original_unlink(path, *arguments, **keywords)

                def guarded_rmdir(path, *arguments, **keywords):
                    if path == source:
                        raise PermissionError("injected source rmdir failure")
                    return original_rmdir(path, *arguments, **keywords)

                with self.device_observation(source):
                    preview = self.preview_move(
                        source, destination, family,
                    )
                    with patch(
                        "pathlib.Path.unlink", new=guarded_unlink,
                    ), patch(
                        "pathlib.Path.rmdir", new=guarded_rmdir,
                    ):
                        with self.assertRaises(PatchExecutionError) as caught:
                            apply_patch(preview)

                self.assertEqual(
                    caught.exception.rollback_status, "completed",
                )
                receipts = caught.exception.results
                self.assertEqual(len(receipts), 2)
                self.assertTrue(receipts[0].succeeded)
                self.assertEqual(receipts[0].kind, "create_link")
                self.assertFalse(receipts[1].succeeded)
                self.assertEqual(receipts[1].kind, "remove_link")
                self.assertTrue(receipts[1].attempted)
                self.assertEqual(
                    inspect_link_path(source, follow_final_link=False),
                    original,
                )
                self.assertFalse(os.path.lexists(destination))
                read_recovery_evidence(preview.execution.backup_root)

    def test_unknown_destination_creation_does_not_remove_source_or_guess_identity(self):
        source = self.project / "unknown-source"
        destination = self.project / "unknown-destination"
        original = self.link(source, "missing.txt", "file_symlink")
        self.links.append((destination, False))
        original_extract = specification_from_link

        def fail_destination(definition):
            if definition.entry_path == destination:
                raise OSError("injected destination verification failure")
            return original_extract(definition)

        with self.device_observation(source):
            preview = self.preview_move(
                source, destination, "file",
            )
            with patch(
                "core.filesystem_changes.specification_from_link",
                side_effect=fail_destination,
            ):
                with self.assertRaises(PatchExecutionError) as caught:
                    apply_patch(preview)

        self.assertEqual(caught.exception.rollback_status, "failed")
        self.assertEqual(
            inspect_link_path(source, follow_final_link=False), original,
        )
        self.assertTrue(os.path.lexists(destination))
        self.assertEqual(len(caught.exception.results), 1)
        failed = caught.exception.results[0]
        self.assertEqual(failed.kind, "create_link")
        self.assertTrue(failed.attempted)
        self.assertIsNone(failed.created_link)
        self.assertFalse(failed.succeeded)
        read_recovery_evidence(preview.execution.backup_root)
        self.assertTrue(preview.execution.consumed)
        with self.assertRaises(ValueError):
            apply_patch(preview)


if __name__ == "__main__":
    unittest.main()
