# -*- coding: utf-8 -*-

"""新解释器核对恢复模型注解及正式服务导入，不创建GUI或修改文件。"""

import subprocess
import sys
import unittest
from pathlib import Path


class RecoveryImportContractTests(unittest.TestCase):
    def test_fresh_process_loads_formal_annotations_and_consumers(self):
        project = Path(__file__).resolve().parents[1]
        source = "\n".join((
            "from typing import get_type_hints",
            "from core.recovery_document import RecoveryDocument",
            "from core.recovery_protection import RecoveryEvidence",
            "from services.patch_service import preview_patch, preview_backup_restore",
            "from services.restore_service import RestoreTask",
            "from app import ZipatchApp",
            "assert get_type_hints(RecoveryEvidence)['document'] is RecoveryDocument",
        ))
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=project,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            "新解释器无法加载正式模型或消费者：\n"
            + result.stdout + result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
