# -*- coding: utf-8 -*-

"""共用定义读取被拒绝时停止；不打开正文、不枚举或猜测引用目标。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.link_paths import inspect_link_path


@unittest.skipUnless(os.name == "nt", "Windows链接定义读取契约")
class LinkDefinitionPermissionTests(unittest.TestCase):
    def test_denied_definition_preserves_context_and_never_follows_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "actual"
            target.mkdir()
            entry = root / "recover-folder"
            os.symlink(target, entry, target_is_directory=True)
            try:
                failure = PermissionError("injected definition access denied")
                for follow in (False, True):
                    with self.subTest(follow=follow):
                        with patch(
                            "core.link_paths.os.readlink", side_effect=failure,
                        ), patch(
                            "pathlib.Path.open",
                            side_effect=AssertionError("不得读取引用目标正文"),
                        ), patch(
                            "os.scandir",
                            side_effect=AssertionError("不得枚举引用目标"),
                        ):
                            with self.assertRaises(PermissionError) as caught:
                                inspect_link_path(entry, follow_final_link=follow)
                        self.assertIs(caught.exception.__cause__, failure)
                        self.assertIn(str(entry), str(caught.exception))
                        self.assertIn("directory_symlink", str(caught.exception))
                        self.assertIn("系统拒绝访问", str(caught.exception))
                self.assertTrue(os.path.lexists(entry))
                self.assertTrue(target.is_dir())
            finally:
                if os.path.lexists(entry):
                    os.rmdir(entry)


if __name__ == "__main__":
    unittest.main()