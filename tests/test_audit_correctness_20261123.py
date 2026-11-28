# -*- coding: utf-8 -*-

"""本轮配置失败、合并遗漏和名称移动收口的定向回归。"""

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core.config import ConfigSaveManager, SaveResult, SaveStatus
from core.constants import DEFAULT_CONFIG
from core.file_observation import FileObservation, ObjectIdentity
from core.link_paths import LinkPathObservation
from core.name_moves import (
    NameMovePlan, PreparedNameMove, _execute_native_name,
    execute_observed_name_move, FixedNameMoveError,
)
from core.execution_ledger import ExecutionLedger
from core.filesystem_changes import tree_execution_results
from services.merge_service import write_merge_file
from services.source_recheck import recheck_source_paths


def make_access(path, *, kind="file", directories=(), followed_links=()):
    observed = object.__new__(LinkPathObservation)
    for name, value in (
        ("nominal_path", path),
        ("actual_path", path),
        ("kind", kind),
        ("object_identity", ObjectIdentity(1, 2)),
        ("followed_links", tuple(followed_links)),
        ("entry_link", None),
        ("directories", tuple(directories)),
        ("first_missing", None),
        ("follow_final_link", False),
    ):
        object.__setattr__(observed, name, value)
    return observed


class ConfigFailureTests(unittest.TestCase):
    def test_initial_observation_failure_forces_republication_of_old_value(self):
        with TemporaryDirectory() as folder:
            config = deepcopy(DEFAULT_CONFIG)
            manager = ConfigSaveManager(
                config, path=Path(folder) / "config.json",
            )
            old_value = config["scan"]["output_path"]
            manager.accept(
                config["scan"], {"output_path": str(Path(folder) / "changed.txt")},
            )
            with patch(
                "core.config.observe_file",
                side_effect=PermissionError("initial observation denied"),
            ):
                failed = manager.save()

            self.assertIs(failed.status, SaveStatus.FAILED)
            self.assertTrue(manager.required_write)
            self.assertIn("旧快照不再提供免写资格", failed.failure_description)
            manager.accept(
                config["scan"], {"output_path": old_value},
            )
            with patch.object(
                manager, "_write_snapshot",
                return_value=SaveResult(SaveStatus.SAVED),
            ) as publish:
                result = manager.save()
            self.assertIs(result.status, SaveStatus.SAVED)
            publish.assert_called_once()


class SourceOmissionTests(unittest.TestCase):
    def test_omission_keeps_directory_and_link_dependencies(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "missing.py"
            directory = SimpleNamespace(path=Path(folder))
            definition = SimpleNamespace(
                entry_path=Path(folder) / "route",
            )
            access = make_access(
                path, directories=(directory,),
                followed_links=(definition,),
            )
            with (
                patch(
                    "core.link_paths._confirm_directory",
                ) as check_directory,
                patch(
                    "core.link_paths._confirm_link",
                ) as check_link,
                patch(
                    "core.link_paths._attributes_or_absent",
                ) as check_target,
            ):
                recheck_source_paths(
                    (access,), omitted_targets=(path,),
                )
            check_directory.assert_called_once_with(directory)
            check_link.assert_called_once_with(definition)
            check_target.assert_not_called()

    def test_omission_does_not_ignore_link_dependency_drift(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "missing.py"
            definition = SimpleNamespace(
                entry_path=Path(folder) / "route",
            )
            access = make_access(path, followed_links=(definition,))
            with patch(
                "core.link_paths._confirm_link",
                side_effect=ValueError("fixed link changed"),
            ):
                with self.assertRaisesRegex(ValueError, "fixed link changed"):
                    recheck_source_paths(
                        (access,), omitted_targets=(path,),
                    )

    def test_non_omitted_source_still_requires_original_target(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "missing.py"
            access = make_access(path)
            with patch(
                "core.link_paths._attributes_or_absent",
                return_value=None,
            ):
                with self.assertRaises(ValueError):
                    recheck_source_paths((access,))

    def test_structure_cannot_use_content_omission(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            access = make_access(path, kind="directory")
            with self.assertRaisesRegex(ValueError, "普通文件"):
                recheck_source_paths(
                    (access,), omitted_targets=(path,),
                )

    def test_merge_read_failure_projects_omission_to_final_recheck(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "source.py"
            output = Path(folder) / "merged.txt"
            access = make_access(source)
            expected = FileObservation(
                output, (), "absent",
                None, None, None, None, None, None,
            )

            def publish(expected_output, chunks, *, recheck_context):
                b"".join(chunks)
                recheck_context()
                return SimpleNamespace(target=expected_output.path)

            with (
                patch("core.link_paths.recheck_link_path"),
                patch(
                    "services.merge_service.read_text_content_for_merge",
                    side_effect=FileNotFoundError("source disappeared"),
                ),
                patch(
                    "services.source_recheck.recheck_source_paths",
                ) as recheck,
                patch(
                    "services.merge_service.publish_stream_output",
                    side_effect=publish,
                ),
            ):
                result = write_merge_file(
                    ({"path": str(source), "merge_content": True,
                      "access": access},),
                    expected, "", "", "--start--", "--end--",
                    record_file_mtime=False,
                )
            self.assertEqual(result.read_failure_count, 1)
            self.assertEqual(result.total_files, 0)
            recheck.assert_called_once_with(
                (access,), omitted_targets=(source,),
            )


class NativeMoveTests(unittest.TestCase):
    def test_native_step_returns_verified_observation_once(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "source.py"
            target = Path(folder) / "target.py"
            plan = NameMovePlan(
                source, target, "file", (1, 2),
                None, None, (), 1, "absent",
            )
            move = SimpleNamespace(
                _current=None, _phase="prepared", phase="prepared",
            )
            original = SimpleNamespace(path=source)
            relocated = object()
            actual = object()
            with (
                patch(
                    "core.name_moves._recheck_move_object",
                    side_effect=(original, actual),
                ),
                patch("core.name_moves.recheck_name_move"),
                patch(
                    "core.name_moves.checked_chain",
                    return_value=(target.parent, ((target.parent, "directory"),)),
                ),
                patch("core.name_moves.os.replace") as rename,
                patch("core.name_moves.check_native_move_result"),
                patch(
                    "core.name_moves._relocated_move_source",
                    return_value=relocated,
                ) as locate,
                patch("core.name_moves._append_move_results") as record,
            ):
                result = _execute_native_name(move, original, plan)
            self.assertIs(result, actual)
            locate.assert_called_once_with(original, target)
            rename.assert_called_once_with(source, target)
            self.assertTrue(record.call_args.args[1][0].succeeded)

    def test_final_source_check_failure_uses_formal_move_error(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "source.py"
            target = Path(folder) / "target.py"
            source_observation = FileObservation(
                source, ((source, "file"),), "file",
                None, ObjectIdentity(1, 2), 1,
                b"old", None, None,
            )
            target_observation = FileObservation(
                target, ((target, "absent"),), "absent",
                None, None, None, None, None, None,
            )
            plan = NameMovePlan(
                source, target, "file", (1, 2),
                None, None, (), 1, "absent",
            )
            move = object.__new__(PreparedNameMove)
            for name, value in (
                ("source", source_observation),
                ("expected", target_observation),
                ("plan", plan),
                ("mechanism", "native"),
                ("_lifecycle", "prepared"),
                ("_execution", ExecutionLedger(tree_execution_results)),
                ("_current", None),
                ("_phase", "prepared"),
                ("_artifact", None),
                ("_final_observations", ()),
            ):
                object.__setattr__(move, name, value)

            with (
                patch(
                    "core.name_moves._recheck_move_object",
                    side_effect=lambda observed: observed,
                ),
                patch("core.name_moves.recheck_name_move"),
                patch(
                    "core.name_moves.checked_chain",
                    return_value=(target.parent, ((target.parent, "directory"),)),
                ),
                patch(
                    "core.name_moves._execute_native_name",
                    return_value=target_observation,
                ),
                patch(
                    "core.name_moves.recheck_file",
                    side_effect=ValueError("source unexpectedly reappeared"),
                ),
            ):
                with self.assertRaises(FixedNameMoveError) as raised:
                    execute_observed_name_move(move)
            self.assertEqual(
                raised.exception.result.phase,
                "名称组合结束来源不存在复核",
            )
            self.assertEqual(move.final_observations, ())
            self.assertEqual(move._lifecycle, "attempted")


if __name__ == "__main__":
    unittest.main()