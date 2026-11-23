# -*- coding: utf-8 -*-

"""结构树在正式内部材料消费者中只生成普通材料。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file, observe_tree, recheck_tree
from core.internal_materials import (
    InternalMaterialError, dispose_internal_material,
    prepare_internal_material, publish_internal_material,
)
from core.link_paths import decode_link_specification, specification_from_link


@unittest.skipUnless(os.name == "nt", "结构材料使用Windows正式提交")
class StructureMaterialTreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_structure_material_",
        )
        self.root = Path(self.temporary.name)
        self.scope = self.root / "scope"
        self.scope.mkdir()
        self.destination = self.root / "materials"
        self.destination.mkdir()
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    os.rmdir(entry) if directory else os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, *, directory=False):
        try:
            os.symlink(target, entry, target_is_directory=directory)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("当前账户没有符号链接创建权限")
            raise
        self.links.append((entry, directory))

    def observed(self):
        return observe_tree(
            self.scope, include_bytes=True, include_mtime=True,
            include_links=True,
        )

    def test_mixed_tree_publishes_only_regular_materials(self):
        (self.scope / "nested").mkdir()
        (self.scope / "nested" / "body.bin").write_bytes(b"\x00original")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"not included")
        self.link(self.scope / "folder_alias", outside, directory=True)
        self.link(self.scope / "missing_alias.txt", "missing.txt")
        source = self.observed()
        target = self.destination / "scope"
        material = prepare_internal_material(source, observe_file(target))
        staged = observe_tree(material.staging_path, include_bytes=True)
        self.assertFalse(staged.links)
        self.assertEqual(
            set(dict(staged.files)),
            {"nested/body.bin", "folder_alias", "missing_alias.txt"},
        )
        receipt = publish_internal_material(material)
        self.assertTrue(receipt.succeeded)
        self.assertEqual(receipt.kind, "directory")
        self.assertFalse(receipt.observation.links)
        self.assertEqual(
            (target / "nested" / "body.bin").read_bytes(), b"\x00original",
        )
        for relative, original in source.links:
            saved = observe_file(target / relative, include_bytes=True)
            self.assertEqual(saved.link_count, 1)
            self.assertEqual(
                decode_link_specification(
                    saved.source_bytes, entry_path=source.path / relative,
                ),
                specification_from_link(original.entry_link),
            )
        self.assertEqual(
            (outside / "private.txt").read_bytes(), b"not included",
        )
        recheck_tree(source, check_mtime=True)

    def test_complete_staged_structure_can_be_explicitly_cancelled(self):
        self.link(self.scope / "alias.txt", "missing.txt")
        source = self.observed()
        target = self.destination / "scope"
        material = prepare_internal_material(source, observe_file(target))
        container = material.container_path
        result = dispose_internal_material(material)
        self.assertEqual(result.cleanup_state, "removed")
        self.assertFalse(os.path.lexists(container))
        self.assertFalse(os.path.lexists(target))
        recheck_tree(source)

    def test_retargeting_after_preparation_prevents_name_commit(self):
        entry = self.scope / "alias.txt"
        self.link(entry, "first.txt")
        source = self.observed()
        target = self.destination / "scope"
        material = prepare_internal_material(source, observe_file(target))
        staged = material.staging_path
        os.unlink(entry)
        os.symlink("second.txt", entry)
        with self.assertRaises(InternalMaterialError) as captured:
            publish_internal_material(material)
        self.assertFalse(captured.exception.result.name_call_attempted)
        self.assertFalse(target.exists())
        self.assertTrue(staged.is_dir())
        with self.assertRaises(ValueError):
            dispose_internal_material(material)

    def test_external_member_stops_structure_material_generation(self):
        self.link(self.scope / "alias.txt", "missing.txt")
        source = self.observed()
        target = self.destination / "scope"
        from core import internal_materials
        original_execute = internal_materials.execute_filesystem_change
        injected = []

        def create_and_inject(step):
            receipt = original_execute(step)
            if (
                receipt.kind == "create_directory"
                and receipt.target.name == "material"
                and not injected
            ):
                external = receipt.target / "external.txt"
                external.write_bytes(b"external")
                injected.append(external)
            return receipt

        with patch(
            "core.internal_materials.execute_filesystem_change",
            side_effect=create_and_inject,
        ):
            with self.assertRaises(InternalMaterialError):
                prepare_internal_material(source, observe_file(target))
        self.assertEqual(len(injected), 1)
        self.assertEqual(injected[0].read_bytes(), b"external")
        self.assertFalse(target.exists())
        recheck_tree(source)


if __name__ == "__main__":
    unittest.main()
