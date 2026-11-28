# -*- coding: utf-8 -*-

"""后台硬链接重复规则、保存定义展示及恢复选择的直接回归。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from core.exclusion_rules import prepare_exclusions
from core.file_observation import observe_file
from core.link_paths import LinkSpecification
from core.protection_projection import (
    FixedEffect, FixedUnit, build_protection_projection, state_from_bytes,
)
from core.recovery_protection import RecoveryProtectionSession, read_recovery_evidence
from services.demand_rules import inspect_demand_rules
from services.link_authorization import LinkAuthorizationNode
from services.merge_service import merge_files
from services.recovery import plan_restore
from services.scan_service import panoramic_scan
from ui.restore_scope import make_recovery_selector


class SavedDefinitionDisplayTests(unittest.TestCase):
    def test_saved_definition_is_not_a_current_identity_observation(self):
        specification = LinkSpecification("file_symlink", "a.py", True)
        node = LinkAuthorizationNode(
            None, Path("saved-link.py"), specification=specification,
        )
        self.assertIsNone(node.definition)
        self.assertIsNone(node.metadata)
        self.assertEqual(node.kind, "file_symlink")
        self.assertEqual(node.entry_path, Path("saved-link.py"))
        self.assertEqual(node.target_description, "保存原定义：a.py")


@unittest.skipUnless(os.name == "nt", "正式来源和恢复使用Windows")
class AuthorizationClosureTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "source"
        source.mkdir()
        first = source / "a.py"
        second = source / "b.py"
        first.write_bytes(b"old\n")
        os.link(first, second)
        return source, first, second

    def test_background_repeated_hardlink_rules_both_remain_pending(self):
        with TemporaryDirectory(prefix="ZipatchAuthBackground_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            results = inspect_demand_rules(
                ((1, str(first)), (2, str(first)), (3, str(second))),
                "", Event(),
            )
            self.assertTrue(all(
                result.requires_authorization for result in results.values()
            ))
            self.assertTrue(all(not result.issue for result in results.values()))

    def test_hardlink_summary_counts_selected_names_not_shared_objects(self):
        with TemporaryDirectory(prefix="ZipatchAuthSummary_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            selector = lambda nodes: ("authorize",) * len(nodes)
            merged = merge_files(
                "directory", "", str(source), "", str(root / "merged.txt"),
                prepare_exclusions("", ""), "", "", "--begin--", "--end--",
                record_file_mtime=False, select_source_links=selector,
            )
            self.assertEqual(dict(merged.link_counts)["file_hardlink"], 2)
            self.assertEqual(merged.total_files, 1)
            self.assertEqual(merged.reference_count, 1)
            scanned = panoramic_scan(
                str(source), str(root / "scan.txt"), prepare_exclusions("", ""),
                "", "", select_source_links=selector,
            )
            self.assertEqual(scanned.hardlink_count, 2)
            self.assertEqual(scanned.link_count, 2)

    def test_terminal_link_access_and_observation_produce_one_node(self):
        from core.link_paths import inspect_link_path
        from services.link_authorization import collect_link_nodes

        with TemporaryDirectory(prefix="ZipatchAuthTerminal_") as folder:
            root = Path(folder)
            target = root / "target.py"
            target.write_bytes(b"unchanged")
            entry = root / "entry.py"
            try:
                os.symlink("target.py", entry)
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest("缺少Windows软链接创建权限，本场景未验证")
                raise
            access = inspect_link_path(entry, follow_final_link=False)
            observed = inspect_link_path(entry, follow_final_link=False)
            nodes = collect_link_nodes(
                ((access,),), ((observed,),), ("操作指定链接入口",),
            )
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].kind, "file_symlink")
            self.assertEqual(nodes[0].nominal_path, entry)
            self.assertIsNone(nodes[0].parent_index)
            self.assertEqual(nodes[0].work_indices, (0,))
            self.assertEqual(target.read_bytes(), b"unchanged")

    def evidence(self, root, first, second):
        observations = tuple(
            observe_file(
                path, include_bytes=True, include_digest=True, include_mtime=True,
            )
            for path in (first, second)
        )
        projection = build_protection_projection(
            tuple(
                FixedUnit(
                    (f"original_{number}",),
                    (FixedEffect(observed, state_from_bytes(b"changed\n")),),
                )
                for number, observed in enumerate(observations)
            ),
            forbidden_roots=(),
        )
        session = RecoveryProtectionSession(
            projection, root / "original_backup",
            "authorization_closure", "patch", material_base=root,
        )
        session.protect_all()
        return read_recovery_evidence(session.task_root)

    def test_recovery_shared_authorization_cancels_both_units_without_body_read(self):
        with TemporaryDirectory(prefix="ZipatchAuthRecovery_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            evidence = self.evidence(root, first, second)
            first.write_bytes(b"changed\n")
            original_open = Path.open
            calls = []

            def guarded(path, *args, **kwargs):
                if Path(path) in (first, second):
                    raise AssertionError("未选恢复对象不得读取当前正文")
                return original_open(path, *args, **kwargs)

            def select(entries, accesses, metadata):
                calls.append(metadata)
                self.assertEqual([member.link_count for member in metadata], [2, 2])
                return ("skip", "authorize")

            with patch.object(Path, "open", guarded):
                plan = plan_restore(evidence, select_restore_scope=select)
            self.assertEqual(len(calls), 1)
            self.assertEqual(plan.actions, ())
            self.assertEqual(first.read_bytes(), b"changed\n")
            self.assertEqual(second.read_bytes(), b"changed\n")

    def test_recovery_selector_projects_object_rejection_to_whole_tree(self):
        from services.patch_scope import inspect_scope_tree
        from core.link_paths import inspect_link_path
        from core.protection_projection import observation_state
        from core.recovery_document import _state_value

        with TemporaryDirectory(prefix="ZipatchAuthTreeSelection_") as folder:
            root = Path(folder)
            source, first, second = self.fixture(root)
            from core.file_observation import observe_tree
            current = observe_tree(
                source, include_bytes=True, include_mtime=True, include_links=True,
            )
            state, _ = observation_state(current)
            entry = {
                "id": "o000001", "original": _state_value(state),
            }
            access = inspect_link_path(source, follow_final_link=False)
            metadata = inspect_scope_tree(source)
            seen = []

            def select(parent, commits, nodes, **keywords):
                seen.extend(nodes)
                self.assertEqual(keywords["related_groups"], ((0, 1),))
                return ("skip", "authorize")

            with patch("ui.restore_scope.select_source_tree", side_effect=select):
                decision = make_recovery_selector(None, None)(
                    (entry,), (access,), (metadata,),
                )
            self.assertEqual(len(seen), 2)
            self.assertEqual(decision, ("skip",))


if __name__ == "__main__":
    unittest.main(verbosity=2)