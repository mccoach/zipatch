# -*- coding: utf-8 -*-

"""排除业务的目录、文件联合准备；名单解释归 core.rule_lists。"""

from dataclasses import dataclass

from core.rule_lists import (
    RuleListResult, RuleValidationError, analyze_rule_list, format_rule_diagnostics,
)


class ExclusionValidationError(RuleValidationError):
    pass


@dataclass(frozen=True)
class PreparedExclusions:
    directories: RuleListResult
    files: RuleListResult

    def __post_init__(self):
        if (
            not isinstance(self.directories, RuleListResult)
            or self.directories.object_kind != "directory"
            or not isinstance(self.files, RuleListResult)
            or self.files.object_kind != "file"
        ):
            raise TypeError("排除必须消费类别正确的目录和文件名单结果")
        if self.directories.has_errors or self.files.has_errors:
            raise ExclusionValidationError(
                format_rule_diagnostics(
                    (("目录名单", self.directories), ("文件名单", self.files)),
                    use_input_lines=True,
                )
            )


def prepare_exclusions(directory_text, file_text):
    return PreparedExclusions(
        analyze_rule_list(directory_text, "directory"),
        analyze_rule_list(file_text, "file"),
    )