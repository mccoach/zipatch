# -*- coding: utf-8 -*-

"""共同任务顺序、唯一账本、条目事实及工作／辅助边界。"""

import tempfile
import unittest
from pathlib import Path

from core.file_observation import observe_file
from core.fixed_content_execution import FixedContentExecution
from core.fixed_task_execution import FixedTaskExecution, FixedTaskWork
from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)


class RecordedProtection:
    """只替换保护副作用，工作发布仍使用真实文件系统。"""

    def __init__(self, root, events, *, reject=False, reject_complete=False):
        self.task_root = root
        self.events = events
        self.reject = reject
        self.reject_complete = reject_complete
        self.failed = False

    def protect_all(self):
        self.events.append("protect")
        if self.reject:
            self.failed = True
            raise OSError("backup rejected")

    def begin(self, unit_id):
        self.events.append("started")

    def complete(self, unit_id, results):
        self.events.append("completed")
        if not results or not all(result.succeeded for result in results):
            raise ValueError("missing successful work")
        if self.reject_complete:
            self.failed = True
            raise OSError("completion fact rejected")

    def uncertain(self, unit_id, results):
        self.events.append("uncertain")


class FixedTaskExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "target.txt"
        self.events = []
        self.task = FixedTaskExecution()
        before = observe_file(self.target)
        projection = build_protection_projection((
            FixedUnit(("member",), (
                FixedEffect(before, state_from_bytes(b"complete")),
            )),
        ), forbidden_roots=())
        self.unit = projection.units[0]

    def tearDown(self):
        self.temporary.cleanup()

    def recheck(self):
        self.events.append("recheck")

    def stage(self, role, target, phase):
        pass

    def prepare(self, entry):
        def perform():
            self.events.append("perform")
            execution = FixedContentExecution(observe_file(self.target), b"complete")
            state = self.task.state
            actual = state.execute_active(
                execution,
                lambda prepared: prepared.execute(
                    recheck_context=self.recheck,
                    record_step=state.record_step,
                    stage=lambda target, phase: None,
                ),
                stage=lambda target, phase: None,
            )
            return (actual,)

        def accept(actuals):
            self.events.append("accept")
            self.assertEqual(actuals[0].path, self.target)

        return FixedTaskWork(self.unit, perform, accept, self.recheck)

    def execute(self, task, **changes):
        self.task = task
        arguments = {
            "protection": None,
            "initial_recheck": self.recheck,
            "prepare_entry": self.prepare,
            "stage": self.stage,
            "entry_key": lambda entry: entry,
            "task_target": self.root,
        }
        arguments.update(changes)
        return task.execute(("entry",), **arguments)

    def test_protection_and_started_precede_real_work(self):
        task = FixedTaskExecution()
        protection = RecordedProtection(self.root / "backup", self.events)
        self.execute(task, protection=protection)
        self.assertLess(self.events.index("protect"), self.events.index("started"))
        self.assertLess(self.events.index("started"), self.events.index("perform"))
        self.assertLess(self.events.index("accept"), self.events.index("completed"))
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertTrue(task.work_completed)
        self.assertEqual(task.lifecycle, "completed")
        result = task.entry_result("entry")
        self.assertTrue(result.work_completed)
        self.assertEqual(result.status, "completed")
        self.assertIs(result.results[0], task.state.results[0])
        with self.assertRaises(ValueError):
            self.execute(task)

    def test_backup_failure_does_not_start_work(self):
        task = FixedTaskExecution()
        protection = RecordedProtection(
            self.root / "backup", self.events, reject=True,
        )
        with self.assertRaisesRegex(OSError, "backup rejected"):
            self.execute(task, protection=protection)
        self.assertFalse(self.target.exists())
        self.assertNotIn("perform", self.events)
        self.assertFalse(task.work_completed)
        self.assertEqual(task.lifecycle, "failed")

    def test_auxiliary_failure_preserves_work_completion(self):
        task = FixedTaskExecution()

        def auxiliary():
            raise OSError("auxiliary rejected")

        with self.assertRaisesRegex(OSError, "auxiliary rejected"):
            self.execute(task, auxiliary=auxiliary)
        self.assertTrue(task.work_completed)
        self.assertEqual(task.lifecycle, "failed")
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertEqual(len(task.state.results), 1)
        self.assertEqual(task.entry_result("entry").status, "completed")

    def test_final_check_failure_does_not_confirm_work(self):
        task = FixedTaskExecution()

        def reject():
            raise ValueError("final rejected")

        with self.assertRaisesRegex(ValueError, "final rejected"):
            self.execute(task, final_check=reject)
        self.assertFalse(task.work_completed)
        self.assertEqual(task.lifecycle, "failed")
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertTrue(task.entry_result("entry").work_completed)

    def test_completion_fact_failure_preserves_unit_work(self):
        task = FixedTaskExecution()
        protection = RecordedProtection(
            self.root / "backup", self.events, reject_complete=True,
        )
        with self.assertRaisesRegex(OSError, "completion fact rejected"):
            self.execute(task, protection=protection)
        result = task.entry_result("entry")
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.work_completed)
        self.assertEqual(len(result.results), 1)
        self.assertIs(result.results[0], task.state.results[0])
        self.assertFalse(task.work_completed)

    def test_cancel_consumes_qualification_without_work(self):
        task = FixedTaskExecution()
        self.assertTrue(task.invalidate())
        self.assertFalse(task.invalidate())
        with self.assertRaises(ValueError):
            self.execute(task)
        self.assertFalse(self.target.exists())
        self.assertEqual(task.lifecycle, "cancelled")

    def test_fixed_skip_does_not_publish_steps(self):
        task = FixedTaskExecution()
        self.execute(task, prepare_entry=lambda entry: None)
        self.assertEqual(task.state.results, ())
        self.assertFalse(self.target.exists())
        self.assertTrue(task.work_completed)
        self.assertEqual(task.entry_result("entry").status, "skipped")
        self.assertFalse(task.entry_result("entry").work_completed)

    def test_program_defect_propagates_and_invalidates_task(self):
        task = FixedTaskExecution()

        def defect(entry):
            raise RuntimeError("program defect")

        with self.assertRaisesRegex(RuntimeError, "program defect"):
            self.execute(task, prepare_entry=defect)
        self.assertTrue(task.consumed)
        self.assertEqual(task.lifecycle, "failed")
        self.assertFalse(task.work_completed)
        self.assertEqual(task.entry_result("entry").status, "failed")
        self.assertIsNone(task.entry_result("entry").error)

    def test_failed_entry_stops_following_entries(self):
        task = FixedTaskExecution()
        visited = []

        def prepare(entry):
            visited.append(entry)
            raise ValueError("entry rejected")

        with self.assertRaisesRegex(ValueError, "entry rejected"):
            task.execute(
                ("first", "second"), protection=None,
                initial_recheck=self.recheck, prepare_entry=prepare,
                entry_key=lambda entry: entry, stage=self.stage,
            )
        self.assertEqual(visited, ["first"])
        self.assertEqual(task.current_entry, "first")
        self.assertEqual(task.entry_result("first").status, "failed")
        self.assertEqual(task.entry_result("second").status, "pending")


if __name__ == "__main__":
    unittest.main()