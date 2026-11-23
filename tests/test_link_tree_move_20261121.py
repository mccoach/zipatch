# -*- coding: utf-8 -*-

"""含链接混合树原生移动、正式保护事实及失败不切换机制验证。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file, observe_tree, recheck_tree
from core.filesystem_changes import (
    execute_filesystem_change, prepare_link_creation,
)
from core.fixed_task_execution import FixedTaskExecution, FixedTaskWork
from core.link_paths import LinkSpecification, specification_from_link
from core.name_moves import (
    FixedNameMoveError, execute_observed_name_move,
    prepare_observed_name_move,
)
from core.protection_projection import (
    FixedEffect, FixedUnit, absent_state, build_protection_projection,
    state_from_observation,
)
from core.recovery_protection import (
    RecoveryProtectionSession, read_recovery_evidence,
)


@unittest.skipUnless(os.name == "nt", "混合树移动使用Windows")
class LinkTreeMoveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_link_tree_move_",
        )
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.target = self.root / "moved"
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "private.bin").write_bytes(b"outside")
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

    def symlink(self, entry, raw, directory=False):
        os.symlink(raw, entry, target_is_directory=directory)
        self.links.append((entry, directory))

    def observed_source(self):
        return observe_tree(
            self.source, include_bytes=True, include_mtime=True,
            include_links=True,
        )

    def test_formal_protected_native_move_preserves_mixed_tree(self):
        nested = self.source / "nested"
        nested.mkdir()
        (self.source / "empty").mkdir()
        body = nested / "body.bin"
        body.write_bytes(b"\x00body")
        modified = 1700000000000000000
        os.utime(body, ns=(modified, modified))
        peer = self.root / "peer.bin"
        os.link(body, peer)
        self.symlink(self.source / "middle", self.outside / "private.bin")
        self.symlink(self.source / "first", "middle")
        self.symlink(nested / "dangling", "missing")
        self.symlink(self.source / "folder", self.outside, True)
        junction = self.source / "joined"
        receipt = execute_filesystem_change(prepare_link_creation(
            observe_file(junction),
            LinkSpecification(
                "junction", "\\\\?\\" + str(self.outside), False,
            ),
        ))
        self.assertTrue(receipt.succeeded)
        self.links.append((junction, True))
        source = self.observed_source()
        expected = observe_file(self.target)
        desired = state_from_observation(source)
        move = prepare_observed_name_move(source, expected)
        self.assertEqual(move.mechanism, "native")
        projection = build_protection_projection(
            (FixedUnit(
                ("move001",),
                (
                    FixedEffect(source, absent_state()),
                    FixedEffect(expected, desired),
                ),
            ),),
            forbidden_roots=(),
        )
        protection = RecoveryProtectionSession(
            projection, self.backups / "task",
            "mixed-move", "patch", material_base=self.root,
        )
        task = FixedTaskExecution()
        for relative, member in source.links:
            self.links.append((
                self.target / relative, member.kind != "file_symlink",
            ))
        original_open = Path.open

        def guarded_open(path, *args, **keywords):
            if path == self.outside or self.outside in path.parents:
                raise AssertionError("混合树移动不得读取根外链接目标")
            return original_open(path, *args, **keywords)

        def perform():
            task.state.execute_active(
                move, execute_observed_name_move,
                stage=lambda position, phase: None,
            )
            return move.final_observations

        with patch("pathlib.Path.open", new=guarded_open):
            task.execute(
                (projection.units[0],),
                protection=protection,
                initial_recheck=lambda: (
                    recheck_tree(source, check_mtime=True),
                    observe_file(self.target),
                ),
                prepare_entry=lambda unit: FixedTaskWork(
                    unit, perform,
                    lambda actuals: None,
                    lambda: recheck_tree(source, check_mtime=True),
                ),
                stage=lambda role, position, phase: None,
                entry_key=lambda unit: unit.unit_id,
            )
        self.assertTrue(task.work_completed)
        self.assertTrue(protection.backup_completed)
        self.assertFalse(os.path.lexists(self.source))
        absent, actual = move.final_observations
        self.assertEqual(absent.kind, "absent")
        self.assertEqual(actual.path, self.target)
        self.assertEqual(actual.content_records, desired.records)
        self.assertEqual(actual.file_times, source.file_times)
        originals = dict(source.links)
        for relative, member in actual.links:
            self.assertEqual(member.path, self.target / relative)
            self.assertEqual(
                member.object_identity, originals[relative].object_identity,
            )
            self.assertEqual(
                specification_from_link(member.entry_link),
                specification_from_link(originals[relative].entry_link),
            )
        self.assertEqual(
            dict(actual.links)["first"].entry_link.direct_target,
            self.target / "middle",
        )
        self.assertEqual(
            dict(actual.files)["nested/body.bin"].object_identity,
            dict(source.files)["nested/body.bin"].object_identity,
        )
        self.assertEqual((self.target / "nested" / "body.bin").lstat().st_nlink, 2)
        self.assertEqual(peer.read_bytes(), b"\x00body")
        self.assertEqual((self.outside / "private.bin").read_bytes(), b"outside")
        self.assertFalse((self.target / "nested" / "missing").exists())
        evidence = read_recovery_evidence(protection.task_root)
        fact = evidence.document.value["units"][0]["fact"]
        self.assertEqual(fact["state"], "completed")
        self.assertEqual(len(fact["changes"]), 1)
        self.assertEqual(fact["changes"][0]["kind"], "move_name")
        self.assertEqual(len(task.state.results), 1)
        recheck_tree(actual, check_mtime=True)
        with self.assertRaises(ValueError):
            execute_observed_name_move(move)

    def test_retargeted_link_stops_before_native_name_call(self):
        entry = self.source / "alias"
        self.symlink(entry, "first")
        source = self.observed_source()
        move = prepare_observed_name_move(source, observe_file(self.target))
        entry.unlink()
        os.symlink("second", entry)
        with patch("core.name_moves.os.rename") as rename:
            with self.assertRaises(FixedNameMoveError):
                execute_observed_name_move(move)
            rename.assert_not_called()
        self.assertEqual(move.results, ())
        self.assertTrue(self.source.is_dir())
        self.assertFalse(os.path.lexists(self.target))
        self.assertEqual(os.readlink(entry), "second")

    def test_native_failure_never_switches_to_copy_remove(self):
        self.symlink(self.source / "alias", "missing")
        source = self.observed_source()
        move = prepare_observed_name_move(source, observe_file(self.target))
        with patch(
            "core.name_moves.os.rename",
            side_effect=OSError("injected native move failure"),
        ) as rename, patch(
            "core.name_moves.execute_tree_copy",
            side_effect=AssertionError("原生失败不得切换复制删除"),
        ) as copy:
            with self.assertRaises(FixedNameMoveError) as caught:
                execute_observed_name_move(move)
            rename.assert_called_once_with(self.source, self.target)
            copy.assert_not_called()
        self.assertEqual(len(caught.exception.result.results), 1)
        self.assertTrue(move.results[0].attempted)
        self.assertFalse(move.results[0].succeeded)
        self.assertEqual(move.final_observations, ())
        self.assertFalse(os.path.lexists(self.target))
        recheck_tree(source, check_mtime=True)
        with self.assertRaises(ValueError):
            execute_observed_name_move(move)


if __name__ == "__main__":
    unittest.main()
