# -*- coding: utf-8 -*-

"""正式内部材料消费者保存链接定义，不创建活动链接或读取目标正文。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_observation import observe_file
from core.filesystem_changes import (
    execute_filesystem_change, prepare_link_creation,
)
from core.internal_materials import (
    InternalMaterialError, dispose_internal_material,
    prepare_internal_material, publish_internal_material,
)
from core.link_paths import (
    LinkSpecification, decode_link_specification, inspect_link_path,
    recheck_link_path, specification_from_link,
)
from core.protection_projection import state_from_observation


@unittest.skipUnless(os.name == "nt", "正式内部材料提交只支持Windows")
class InternalLinkMaterialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="zipatch_internal_link_material_",
        )
        self.root = Path(self.temporary.name)
        self.material_root = self.root / "materials"
        self.material_root.mkdir()
        self.links = []

    def tearDown(self):
        try:
            for entry, directory in reversed(self.links):
                if os.path.lexists(entry):
                    if directory:
                        os.rmdir(entry)
                    else:
                        os.unlink(entry)
        finally:
            self.temporary.cleanup()

    def make_link(self, name, specification):
        entry = self.root / name
        try:
            result = execute_filesystem_change(prepare_link_creation(
                observe_file(entry), specification,
            ))
        except OSError as error:
            if getattr(error.__cause__, "winerror", None) == 1314:
                self.skipTest("当前账户没有符号链接创建权限")
            raise
        self.assertTrue(result.succeeded)
        self.links.append((
            entry, specification.kind != "file_symlink",
        ))
        return result.created_link

    def save_and_check(self, observed):
        target = self.material_root / observed.actual_path.name
        fixed_state = state_from_observation(observed)
        specification = fixed_state.link_definitions[0][1]
        material = prepare_internal_material(
            observed, observe_file(target), durable_publication=True,
        )
        self.assertFalse(target.exists())
        staged = material.staging_path
        self.assertTrue(staged.is_file())
        self.assertFalse(staged.is_symlink())
        receipt = publish_internal_material(material)
        self.assertTrue(receipt.succeeded)
        self.assertEqual(receipt.source, observed.actual_path)
        self.assertEqual(receipt.kind, observed.kind)
        self.assertEqual(receipt.observation.kind, "file")
        self.assertEqual(receipt.observation.link_count, 1)
        self.assertFalse(target.is_symlink())
        self.assertFalse(os.path.lexists(staged))
        saved = observe_file(target, include_bytes=True)
        self.assertEqual(
            decode_link_specification(
                saved.source_bytes, entry_path=observed.actual_path,
            ),
            specification,
        )
        recheck_link_path(observed)
        with self.assertRaises(ValueError):
            publish_internal_material(material)
        return receipt

    def test_file_symlink_material_preserves_system_raw_definition(self):
        source = self.root / "source.txt"
        source.write_bytes(b"keep")
        entry = self.root / "alias.txt"
        # 本测试验证既有入口的材料保存，不以严格定义重建核心
        # 创建来源。Windows可以为普通绝对创建参数增加系统前缀；
        # 材料必须保留探查到的真实定义，而不是创建参数。
        try:
            os.symlink(source, entry)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("当前账户没有符号链接创建权限")
            raise
        self.links.append((entry, False))
        observed = inspect_link_path(entry, follow_final_link=False)
        raw_definition = os.readlink(entry)
        self.assertEqual(observed.entry_link.raw_target, raw_definition)
        self.assertEqual(observed.entry_link.direct_target, source)
        receipt = self.save_and_check(observed)
        saved = observe_file(receipt.target, include_bytes=True)
        self.assertEqual(
            decode_link_specification(
                saved.source_bytes, entry_path=entry,
            ).raw_target,
            raw_definition,
        )
        self.assertEqual(source.read_bytes(), b"keep")

    def test_directory_symlink_material_is_regular_definition_file(self):
        target = self.root / "outside"
        target.mkdir()
        (target / "keep.txt").write_bytes(b"keep")
        observed = self.make_link(
            "directory_alias",
            LinkSpecification("directory_symlink", "outside", True),
        )
        self.save_and_check(observed)
        self.assertEqual((target / "keep.txt").read_bytes(), b"keep")

    def test_junction_material_is_regular_definition_file(self):
        target = self.root / "中文 目标"
        target.mkdir()
        (target / "keep.txt").write_bytes(b"keep")
        # 原生创建后的系统定义作为正式材料来源，不猜其前缀。
        entry = self.root / "联接 入口"
        result = execute_filesystem_change(prepare_link_creation(
            observe_file(entry),
            LinkSpecification("junction", "\\\\?\\" + str(target), False),
        ))
        self.links.append((entry, True))
        self.assertTrue(result.succeeded)
        self.save_and_check(result.created_link)
        self.assertEqual((target / "keep.txt").read_bytes(), b"keep")

    def test_dangling_material_does_not_read_or_create_target(self):
        observed = self.make_link(
            "alias.txt",
            LinkSpecification("file_symlink", "missing.txt", True),
        )
        target = self.material_root / "definition.json"
        with patch(
            "pathlib.Path.open",
            side_effect=AssertionError("材料准备不得打开链接目标正文"),
        ):
            material = prepare_internal_material(
                observed, observe_file(target),
            )
        receipt = publish_internal_material(material)
        self.assertTrue(receipt.succeeded)
        self.assertFalse((self.root / "missing.txt").exists())
        recheck_link_path(observed)

    def test_retargeting_blocks_commit_and_preserves_consumed_material(self):
        observed = self.make_link(
            "alias.txt",
            LinkSpecification("file_symlink", "first.txt", True),
        )
        target = self.material_root / "definition.json"
        material = prepare_internal_material(
            observed, observe_file(target),
        )
        staged = material.staging_path
        original_material = staged.read_bytes()
        os.unlink(observed.actual_path)
        os.symlink("second.txt", observed.actual_path)
        with self.assertRaises(InternalMaterialError) as captured:
            publish_internal_material(material)
        self.assertFalse(captured.exception.result.name_call_attempted)
        self.assertEqual(
            captured.exception.result.publication, "not_attempted",
        )
        self.assertFalse(target.exists())
        # 发布资格已经消费。名称调用未发起也不恢复取消或发布资格；
        # 保留现场，由所属保护流程决定失败任务目录的处理。
        with self.assertRaises(ValueError):
            dispose_internal_material(material)
        with self.assertRaises(ValueError):
            publish_internal_material(material)
        self.assertEqual(staged.read_bytes(), original_material)
        self.assertEqual(os.readlink(observed.actual_path), "second.txt")

    def test_external_material_target_is_not_overwritten(self):
        observed = self.make_link(
            "alias.txt",
            LinkSpecification("file_symlink", "missing.txt", True),
        )
        target = self.material_root / "definition.json"
        material = prepare_internal_material(
            observed, observe_file(target),
        )
        staged = material.staging_path
        original_material = staged.read_bytes()
        target.write_bytes(b"external")
        with self.assertRaises(InternalMaterialError) as captured:
            publish_internal_material(material)
        self.assertFalse(captured.exception.result.name_call_attempted)
        self.assertEqual(target.read_bytes(), b"external")
        with self.assertRaises(ValueError):
            dispose_internal_material(material)
        with self.assertRaises(ValueError):
            publish_internal_material(material)
        self.assertEqual(staged.read_bytes(), original_material)
        self.assertEqual(target.read_bytes(), b"external")
        recheck_link_path(observed)

    def test_explicit_cancellation_preserves_original_entry(self):
        observed = self.make_link(
            "alias.txt",
            LinkSpecification("file_symlink", "missing.txt", True),
        )
        target = self.material_root / "definition.json"
        material = prepare_internal_material(
            observed, observe_file(target),
        )
        staged = material.staging_path
        result = dispose_internal_material(material)
        self.assertEqual(result.cleanup_state, "removed")
        self.assertFalse(os.path.lexists(staged))
        self.assertFalse(target.exists())
        recheck_link_path(observed)
        with self.assertRaises(ValueError):
            publish_internal_material(material)


if __name__ == "__main__":
    unittest.main()
