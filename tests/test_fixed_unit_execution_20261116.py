# -*- coding: utf-8 -*-

"""共用单元主链、正式步骤及自动回滚范围；仅使用临时目录。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core.automatic_rollback_scope import validate_automatic_rollback_scope
from core.content_publication import (
    prepare_content_artifact, publish_content,
)
from core.file_observation import observe_file, observe_tree
from core.fixed_unit_execution import FixedUnitExecution
from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)


class FixedUnitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.target = self.home / "new" / "nested" / "sample.txt"
        self.before = observe_file(
            self.target, include_bytes=True, include_digest=True,
            include_mtime=True,
        )
        self.projection = build_protection_projection((
            FixedUnit(
                ("sample",),
                (FixedEffect(self.before, state_from_bytes(b"desired")),),
            ),
        ), forbidden_roots=())
        from core.execution_state import ExecutionState
        self.state = ExecutionState()
        self.events = []

    @property
    def steps(self):
        return self.state.results

    @property
    def created(self):
        return self.state.created_directories

    def make_execution(self, protection=None):
        return FixedUnitExecution(
            self.projection.units[0], protection=protection,
            state=self.state,
            recheck=lambda: self.events.append("recheck"),
            stage=lambda role, target, phase:
            self.events.append((role, target, phase)),
        )

    def perform(self):
        self.events.append("perform")
        artifact = prepare_content_artifact(self.before, b"desired")
        self.state.record_step(publish_content(artifact))
        return (observe_file(self.target, include_digest=True),)

    def test_real_parent_creation_publication_and_one_shot(self):
        execution = self.make_execution()
        accepted = []
        execution.execute(self.perform, accepted.append)
        self.assertTrue(execution.completed)
        self.assertEqual(self.target.read_bytes(), b"desired")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(
            [step.kind for step in self.steps[:-1]],
            ["create_directory", "create_directory"],
        )
        self.assertEqual(len(self.created), 2)
        with self.assertRaises(ValueError):
            execution.execute(self.perform, accepted.append)

    def test_started_precedes_directory_mutation_and_complete_follows_accept(self):
        class Protection:
            task_root = self.home / "backup"
            failed = False

            def begin(inner, unit_id):
                self.assertFalse(self.target.parent.exists())
                self.events.append("begin")

            def complete(inner, unit_id, results):
                self.assertEqual(self.events[-2], "accept")
                self.assertTrue(all(receipt.succeeded for receipt in results))
                self.events.append("complete")

            def uncertain(inner, unit_id, results):
                self.fail("正常任务不得登记不确定事实")

        def accept(actuals):
            self.events.append("accept")

        execution = self.make_execution(Protection())
        execution.execute(self.perform, accept)
        self.assertLess(
            self.events.index("begin"), self.events.index("perform"),
        )
        self.assertLess(
            self.events.index("accept"), self.events.index("complete"),
        )

    def test_begin_failure_has_no_directory_creation_or_action(self):
        class Protection:
            task_root = self.home / "backup"
            failed = True

            def begin(inner, unit_id):
                raise PermissionError("started denied")

            def uncertain(inner, unit_id, results):
                self.fail("开始事实失败不能登记另一份开始后的事实")

        execution = self.make_execution(Protection())
        with self.assertRaises(PermissionError):
            execution.execute(self.perform, lambda actuals: None)
        self.assertFalse(self.target.parent.exists())
        self.assertFalse(self.steps)
        self.assertNotIn("perform", self.events)

    def test_wrong_effect_result_is_not_accepted(self):
        execution = self.make_execution()
        accepted = []

        def perform():
            self.target.write_bytes(b"wrong")
            return (observe_file(self.target, include_digest=True),)

        with self.assertRaises(OSError):
            execution.execute(perform, accepted.append)
        self.assertFalse(accepted)
        self.assertFalse(execution.completed)

    def test_failure_records_actual_prefix_once_and_never_retries(self):
        uncertain = []

        class Protection:
            task_root = self.home / "backup"
            failed = False

            def begin(inner, unit_id):
                pass

            def uncertain(inner, unit_id, results):
                uncertain.append(results)

            def complete(inner, unit_id, results):
                self.fail("失败动作不能登记完成")

        execution = self.make_execution(Protection())

        def perform():
            raise PermissionError("work denied")

        with self.assertRaises(PermissionError):
            execution.execute(perform, lambda actuals: None)
        self.assertEqual(len(uncertain), 1)
        self.assertEqual(uncertain[0], tuple(self.steps))
        self.assertEqual(len(self.steps), 2)
        with self.assertRaises(ValueError):
            execution.execute(perform, lambda actuals: None)
        self.assertEqual(len(uncertain), 1)

    def test_directory_creation_failure_preserves_formal_failure_result(self):
        execution = self.make_execution()
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            with self.assertRaises(OSError):
                execution.execute(self.perform, lambda actuals: None)
        self.assertEqual(len(self.steps), 1)
        self.assertFalse(self.steps[0].succeeded)
        self.assertTrue(self.steps[0].attempted)
        self.assertFalse(self.created)

    def test_automatic_rollback_rejects_extra_directory_members(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"partial")
        extra = self.home / "new" / "external.txt"
        extra.write_bytes(b"not authorized")
        protected = self.projection.objects[0]
        actions = (SimpleNamespace(
            target=protected.target,
            current=observe_tree(
                protected.target, include_bytes=True, include_mtime=True,
            ),
        ),)
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(
                self.projection, actions, results=(),
            )
        self.assertEqual(extra.read_bytes(), b"not authorized")

    def test_automatic_rollback_accepts_actual_planned_directory_results(self):
        self.make_execution().execute(self.perform, lambda actuals: None)
        protected = self.projection.objects[0]
        actions = (SimpleNamespace(
            target=protected.target,
            current=observe_tree(
                protected.target, include_bytes=True, include_mtime=True,
            ),
        ),)
        validate_automatic_rollback_scope(
            self.projection, actions, results=self.state.results,
        )
        self.assertEqual(self.target.read_bytes(), b"desired")


if __name__ == "__main__":
    unittest.main()
