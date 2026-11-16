# -*- coding: utf-8 -*-

"""真实文件对象上的任务正常变化及自动回滚授权边界。"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.automatic_rollback_scope import validate_automatic_rollback_scope
from core.content_publication import prepare_content_artifact, publish_content
from core.file_observation import observe_file, observe_tree
from core.filesystem_changes import (
    execute_filesystem_change, prepare_directory_creation, prepare_file_removal,
)
from core.protection_projection import (
    FixedEffect, FixedUnit, absent_state, build_protection_projection,
    state_from_bytes,
)
from core.task_observation import TaskObservationState


def fixed_file(path):
    return observe_file(
        path, include_bytes=True, include_digest=True, include_mtime=True,
    )


def projection_for(*effects):
    return build_protection_projection(
        tuple(
            FixedUnit((f"member{number}",), (effect,))
            for number, effect in enumerate(effects, 1)
        ),
        forbidden_roots=(),
    )


def current_actions(projection):
    return tuple(
        SimpleNamespace(
            target=member.target,
            current=(
                observe_tree(member.target, include_bytes=True, include_mtime=True)
                if member.target.is_dir() else fixed_file(member.target)
            ),
        )
        for member in projection.objects
    )


class TaskStateAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_normal_hardlink_content_is_shared_without_rebuilding_finished(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"original")
        os.link(first, second)
        before_first, before_second = fixed_file(first), fixed_file(second)
        state = TaskObservationState((before_first, before_second))
        receipt = publish_content(prepare_content_artifact(before_first, b"changed"))
        state.accept((receipt.observation,), (receipt,))
        projected = state.expected(before_second, (receipt,))
        self.assertEqual(projected.digest, receipt.observation.digest)
        self.assertEqual(projected.object_identity, before_second.object_identity)
        self.assertEqual(projected.link_count, 2)
        self.assertIsNone(projected.source_bytes)
        self.assertEqual(len(state.finished), 1)

    def test_name_removed_once_even_after_same_name_is_rebuilt(self):
        first = self.root / "first.txt"
        alias = self.root / "alias.txt"
        first.write_bytes(b"original")
        os.link(first, alias)
        before, before_alias = fixed_file(first), fixed_file(alias)
        state = TaskObservationState((before, before_alias))
        removed = execute_filesystem_change(prepare_file_removal(before))
        published = publish_content(
            prepare_content_artifact(observe_file(first), b"rebuilt"),
        )
        results = (removed, published)
        self.assertEqual(state.removed_counts(results)[before.object_identity], 1)
        self.assertEqual(state.removed_counts(results)[before.object_identity], 1)
        projected = state.project(before_alias, results)
        self.assertEqual(projected.link_count, 1)
        self.assertEqual(projected.source_bytes, b"original")
        with self.assertRaises(ValueError):
            state.consume_results((published, removed))

    def test_normal_publication_is_authorized_by_its_actual_identity(self):
        target = self.root / "target.txt"
        target.write_bytes(b"original")
        before = fixed_file(target)
        projection = projection_for(FixedEffect(before, state_from_bytes(b"changed")))
        receipt = publish_content(prepare_content_artifact(before, b"changed"))
        validate_automatic_rollback_scope(
            projection, current_actions(projection), results=(receipt,),
        )
        replacement = self.root / "external.txt"
        replacement.write_bytes(b"changed")
        os.replace(replacement, target)
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(
                projection, current_actions(projection), results=(receipt,),
            )
        self.assertEqual(target.read_bytes(), b"changed")

    def test_external_change_to_unattempted_object_is_not_rollback_authorized(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        before_first, before_second = fixed_file(first), fixed_file(second)
        projection = projection_for(
            FixedEffect(before_first, state_from_bytes(b"changed")),
            FixedEffect(before_second, state_from_bytes(b"planned")),
        )
        receipt = publish_content(prepare_content_artifact(before_first, b"changed"))
        second.write_bytes(b"external")
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(
                projection, current_actions(projection), results=(receipt,),
            )
        self.assertEqual(second.read_bytes(), b"external")

    def test_created_parent_does_not_authorize_external_child(self):
        target = self.root / "new" / "target.txt"
        before = fixed_file(target)
        projection = projection_for(FixedEffect(before, state_from_bytes(b"new")))
        directory = execute_filesystem_change(
            prepare_directory_creation(observe_file(target.parent)),
        )
        publication = publish_content(
            prepare_content_artifact(observe_file(target), b"new"),
        )
        results = (directory, publication)
        validate_automatic_rollback_scope(
            projection, current_actions(projection), results=results,
        )
        external = target.parent / "external.txt"
        external.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(
                projection, current_actions(projection), results=results,
            )
        self.assertEqual(external.read_bytes(), b"keep")

    def test_original_absence_requires_actual_removal_attempt(self):
        target = self.root / "target.txt"
        target.write_bytes(b"original")
        before = fixed_file(target)
        projection = projection_for(FixedEffect(before, absent_state()))
        receipt = execute_filesystem_change(prepare_file_removal(before))
        actions = current_actions(projection)
        with self.assertRaises(ValueError):
            validate_automatic_rollback_scope(projection, actions, results=())
        validate_automatic_rollback_scope(projection, actions, results=(receipt,))

    def test_shared_write_authorizes_linked_original_names(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"original")
        os.link(first, second)
        before_first, before_second = fixed_file(first), fixed_file(second)
        projection = projection_for(
            FixedEffect(before_first, state_from_bytes(b"changed")),
            FixedEffect(before_second, state_from_bytes(b"changed")),
        )
        receipt = publish_content(prepare_content_artifact(before_first, b"changed"))
        validate_automatic_rollback_scope(
            projection, current_actions(projection), results=(receipt,),
        )


if __name__ == "__main__":
    unittest.main()
