# -*- coding: utf-8 -*-

"""本包固定OP的勾选决定；不保存授权、不读取正文或执行工作。"""

from services.patch_scope import PatchScopeTree, format_package_scope


LINK_GROUPS = (
    ("file_symlink", "文件软链接"),
    ("directory_symlink", "目录软链接"),
    ("junction", "目录联接 Junction"),
)


def patch_scope_group(scope):
    """类型仅作展示分组；同一OP的结构及两端不能拆成独立选择。"""
    kinds = {
        definition.kind
        for access in scope.accesses
        for definition in (
            *access.followed_links,
            *((access.entry_link,) if access.entry_link is not None else ()),
        )
    }
    for observed in scope.observations:
        if isinstance(observed, PatchScopeTree):
            kinds.update(member.kind for _, member in observed.links)
    if len(kinds) > 1:
        return "混合链接工作项"
    for kind, label in LINK_GROUPS:
        if kind in kinds:
            return label
    return "普通工作项"


def describe_patch_scope_choice(scope):
    """主视图只给出OP、名义位置及真正不同的实际位置。"""
    operation = scope.operation
    lines = [
        f'id="{operation["id"]}"  {operation["op"]}  {operation["path"]}',
    ]
    if "new_path" in operation:
        lines[0] += " → " + operation["new_path"]
    for number, access in enumerate(scope.accesses):
        role = "来源／入口" if number == 0 else "目标"
        if access.actual_path != access.nominal_path:
            lines.append(f"实际{role}：{access.actual_path}")
        if access.entry_link is not None:
            lines.append("入口指向：" + access.entry_link.raw_target)
    if scope.fixed_skip:
        lines.append("既定跳过：不会因勾选而执行。")
    elif any(isinstance(member, PatchScopeTree) for member in scope.observations):
        lines.append("该OP及所列目录树作为一个工作项处理，不拆分树内成员。")
    return "\n".join(lines)


def select_patch_scope(parent, commits, scopes, root):
    """同一实际工作目标的OP共用一个选项；返回原OP顺序决定。"""
    from services.patch_scope import patch_target_groups
    from ui.work_scope import (
        WorkScopeChoice, link_group,
        select_link_work_scope as select_work_scope,
    )

    scopes = tuple(scopes)
    groups = patch_target_groups(scopes)
    choices = []
    for numbers in groups:
        members = tuple(scopes[number] for number in numbers)
        if len(members) == 1:
            label = describe_patch_scope_choice(members[0])
            group = patch_scope_group(members[0])
        else:
            accesses = tuple(
                access for member in members for access in member.accesses
            )
            group = link_group(accesses)
            label = (
                "共同实际目标："
                + str(members[0].accesses[0].actual_path)
                + "\n关联操作："
                + "；".join(
                    f'id="{member.operation["id"]}" {member.operation["op"]}'
                    for member in members
                )
                + "\n取消该目标会取消全部关联OP。"
            )
        choices.append(WorkScopeChoice(
            label, group, all(member.fixed_skip for member in members),
        ))
    selected = select_work_scope(
        parent, commits, title="选择本次修改操作",
        introduction=(
            f"项目目录：{root}\n"
            "按实际工作目标授权；同一文件的全部关联OP共同保留或取消。\n"
            "正文编辑关联实际正文文件；入口操作关联被操作入口，"
            "不是链接的引用目标。目录树作为一个范围，不展开逐项授权。\n"
            "未选目标不读取正文、不验证正文定位、不备份、不执行。"
            "保留范围仍须通过正文和联合检查，再确认执行。"
        ),
        choices=tuple(choices),
        details=format_package_scope(scopes, root),
    )
    if selected is None:
        return None
    if (
        type(selected) is not tuple or len(selected) != len(groups)
        or any(value not in ("authorize", "skip") for value in selected)
    ):
        raise ValueError("目标选择结果未准确覆盖全部展示目标")
    decisions = ["skip"] * len(scopes)
    for numbers, decision in zip(groups, selected):
        for number in numbers:
            decisions[number] = "skip" if scopes[number].fixed_skip else decision
    return tuple(decisions)


def make_patch_scope_authorizer(parent, commits):
    def decide(scopes, root):
        return select_patch_scope(parent, commits, scopes, root)

    return decide

def make_patch_enumeration_selector(parent, commits):
    def select(scopes, root):
        from services.scan_scope import describe_scan_access
        from ui.work_scope import (
            WorkScopeChoice, select_link_work_scope as select_work_scope,
        )

        choices, details = [], []
        for scope in scopes:
            operation = scope.operation
            label = f'id="{operation["id"]}"  {operation["op"]}  {operation["path"]}'
            if "new_path" in operation:
                label += " → " + operation["new_path"]
            lines = [label]
            for access in scope.accesses:
                lines.append(describe_scan_access(access))
                if access.kind == "directory":
                    lines.append(
                        "此OP保留时需枚举该实际普通目录及普通子树；"
                        "树内链接为叶子，不进入其引用目标。"
                    )
                    label += "\n实际目录：" + str(access.actual_path)
            choices.append(WorkScopeChoice(label, patch_scope_group(scope)))
            details.append("\n".join(lines))
        return select_work_scope(
            parent, commits, title="选择修改包先行目录枚举范围",
            introduction=(
                f"项目目录：{root}\n"
                "整包参数、入口类型及名义路径冲突已检查；"
                "尚未枚举本包目录树或读取普通正文。\n"
                "勾选允许为该OP枚举列出的实际普通目录及普通子树元信息；"
                "一个OP的两端必须一起选择。\n"
                "未选OP固定跳过：不枚举、不读取正文、不备份、不执行，"
                "后续最终OP窗口不能重新勾回。\n"
                "树内链接仍为叶子，不进入引用目标。"
                "枚举后仍须选择完整OP范围，通过正文和联合检查后再确认执行。\n"
                "全不选不等于取消；其他无需此枚举的OP仍可继续准备。"
                "本次决定不保存为以后任务的授权。"
            ),
            choices=choices, details="\n\n".join(details),
        )
    return select