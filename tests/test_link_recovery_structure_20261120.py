# -*- coding: utf-8 -*-

"""严格原结构、普通材料对应和共同恢复重建消费者。"""

import copy
import os
import tempfile
import unittest
from pathlib import Path

from core.execution_state import ExecutionState
from core.file_observation import observe_file, observe_tree
from core.internal_materials import (
    prepare_internal_material, publish_internal_material,
    validate_material_state,
)
from core.link_paths import (
    inspect_link_path, specification_from_link,
)
from core.protection_projection import state_from_observation
from core.recovery_document import (
    _state_value, content_state_from_value,
)
from core.recovery_execution import _rebuild_structure


@unittest.skipUnless(os.name == "nt", "正式链接恢复使用Windows")
class LinkRecoveryStructureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_recovery_structure_",
        )
        self.root = Path(self.temporary.name)
        self.material_root = self.root / "materials"
        self.material_root.mkdir()
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, directory=False):
        try:
            os.symlink(target, entry, target_is_directory=directory)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("当前账户没有符号链接创建权限")
            raise
        self.links.append((entry, directory))
        return inspect_link_path(entry, follow_final_link=False)

    def material(self, observed):
        target = self.material_root / observed.path.name
        receipt = publish_internal_material(prepare_internal_material(
            observed, observe_file(target),
        ))
        self.assertTrue(receipt.succeeded)
        if receipt.kind == "directory":
            return observe_tree(
                target, include_bytes=True, include_mtime=True,
            )
        return observe_file(
            target, include_bytes=True, include_digest=True,
            include_mtime=True,
        )

    def test_strict_state_roundtrip_keeps_raw_definition(self):
        observed = self.link(self.root / "alias.txt", self.root / "missing.txt")
        state = state_from_observation(observed)
        value = _state_value(state)
        restored = content_state_from_value(value, target=observed.path)
        self.assertEqual(restored, state)
        self.assertEqual(
            restored.link_definitions[0][1].raw_target,
            os.readlink(observed.path),
        )
        for changed in ("missing", "mismatch", "unknown"):
            candidate = copy.deepcopy(value)
            if changed == "missing":
                del candidate["link_definitions"]
            elif changed == "mismatch":
                candidate["link_definitions"][0]["raw_target"] = "other.txt"
                candidate["link_definitions"][0]["relative"] = True
            else:
                candidate["link_definitions"][0]["extra"] = True
            with self.assertRaises(ValueError):
                content_state_from_value(candidate, target=observed.path)

    def test_root_link_rebuild_uses_saved_definition_not_json_content(self):
        observed = self.link(self.root / "alias.txt", "missing.txt")
        desired = state_from_observation(observed)
        source = self.material(observed)
        os.unlink(observed.path)
        state = ExecutionState()
        actual = _rebuild_structure(
            source, observe_file(observed.path), desired, (),
            state=state, recheck_context=lambda: None,
            stage=lambda position, phase: None,
        )
        self.assertEqual(
            specification_from_link(actual.entry_link),
            desired.link_definitions[0][1],
        )
        self.assertEqual(
            tuple(receipt.kind for receipt in state.results), ("create_link",),
        )
        self.assertFalse((self.root / "missing.txt").exists())
        self.assertTrue(source.path.is_file())
        self.assertFalse(source.path.is_symlink())

    def test_mixed_tree_rebuild_preserves_leaf_links_and_file_time(self):
        scope = self.root / "scope"
        scope.mkdir()
        body = scope / "body.bin"
        body.write_bytes(b"\x00body")
        os.utime(body, ns=(1700000000000000000, 1700000000000000000))
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"keep")
        self.link(scope / "folder_alias", outside, True)
        self.link(scope / "missing_alias.txt", "missing.txt")
        observed = observe_tree(
            scope, include_bytes=True, include_mtime=True, include_links=True,
        )
        desired = state_from_observation(observed)
        source = self.material(observed)
        rebuilt = self.root / "rebuilt"
        state = ExecutionState()
        actual = _rebuild_structure(
            source, observe_file(rebuilt), desired, observed.file_times,
            state=state, recheck_context=lambda: None,
            stage=lambda position, phase: None,
        )
        self.links.extend((
            (rebuilt / "folder_alias", True),
            (rebuilt / "missing_alias.txt", False),
        ))
        self.assertEqual(actual.content_records, desired.records)
        self.assertEqual(actual.file_times, observed.file_times)
        self.assertEqual(len(actual.links), 2)
        self.assertEqual((rebuilt / "body.bin").read_bytes(), b"\x00body")
        self.assertEqual((outside / "private.txt").read_bytes(), b"keep")
        self.assertEqual(
            {position for position, _ in source.files},
            {"body.bin", "folder_alias", "missing_alias.txt"},
        )

    def test_wrong_definition_material_is_rejected_before_creation(self):
        observed = self.link(self.root / "alias.txt", "missing.txt")
        desired = state_from_observation(observed)
        source = self.material(observed)
        source.path.write_bytes(b'{"not":"the saved definition"}')
        changed = observe_file(
            source.path, include_bytes=True, include_digest=True,
            include_mtime=True,
        )
        target = self.root / "rebuilt.txt"
        state = ExecutionState()
        with self.assertRaises(ValueError):
            _rebuild_structure(
                changed, observe_file(target), desired, (),
                state=state, recheck_context=lambda: None,
                stage=lambda position, phase: None,
            )
        self.assertEqual(state.results, ())
        self.assertFalse(os.path.lexists(target))

    def test_material_tree_rejects_additional_member(self):
        scope = self.root / "scope"
        scope.mkdir()
        self.link(scope / "alias.txt", "missing.txt")
        observed = observe_tree(
            scope, include_bytes=True, include_mtime=True, include_links=True,
        )
        desired = state_from_observation(observed)
        source = self.material(observed)
        (source.path / "extra.txt").write_bytes(b"external")
        changed = observe_tree(
            source.path, include_bytes=True, include_mtime=True,
        )
        with self.assertRaises(ValueError):
            validate_material_state(changed, desired, target=scope)

    def test_rebuild_does_not_overwrite_new_external_entry(self):
        observed = self.link(self.root / "alias.txt", "missing.txt")
        desired = state_from_observation(observed)
        source = self.material(observed)
        target = self.root / "rebuilt.txt"
        expected = observe_file(target)
        target.write_bytes(b"external")
        state = ExecutionState()
        with self.assertRaises(ValueError):
            _rebuild_structure(
                source, expected, desired, (),
                state=state, recheck_context=lambda: None,
                stage=lambda position, phase: None,
            )
        self.assertEqual(state.results, ())
        self.assertEqual(target.read_bytes(), b"external")


if __name__ == "__main__":
    unittest.main()
