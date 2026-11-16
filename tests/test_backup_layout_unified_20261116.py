# -*- coding: utf-8 -*-

"""唯一材料布局和三个真实业务调用链；仅使用独占临时目录。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.backup_material_layout import build_backup_material_paths
from core.file_observation import observe_file
from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)
from core.recovery_protection import read_recovery_evidence
from services.patch_service import (
    apply_backup_restore, apply_patch, confirm_backup_restore,
    preview_backup_restore, preview_patch,
)
from services.restore_service import RestoreTask, prepare_restore


def projection_for(paths):
    units = tuple(
        FixedUnit(
            (f"member{number}",),
            (FixedEffect(
                observe_file(
                    path, include_bytes=True, include_digest=True,
                    include_mtime=True,
                ),
                state_from_bytes(b"desired"),
            ),),
        )
        for number, path in enumerate(paths, 1)
    )
    return build_protection_projection(units, forbidden_roots=())


def material_by_target(session):
    return {
        member.target: session.material_paths[member.object_id]
        for member in session.projection.objects
    }


def patch_text(content):
    boundary = "ZIPATCH_BOUNDARY_TEST_LAYOUT_20261116035900_A7C9142E"
    return (
        f'<<ZIPATCH_V3 boundary="{boundary}">>\n'
        '---OP write_file id="change" if_exists="overwrite"\n'
        "---PATH\nsample.txt\n"
        f"{boundary}\n"
        "---CONTENT\n"
        f"{content}\n"
        f"{boundary}\n"
        "---END_OP\n"
        "<</ZIPATCH_V3>>\n"
    )


class LayoutRuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.base = self.home / "project"
        self.base.mkdir()

    def test_root_file_has_no_volume_layer(self):
        target = self.base / "sample.txt"
        projection = projection_for((target,))
        self.assertEqual(
            tuple(build_backup_material_paths(
                projection, base=self.base,
            ).values()),
            ("sample.txt",),
        )

    def test_nested_file_keeps_root_relative_position(self):
        target = self.base / "src" / "sample.txt"
        target.parent.mkdir()
        projection = projection_for((target,))
        self.assertEqual(
            tuple(build_backup_material_paths(
                projection, base=self.base,
            ).values()),
            ("src/sample.txt",),
        )

    def test_protected_root_preserves_its_name(self):
        missing_root = self.home / "missing_project"
        projection = projection_for((missing_root,))
        self.assertEqual(
            tuple(build_backup_material_paths(
                projection, base=missing_root,
            ).values()),
            ("missing_project",),
        )

    def test_same_volume_external_target_uses_common_base(self):
        external = self.home / "other"
        external.mkdir()
        projection = projection_for((
            self.base / "sample.txt", external / "sample.txt",
        ))
        positions = set(build_backup_material_paths(
            projection, base=self.base,
        ).values())
        self.assertEqual(
            positions, {"project/sample.txt", "other/sample.txt"},
        )

    def test_absent_objects_participate_without_creating_files(self):
        target = self.base / "new.txt"
        projection = projection_for((target,))
        member = projection.objects[0]
        self.assertEqual(member.original_type, "absent")
        self.assertEqual(
            build_backup_material_paths(
                projection, base=self.base,
            )[member.object_id],
            "new.txt",
        )
        self.assertFalse(target.exists())

    def test_no_base_has_explicit_volume_layer(self):
        target = self.base / "sample.txt"
        projection = projection_for((target,))
        drive = target.drive
        prefix = (
            drive[0].upper()
            if len(drive) == 2 and drive[1] == ":" else
            "UNC/" + "/".join(drive.lstrip("\\").split("\\"))
            if drive.startswith("\\\\") else "ROOT"
        )
        self.assertEqual(
            tuple(build_backup_material_paths(projection).values()),
            (prefix + "/sample.txt",),
        )

    def test_layout_does_not_depend_on_member_order(self):
        paths = (self.base / "a.txt", self.base / "b.txt")
        first = projection_for(paths)
        second = projection_for(tuple(reversed(paths)))
        first_paths = build_backup_material_paths(first, base=self.base)
        second_paths = build_backup_material_paths(second, base=self.base)
        self.assertEqual(
            {member.target: first_paths[member.object_id]
             for member in first.objects},
            {member.target: second_paths[member.object_id]
             for member in second.objects},
        )


@unittest.skipUnless(os.name == "nt", "真实材料发布链路只支持项目Windows环境")
class BusinessLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / "project"
        self.root.mkdir()
        self.target = self.root / "sample.txt"
        self.target.write_bytes(b"original")
        self.backups = self.home / "backups"

    def test_patch_and_active_restore_use_same_material_position(self):
        preview = preview_patch(
            None, str(self.root), patch_text("changed"),
            backup_dir=str(self.backups),
            normalize_text_newlines=False,
        )
        self.assertEqual(preview.failed_count, 0)
        initial_positions = material_by_target(preview.execution.protection)
        applied = apply_patch(preview)
        self.assertEqual(self.target.read_bytes(), b"changed")
        original_evidence = read_recovery_evidence(applied.backup_root)

        recovery = preview_backup_restore(
            applied.backup_root, backup_dir=str(self.backups),
            backup_project_root=str(self.root),
        )
        self.assertEqual(
            material_by_target(recovery.execution.protection),
            initial_positions,
        )
        self.assertEqual(initial_positions[self.target], "sample.txt")
        confirm_backup_restore(recovery)
        restored = apply_backup_restore(recovery)
        self.assertTrue(restored.complete_original_state)
        self.assertEqual(self.target.read_bytes(), b"original")

        secondary = read_recovery_evidence(restored.backup_root)
        entry = secondary.document.value["objects"][0]
        self.assertEqual(entry["target"], str(self.target))
        self.assertEqual(entry["material"], "sample.txt")
        self.assertEqual(
            secondary.materials[entry["id"]].source_bytes, b"changed",
        )
        self.assertEqual(
            original_evidence.document.value["objects"][0]["material"],
            "sample.txt",
        )

    def test_both_split_modes_forward_the_same_explicit_base(self):
        source = (
            "---第1个文件---\n"
            "sample.txt\n"
            "---以下是源代码---\n"
            "changed\n"
            "---源代码结束---\n"
        )
        positions = []
        for mode in ("directory", "in_place"):
            plan = prepare_restore(
                None, str(self.root),
                "---以下是源代码---", "---源代码结束---",
                "overwrite", source_text=source, target_mode=mode,
                backup_dir=str(self.backups),
            )
            task = RestoreTask(plan)
            positions.append(material_by_target(task.protection))
            task.invalidate()
        self.assertEqual(positions[0], positions[1])
        self.assertEqual(positions[0][self.target], "sample.txt")

    def test_split_backup_and_restore_preserve_material_and_target(self):
        source = (
            "---第1个文件---\n"
            "sample.txt\n"
            "---以下是源代码---\n"
            "changed\n"
            "---源代码结束---\n"
        )
        task = RestoreTask(prepare_restore(
            None, str(self.root),
            "---以下是源代码---", "---源代码结束---",
            "overwrite", source_text=source, target_mode="in_place",
            backup_dir=str(self.backups),
        ))
        split_positions = material_by_target(task.protection)
        result = task.execute()
        self.assertTrue(result.completed)
        self.assertEqual(self.target.read_bytes(), b"changed")
        evidence = read_recovery_evidence(result.backup_root)
        self.assertEqual(
            evidence.document.value["objects"][0]["material"], "sample.txt",
        )

        recovery = preview_backup_restore(
            result.backup_root, backup_dir=str(self.backups),
            backup_project_root=str(self.root),
        )
        self.assertEqual(
            material_by_target(recovery.execution.protection),
            split_positions,
        )
        confirm_backup_restore(recovery)
        restored = apply_backup_restore(recovery)
        self.assertTrue(restored.complete_original_state)
        self.assertEqual(self.target.read_bytes(), b"original")

    def test_external_restore_target_is_not_rebased_to_protection_root(self):
        preview = preview_patch(
            None, str(self.root), patch_text("changed"),
            backup_dir=str(self.backups),
            normalize_text_newlines=False,
        )
        self.assertEqual(preview.failed_count, 0)
        applied = apply_patch(preview)
        unrelated = self.home / "other_project"
        unrelated.mkdir()

        recovery = preview_backup_restore(
            applied.backup_root, backup_dir=str(self.backups),
            backup_project_root=str(unrelated),
        )
        self.assertEqual(
            material_by_target(recovery.execution.protection)[self.target],
            "project/sample.txt",
        )
        confirm_backup_restore(recovery)
        restored = apply_backup_restore(recovery)
        self.assertTrue(restored.complete_original_state)
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertFalse((unrelated / "sample.txt").exists())


if __name__ == "__main__":
    unittest.main()
