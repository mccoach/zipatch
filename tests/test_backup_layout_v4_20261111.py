# -*- coding: utf-8 -*-

"""直观备份布局、严格version 4及真实Windows材料恢复回归。"""

import copy
import os
import tempfile
import unittest
from pathlib import Path

from core.backup_material_layout import build_backup_material_paths
from core.file_observation import observe_file, observe_tree
from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)
from core.recovery_document import VERSION, validate_document
from core.recovery_protection import (
    RecoveryProtectionSession, read_recovery_evidence,
)


def plain(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return {key: plain(member) for key, member in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(member) for member in value]
    return value


@unittest.skipUnless(os.name == "nt", "本项目材料名称提交要求Windows")
class BackupLayoutV4Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def projection(self, paths):
        units = []
        for number, path in enumerate(paths, 1):
            if path.is_dir():
                observed = observe_tree(
                    path, include_bytes=True, include_mtime=True,
                )
            else:
                observed = observe_file(
                    path, include_bytes=True, include_digest=True,
                    include_mtime=True,
                )
            units.append(FixedUnit(
                (f"member{number}",),
                (FixedEffect(observed, state_from_bytes(b"new")),),
            ))
        return build_protection_projection(units, forbidden_roots=())

    def test_explicit_root_preserves_original_relative_names(self):
        first = self.project / "src" / "same.txt"
        second = self.project / "docs" / "same.txt"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"src-original")
        second.write_bytes(b"docs-original")
        projection = self.projection((first, second))
        paths = build_backup_material_paths(projection, base=self.project)
        self.assertEqual(
            set(paths.values()), {"src/same.txt", "docs/same.txt"},
        )

    def test_automatic_layout_removes_common_parent(self):
        first = self.project / "src" / "a.txt"
        second = self.project / "docs" / "b.txt"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        paths = build_backup_material_paths(self.projection((first, second)))
        prefix = self.project.drive[0].upper()
        self.assertEqual(
            set(paths.values()), {prefix + "/src/a.txt", prefix + "/docs/b.txt"},
        )

    def test_directory_object_keeps_its_own_name(self):
        folder = self.project / "src"
        folder.mkdir()
        (folder / "a.txt").write_bytes(b"a")
        paths = build_backup_material_paths(
            self.projection((folder,)), base=self.project,
        )
        self.assertEqual(tuple(paths.values()), ("src",))

    def test_absent_protection_root_keeps_directory_name(self):
        missing = self.project / "new-folder"
        paths = build_backup_material_paths(
            self.projection((missing / "a.txt",)), base=missing,
        )
        self.assertEqual(tuple(paths.values()), ("new-folder",))

    def test_other_volume_does_not_deepen_first_group(self):
        # 纯布局测试不访问第二盘符；只改变已校验投影的路径用于算法输入。
        from dataclasses import replace
        first = self.project / "src" / "a.txt"
        second = self.project / "docs" / "b.txt"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        projection = self.projection((first, second))
        other_drive = "D" if self.project.drive[0].upper() != "D" else "E"
        extra = replace(
            projection.objects[0],
            object_id="o999999",
            before=replace(
                projection.objects[0].before,
                path=Path(other_drive + ":/lonely.txt"),
            ),
        )
        candidate = object.__new__(type(projection))
        object.__setattr__(
            candidate, "objects", (*projection.objects, extra),
        )
        object.__setattr__(candidate, "units", projection.units)
        object.__setattr__(
            candidate, "forbidden_roots", projection.forbidden_roots,
        )
        paths = build_backup_material_paths(candidate)
        prefix = self.project.drive[0].upper()
        self.assertIn(prefix + "/src/a.txt", paths.values())
        self.assertIn(prefix + "/docs/b.txt", paths.values())
        self.assertEqual(paths["o999999"], other_drive + "/lonely.txt")

    def test_publication_document_and_evidence_use_original_names(self):
        first = self.project / "src" / "same.txt"
        second = self.project / "docs" / "same.txt"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"first-original")
        second.write_bytes(b"second-original")
        projection = self.projection((first, second))
        task = self.backups / "task"
        session = RecoveryProtectionSession(
            projection, task, "test-task", "patch",
            durable_publication=False, material_base=self.project,
        )
        session.protect_all()
        self.assertEqual((task / "files/src/same.txt").read_bytes(), b"first-original")
        self.assertEqual((task / "files/docs/same.txt").read_bytes(), b"second-original")
        evidence = read_recovery_evidence(task)
        self.assertEqual(evidence.document.value["version"], 4)
        entries = evidence.document.value["objects"]
        self.assertEqual(
            {entry["material"] for entry in entries},
            {"src/same.txt", "docs/same.txt"},
        )
        self.assertEqual(
            {entry["target"] for entry in entries},
            {str(first), str(second)},
        )
        self.assertEqual(
            {member.source_bytes for member in evidence.materials.values()},
            {b"first-original", b"second-original"},
        )
        previous = plain(evidence.document.value)
        previous["version"] = 3
        with self.assertRaises(ValueError):
            validate_document(previous)

        invalid = plain(evidence.document.value)
        invalid["objects"][0]["material"] = "../outside.txt"
        with self.assertRaises(ValueError):
            validate_document(invalid)

        duplicate = copy.deepcopy(plain(evidence.document.value))
        duplicate["objects"][1]["material"] = duplicate["objects"][0]["material"]
        with self.assertRaises(ValueError):
            validate_document(duplicate)

    def test_incremental_publication_keeps_first_material_position(self):
        first = self.project / "src" / "a.txt"
        second = self.project / "docs" / "b.txt"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        projection = self.projection((first, second))
        task = self.backups / "incremental"
        session = RecoveryProtectionSession(
            projection, task, "incremental", "patch",
            durable_publication=False, material_base=self.project,
        )
        session.protect(projection.units[0].unit_id)
        entries = session.document.value["objects"]
        self.assertEqual(len(entries), 1)
        before = plain(entries[0])
        identifier = before["id"]
        self.assertEqual(before["target"], str(first))
        self.assertEqual(before["material"], "src/a.txt")

        session.protect(projection.units[1].unit_id)
        after_by_id = {
            entry["id"]: plain(entry)
            for entry in session.document.value["objects"]
        }
        self.assertEqual(len(after_by_id), 2)
        # 数组顺序不是对象身份；同一ID的全部原事实必须保持。
        self.assertEqual(after_by_id[identifier], before)
        self.assertEqual(
            {entry["material"] for entry in after_by_id.values()},
            {"src/a.txt", "docs/b.txt"},
        )
        self.assertEqual((task / "files/src/a.txt").read_bytes(), b"a")
        self.assertEqual((task / "files/docs/b.txt").read_bytes(), b"b")

    def test_directory_material_is_read_as_original_tree(self):
        folder = self.project / "src"
        (folder / "nested").mkdir(parents=True)
        (folder / "nested/a.txt").write_bytes(b"tree-original")
        projection = self.projection((folder,))
        task = self.backups / "tree"
        session = RecoveryProtectionSession(
            projection, task, "tree", "patch",
            durable_publication=False, material_base=self.project,
        )
        session.protect_all()
        self.assertEqual(
            (task / "files/src/nested/a.txt").read_bytes(), b"tree-original",
        )
        evidence = read_recovery_evidence(task)
        material = evidence.materials[projection.objects[0].object_id]
        self.assertEqual(
            dict(material.files)["nested/a.txt"].source_bytes,
            b"tree-original",
        )

    def test_restore_reads_material_relative_path(self):
        from core.recovery_execution import prepare_recovery_execution
        from services.recovery import plan_restore
        from services.recovery_selection import (
            change_restore_decisions, initial_restore_decisions,
            selected_restore_plan,
        )
        target = self.project / "nested" / "original.txt"
        target.parent.mkdir()
        target.write_bytes(b"original")
        projection = self.projection((target,))
        task = self.backups / "restore"
        session = RecoveryProtectionSession(
            projection, task, "restore", "patch",
            durable_publication=False, material_base=self.project,
        )
        session.protect_all()
        target.write_bytes(b"changed")
        evidence = read_recovery_evidence(task)
        plan = plan_restore(self.backups, evidence)
        decisions = change_restore_decisions(
            plan, initial_restore_decisions(plan), (), "restore", scope="all",
        )
        selected = selected_restore_plan(plan, decisions)
        execution = prepare_recovery_execution(
            evidence, selected.units, kept=selected.kept,
            unchanged=selected.unchanged,
            forbidden_roots=(self.backups,), durable_publication=False,
        )
        result = execution.execute()
        self.assertTrue(result.succeeded)
        self.assertEqual(target.read_bytes(), b"original")


class RecoveryVersionTests(unittest.TestCase):
    def test_current_version_is_four(self):
        self.assertEqual(VERSION, 4)


if __name__ == "__main__":
    unittest.main()
