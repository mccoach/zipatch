# -*- coding: utf-8 -*-

"""来源节点的精简展示；权限及固定路径事实由来源任务拥有。"""

from ui.work_scope import (
    WorkScopeChoice, LINK_LABELS, select_link_work_scope,
)


def make_source_node_selector(parent, commits):
    def select(nodes):
        choices, details = [], []
        for node in nodes:
            definition = node.definition
            lines = [
                f"名义路径：{node.nominal_path}",
                f"原定义路径：{definition.raw_target}",
            ]
            if definition.entry_path != node.nominal_path:
                lines.append(f"实际入口：{definition.entry_path}")
            choices.append(WorkScopeChoice(
                "\n".join(lines), LINK_LABELS[definition.kind],
            ))
            details.append(
                "\n".join(lines)
                + f"\n类型：{definition.kind}"
                + f"\n原定义转义展示：{definition.raw_target!r}"
                + f"\n直接引用：{definition.direct_target}"
                + "\n选中仅接通本节点，不自动授权下游链接。"
            )
        return select_link_work_scope(
            parent, commits, title="选择本次来源链接节点",
            introduction=(
                "选中为通，未选为断；上层断开后不访问或展示下游。\n"
                "文件夹接通后按普通文件夹展开，新链接仍须分别选择；"
                "合并只输出最终文件正文，不单列文件夹。\n"
                "原定义路径是链接保存的指向表达，可能是相对路径；"
                "不等于全部链接解析后的最终实际位置。\n"
                "成果的“显示实际路径”只控制附加说明，不关闭授权和安全检查。\n"
                "已决定节点不重复询问；全不选表示断点收口，取消则不发布。"
            ),
            choices=tuple(choices), details="\n\n".join(details),
        )
    return select