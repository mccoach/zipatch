# -*- coding: utf-8 -*-

"""用户填写帮助只解释填写契约，不混入开发实现说明。"""

import unittest

from ui.demand_editor import DEMAND_HELP
from ui.exclusion_editor import EXCLUSION_RULES_TEXT


class FillingHelpTests(unittest.TestCase):
    def test_demand_help_retains_file_selection_guidance(self):
        for guidance in (
            "每行填写一条文件选择规则",
            "半角双引号",
            "不自动删除首尾空格",
            "不展开环境变量或 ~",
            "行首 |",
            "C:\\docs\\a.md",
            "\\\\server\\share\\docs\\a.md",
            "相对路径不得使用 ..",
            "文件夹不能直接合并",
            "docs\\**\\*.md",
            "只合并一次",
            "红项不能执行",
            "规则表达",
            "没有选中本次输出文件本身",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, DEMAND_HELP)

    def test_exclusion_help_retains_matching_and_validation_guidance(self):
        for guidance in (
            "目录名单",
            "文件名单",
            "每行一条规则",
            "行首 |",
            "文件名单行首 :",
            "根限定",
            "src\\**\\*.py",
            "Windows 保留设备名",
            "两份业务名单都不能保存",
            "首尾空格",
            "收藏",
            "文件命中排除名单，内容略",
            "不支持正则表达式",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, EXCLUSION_RULES_TEXT)

    def test_help_excludes_internal_implementation_and_developer_acceptance(self):
        internal_terms = (
            "NamedTemporaryFile", "atomic_text_writer", "os.replace",
            "fsync", "临时文件", "原子提交", "旧成果保护",
            "后台", "线程", "防抖", "缓存", "Tk",
            "唯一真相源", "生命周期", "数据流",
            "自动测试", "性能记录", "下一课题",
        )
        for name, help_text in (
            ("按需", DEMAND_HELP),
            ("排除", EXCLUSION_RULES_TEXT),
        ):
            for term in internal_terms:
                with self.subTest(help=name, term=term):
                    self.assertNotIn(term, help_text)

    def test_help_uses_selected_directory_and_list_merge_names(self):
        for help_text in (DEMAND_HELP, EXCLUSION_RULES_TEXT):
            self.assertIn("按目录合并", help_text)
            self.assertIn("按名单合并", help_text)
            self.assertNotIn("常规合并", help_text)
            self.assertNotIn("按需合并", help_text)
            self.assertNotIn("全量合并", help_text)


if __name__ == "__main__":
    unittest.main()
