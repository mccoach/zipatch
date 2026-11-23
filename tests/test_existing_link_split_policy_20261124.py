# -*- coding: utf-8 -*-

"""共同链接筛选、现存路线实体写入及不创建链接的正式回归。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.file_observation import observe_file
from core.filesystem_changes import (
    execute_filesystem_change, prepare_link_creation,
)
from core.link_paths import LinkSpecification, inspect_link_path
from core.merged_container import MergedContainer
from core.work_selection import group_related_indices, project_group_decisions
from services.restore_service import RestoreTask, prepare_restore
from ui.restore_scope import make_split_selector
from ui.work_scope import WorkScopeChoice, select_link_work_scope


class CommonLinkSelectionPolicyTests(unittest.TestCase):
    def test_related_key_bridge_and_projection_are_shared(self):
        groups = group_related_indices((
            ("first",), ("first", "second"), ("second",), (),
        ))
        self.assertEqual(groups, ((0, 1, 2), (3,)))
        self.assertEqual(
            project_group_decisions(
                (groups[0],), ("skip",), 4, description="共同目标",
            ),
            ("skip", "skip", "skip", "authorize"),
        )

    def test_plain_choices_are_silent_and_fixed_skips_stay_skipped(self):
        with patch(
            "ui.work_scope.select_work_scope",
            side_effect=AssertionError("纯普通范围不弹链接筛选"),
        ):
            result = select_link_work_scope(
                None, None, title="普通", introduction="",
                choices=(
                    WorkScopeChoice("普通正文", "普通工作项"),
                    WorkScopeChoice("既定跳过", "普通工作项", True),
                ),
                details="",
            )
        self.assertEqual(result, ("authorize", "skip"))

    def test_only_link_choices_are_displayed_and_projected(self):
        choices = (
            WorkScopeChoice("普通", "普通工作项"),
            WorkScopeChoice("链接", "文件软链接"),
            WorkScopeChoice("目录链接", "目录软链接"),
            WorkScopeChoice("普通固定跳过", "普通工作项", True),
        )
        with patch(
            "ui.work_scope.select_work_scope",
            return_value=("skip", "authorize"),
        ) as window:
            result = select_link_work_scope(
                None, None, title="混合", introduction="",
                choices=choices, details="固定详情",
            )
        self.assertEqual(window.call_args.kwargs["choices"], choices[1:3])
        self.assertEqual(result, ("authorize", "skip", "authorize", "skip"))

    def test_cancel_and_malformed_choices_do_not_become_authorization(self):
        for answer in (None, ("authorize", "authorize")):
            with self.subTest(answer=answer), patch(
                "ui.work_scope.select_work_scope", return_value=answer,
            ):
                arguments = dict(
                    title="链接", introduction="", details="",
                    choices=(WorkScopeChoice("链接", "文件软链接"),),
                )
                if answer is None:
                    self.assertIsNone(select_link_work_scope(
                        None, None, **arguments,
                    ))
                else:
                    with self.assertRaises(ValueError):
                        select_link_work_scope(None, None, **arguments)


@unittest.skipUnless(os.name == "nt", "Windows正式拆分及保护")
class ExistingLinkSplitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.destination = self.root / "destination"
        self.destination.mkdir()
        self.actual = self.root / "actual"
        self.actual.mkdir()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.links = []

    def tearDown(self):
        try:
            for path, directory in reversed(self.links):
                if os.path.lexists(path):
                    os.rmdir(path) if directory else os.unlink(path)
        finally:
            self.temporary.cleanup()

    def link(self, entry, target, kind):
        if kind == "junction":
            receipt = execute_filesystem_change(prepare_link_creation(
                observe_file(entry),
                LinkSpecification("junction", "\\\\?\\" + str(target), False),
            ))
            self.assertTrue(receipt.succeeded)
        else:
            os.symlink(
                target, entry, target_is_directory=kind != "file_symlink",
            )
        self.links.append((entry, kind != "file_symlink"))
        return inspect_link_path(entry, follow_final_link=False)

    def container(self, records):
        with MergedContainer(CODE_HEADER_LINE, CODE_FOOTER_LINE) as container:
            for path, body, kind, specification in records:
                container.add_block(
                    path, body, kind=kind, specification=specification,
                )
            return "".join(container.text_chunks())

    def plan(self, records, *, base=None, selector=None):
        return prepare_restore(
            None, str(self.destination if base is None else base),
            "overwrite", source_text=self.container(records),
            backup_dir=str(self.backups),
            select_split_scope=(
                make_split_selector(None, None) if selector is None else selector
            ),
        )

    def test_plain_formal_split_is_silent(self):
        with patch(
            "ui.work_scope.select_work_scope",
            side_effect=AssertionError("纯普通拆分不弹筛选"),
        ):
            plan = self.plan((("body.py", "new", "file", None),))
            result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        self.assertEqual((self.destination / "body.py").read_bytes(), b"new")

    def test_existing_root_directory_and_final_file_links_are_preserved(self):
        for kind in ("directory_symlink", "junction"):
            with self.subTest(kind=kind):
                folder = self.actual / kind
                folder.mkdir()
                body = folder / "actual.py"
                body.write_bytes(b"old")
                root_alias = self.root / ("root-" + kind)
                original_root = self.link(root_alias, folder, kind)
                final_alias = folder / "body.py"
                original_file = self.link(final_alias, body, "file_symlink")

                def choose(parent, commits, **keywords):
                    self.assertEqual(len(keywords["choices"]), 1)
                    self.assertIn(str(body), keywords["details"])
                    # 授权详情按repr展示完整原定义，保留反斜杠等字符的
                    # 可辨识表达；不能拿未转义字符串去匹配转义展示。
                    for original in (original_root, original_file):
                        self.assertIn(
                            "保存原定义：" + repr(original.entry_link.raw_target),
                            keywords["details"],
                        )
                    return ("authorize",)

                with patch(
                    "ui.work_scope.select_work_scope", side_effect=choose,
                ), patch(
                    "core.filesystem_changes.prepare_link_creation",
                    side_effect=AssertionError("拆分不创建链接"),
                ), patch(
                    "core.filesystem_changes.prepare_link_removal",
                    side_effect=AssertionError("拆分不删除链接"),
                ):
                    plan = self.plan(
                        (("body.py", "new", "file", None),),
                        base=root_alias,
                    )
                    self.assertEqual(plan.projection.objects[0].target, body)
                    result = RestoreTask(plan).execute()
                self.assertTrue(result.completed)
                self.assertEqual(body.read_bytes(), b"new")
                self.assertEqual(
                    inspect_link_path(root_alias, follow_final_link=False),
                    original_root,
                )
                self.assertEqual(
                    inspect_link_path(final_alias, follow_final_link=False),
                    original_file,
                )

    def test_directory_record_follows_existing_directory_link_without_clearing(self):
        for kind in ("directory_symlink", "junction"):
            with self.subTest(kind=kind):
                target = self.actual / kind
                target.mkdir()
                extra = target / "extra"
                extra.write_bytes(b"keep")
                alias = self.destination / kind
                original = self.link(alias, target, kind)
                with patch(
                    "ui.work_scope.select_work_scope",
                    return_value=("authorize", "authorize"),
                ):
                    plan = self.plan((
                        (kind, "", "directory", None),
                        (kind + "/new.py", "created", "file", None),
                    ))
                result = RestoreTask(plan).execute()
                self.assertTrue(result.completed)
                self.assertEqual((target / "new.py").read_bytes(), b"created")
                self.assertEqual(extra.read_bytes(), b"keep")
                self.assertEqual(
                    inspect_link_path(alias, follow_final_link=False), original,
                )

    def test_saved_directory_definitions_create_only_plain_directories(self):
        source_target = self.root / "never-enumerate"
        source_target.mkdir()
        definitions = (
            ("folder", LinkSpecification("directory_symlink", str(source_target), False)),
            ("joined", LinkSpecification("junction", "\\\\?\\" + str(source_target), False)),
        )
        with patch(
            "ui.work_scope.select_work_scope",
            return_value=("authorize", "authorize"),
        ), patch(
            "core.filesystem_changes.prepare_link_creation",
            side_effect=AssertionError("不允许创建活动链接"),
        ):
            plan = self.plan(tuple(
                (name, "", specification.kind, specification)
                for name, specification in definitions
            ))
            result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        for name, specification in definitions:
            current = inspect_link_path(
                self.destination / name, follow_final_link=False,
            )
            self.assertEqual(current.kind, "directory")
            self.assertFalse(current.has_links)
        self.assertEqual(tuple(source_target.iterdir()), ())

    def test_file_definition_does_not_create_empty_file_or_read_reference(self):
        reference = self.actual / "private"
        reference.write_bytes(b"private")
        specification = LinkSpecification("file_symlink", str(reference), False)
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == reference:
                raise AssertionError("没有正文的定义不得补读目标")
            return original_open(path, *arguments, **keywords)

        with patch("pathlib.Path.open", new=opened), patch(
            "ui.work_scope.select_work_scope", return_value=("authorize",),
        ):
            plan = self.plan((("alias", "", "file_symlink", specification),))
        self.assertEqual(plan.entries, ())
        self.assertEqual(plan.accesses, ())
        self.assertIsNone(plan.backup_root)
        self.assertIn("没有普通文件正文", plan.skipped[0][1])
        self.assertFalse(os.path.lexists(self.destination / "alias"))

    def test_skipped_existing_link_reads_no_old_body_and_keeps_plain_work(self):
        body = self.actual / "body"
        body.write_bytes(b"old")
        alias = self.destination / "alias"
        self.link(alias, body, "file_symlink")
        original_open = Path.open

        def opened(path, *arguments, **keywords):
            if path == body:
                raise AssertionError("未选链接不得取得旧正文")
            return original_open(path, *arguments, **keywords)

        with patch("pathlib.Path.open", new=opened), patch(
            "ui.work_scope.select_work_scope", return_value=("skip",),
        ):
            plan = self.plan((
                ("plain", "plain", "file", None),
                ("alias", "new", "file", None),
            ))
            self.assertEqual(len(plan.entries), 1)
            result = RestoreTask(plan).execute()
        self.assertTrue(result.completed)
        self.assertEqual(body.read_bytes(), b"old")
        self.assertEqual((self.destination / "plain").read_bytes(), b"plain")

    def test_three_kinds_never_bypass_original_definition_reading(self):
        for kind in ("file_symlink", "directory_symlink", "junction"):
            with self.subTest(kind=kind):
                target = (
                    self.actual / "body"
                    if kind == "file_symlink" else self.actual
                )
                if kind == "file_symlink":
                    target.write_bytes(b"old")
                entry = self.destination / kind
                self.link(entry, target, kind)
                original_readlink = os.readlink
                seen = []

                def readlink(path, *arguments, **keywords):
                    if Path(path) == entry:
                        seen.append(Path(path))
                    return original_readlink(path, *arguments, **keywords)

                with patch("os.readlink", new=readlink):
                    observed = inspect_link_path(entry, follow_final_link=False)
                self.assertTrue(seen)
                self.assertEqual(
                    observed.entry_link.raw_target, original_readlink(entry),
                )
                with patch(
                    "core.link_paths.os.readlink",
                    side_effect=PermissionError("definition denied"),
                ), patch(
                    "pathlib.Path.open",
                    side_effect=AssertionError("定义失败不访问正文"),
                ):
                    with self.assertRaises(PermissionError):
                        inspect_link_path(entry, follow_final_link=True)


if __name__ == "__main__":
    unittest.main()