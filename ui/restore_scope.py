# -*- coding: utf-8 -*-

"""恢复和拆分的固定元信息到共同选择窗口；不取得正文或规划保护。"""

from core.link_paths import validate_link_specification
from core.recovery_document import content_state_from_value
from ui.work_scope import (
    WorkScopeChoice, link_group,
    select_link_work_scope as select_work_scope,
)


def describe_access(access):
    lines = [
        f"名义位置：{access.nominal_path}",
        f"实际位置：{access.actual_path}",
        f"当前类型：{access.kind}",
    ]
    for definition in (
        *access.followed_links,
        *((access.entry_link,) if access.entry_link is not None else ()),
    ):
        lines.extend((
            f"链接入口：{definition.entry_path}",
            f"类型：{definition.kind}",
            f"保存原定义：{definition.raw_target!r}",
            f"直接引用：{definition.direct_target}",
        ))
    return lines


def make_recovery_selector(parent, commits):
    def decide(entries, accesses):
        choices, details = [], []
        for entry, access in zip(entries, accesses):
            desired = content_state_from_value(
                entry["original"], target=access.actual_path,
            )
            kinds = tuple(spec.kind for _, spec in desired.link_definitions)
            lines = [
                f"清单对象：{entry['id']}",
                *describe_access(access),
                f"恢复原类型：{desired.kind}",
                "目录对象包含清单所列完整原树及待比较的当前普通子树；"
                "其中目录链接仍为叶子。",
            ]
            for relative, specification in desired.link_definitions:
                position = access.actual_path / relative
                lines.extend((
                    f"待恢复链接入口：{position}",
                    f"原定义：{specification.raw_target!r}",
                    "在实际恢复位置的直接引用："
                    f"{validate_link_specification(specification, position)}",
                ))
            details.append("\n".join(lines))
            label = (
                f"{entry['id']}  {desired.kind}  {access.nominal_path}"
                + (
                    f"\n实际位置：{access.actual_path}"
                    if access.actual_path != access.nominal_path else ""
                )
            )
            choices.append(WorkScopeChoice(label, link_group((access,), kinds)))
        return select_work_scope(
            parent, commits, title="选择本次备份还原对象",
            introduction=(
                "选择清单中要还原的对象；目录树作为一个完整工作项。\n"
                "按所选继续，允许为所选对象读取当前正文及枚举普通目录，"
                "形成固定预检；未选对象不获取、不保护、不恢复。\n"
                "本次选择不保存。完整预检通过后仍须执行确认。"
            ),
            choices=choices, details="\n\n".join(details),
        )
    return decide


def make_split_selector(parent, commits):
    def decide(candidates):
        choices, details = [], []
        for block, access, metadata in candidates:
            lines = [
                f"容器路径：{block.path}",
                f"容器类型：{block.kind}",
                *describe_access(access),
            ]
            if block.specification is not None:
                lines.extend((
                    f"来源保存原定义：{block.specification.raw_target!r}",
                    "该定义仅作来源说明，不作为目标链接创建或改指向指令。",
                ))
            lines.append(
                "有正文时允许读取、保护并更新固定实际文件；"
                "已有文件或目录链接保持不变，授权后沿既有路线工作。"
                "目录结构只要求实体目录存在，不清空成员，不创建链接。"
                "只有文件链接定义而没有正文的记录不写文件，明确报告。"
            )
            details.append("\n".join(lines))
            choices.append(WorkScopeChoice(
                f"{block.kind}  {block.path}\n实际位置：{access.actual_path}",
                link_group((access,), (block.kind,)),
            ))
        return select_work_scope(
            parent, commits, title="选择本次代码拆分记录",
            introduction=(
                "选择要处理的容器记录；未选记录不获取旧正文、不保护、不执行。\n"
                "所选子项需要的普通父目录仍会按固定计划创建，"
                "未选父目录记录不表示禁止子项的必要父目录。\n"
                "不创建、删除或改指向链接；只沿已展示并授权的既有路线工作。"
                "未选链接范围不获取旧正文、不保护、不执行。"
                "预检通过后仍须执行确认。"
            ),
            choices=choices, details="\n\n".join(details),
        )
    return decide