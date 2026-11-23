# -*- coding: utf-8 -*-

"""扫描、合并固定来源到共同选择视图；不读取正文或决定来源规划。"""

from services.scan_scope import describe_scan_access, scan_display_path
from ui.work_scope import (
    WorkScopeChoice, link_group,
    select_link_work_scope as select_work_scope,
)


from services.source_selection import (
    source_boundary_positions, source_choice_groups,
    project_source_choices, source_target_key,
)


def describe_source_group(accesses):
    """显示全部共同边界及原定义，不展开目录中的文件清单。"""
    accesses = tuple(accesses)
    boundaries = source_boundary_positions(accesses)

    label = "\n".join(
        ("目录范围：" if kind == "directory" else "文件入口：") + str(path)
        for path, kind in boundaries
    )
    definitions = {}
    for access in accesses:
        for definition in (
            *access.followed_links,
            *((access.entry_link,) if access.entry_link is not None else ()),
        ):
            definitions.setdefault(definition.entry_path, definition)
    lines = [
        label,
        "上述全部边界共同保留或取消；只授权本次已经固定的来源成员。",
        "目录不展开逐文件名单；不授予未来新增成员或其他链接目标的访问资格。",
    ]
    for definition in definitions.values():
        lines.extend((
            f"链接入口：{definition.entry_path}",
            f"类型：{definition.kind}",
            f"实际原定义：{definition.raw_target!r}",
            f"直接引用：{definition.direct_target}",
        ))
    return label, "\n".join(lines)


def make_scan_selector(parent, commits):
    def select(scope, members, output):
        routes = tuple(
            (
                scope.root_access,
                *((member,) if hasattr(member, "entry_link") else ()),
            )
            for member in members
        )
        groups = source_choice_groups(routes)
        if not groups:
            return tuple("authorize" for _ in members)
        choices, details = [], []
        for position, numbers in groups:
            accesses = tuple(
                access for number in numbers for access in routes[number]
            )
            label, description = describe_source_group(accesses)
            choices.append(WorkScopeChoice(label, link_group(accesses)))
            details.append(
                description
                + "\n仅输出固定扫描项元信息，不读取正文、不展开链接叶子目标。"
            )
        selected = select_work_scope(
            parent, commits, title="选择本次扫描输出项",
            introduction=(
                f"扫描根：{scope.root_access.nominal_path}\n"
                "仅列链接入口或目录访问范围；普通来源自动保留。\n"
                "同一范围及关联输出共同决定，全部别名边界均在列表和详情展示；"
                "未选输出及后续依赖一并裁剪。\n"
                f"输出：{output.path}"
            ),
            choices=tuple(choices), details="\n\n".join(details),
        )
        return project_source_choices(groups, selected, len(members))
    return select


def make_merge_selector(parent, commits):
    def select(prepared, accesses, output):
        aliases_by_target = {}
        for access in accesses:
            aliases_by_target.setdefault(source_target_key(access), []).append(access)
        routes = tuple(
            tuple(aliases_by_target[source_target_key(access)])
            for entry, access in prepared
        )
        groups = source_choice_groups(routes)
        if not groups:
            return tuple("authorize" for _ in prepared)
        choices, details = [], []
        for position, numbers in groups:
            aliases = tuple(
                access for number in numbers for access in routes[number]
            )
            label, description = describe_source_group(aliases)
            choices.append(WorkScopeChoice(label, link_group(aliases)))
            details.append(
                description
                + "\n正文只在选择后获取；末级链接只保存原定义，不读取引用目标正文。"
                "\n同一输出使用的全部别名共同授权，不执行半个来源项。"
            )
        selected = select_work_scope(
            parent, commits, title="选择本次合并来源块",
            introduction=(
                "仅列链接入口或目录访问范围；普通来源自动保留。\n"
                "目录后代不逐文件展开；跨多个别名目录的共同项同时展示全部边界。\n"
                "未选范围不获取正文、不生成块，尾部按决定边界汇总，"
                "相关访问依赖一并裁剪。\n"
                "枚举许可不替代本次正文及输出授权。\n"
                f"输出：{output.path}"
            ),
            choices=tuple(choices), details="\n\n".join(details),
        )
        return project_source_choices(groups, selected, len(prepared))
    return select


def make_enumeration_selector(parent, commits):
    def select(accesses):
        choices = tuple(
            WorkScopeChoice(
                f"目录枚举：{access.nominal_path}\n实际目录：{access.actual_path}",
                link_group((access,)),
            )
            for access in accesses
        )
        return select_work_scope(
            parent, commits, title="选择本次实际目录枚举范围",
            introduction=(
                "尚未进入以下链接实际目录。\n"
                "勾选允许枚举该固定目录及普通子树的元信息；"
                "未选范围不进入，也不从该范围选择文件。\n"
                "同一名义范围的目录来源和列表规则共用决定。"
                "不同入口即使指向同处也分别展示。\n"
                "不会进入途中其他链接目标，不读取正文。"
                "枚举后仍需选择具体输出项；本次决定不保存。\n"
                "全不选可以形成空成果；取消不发布成果。"
            ),
            choices=choices,
            details="\n\n".join(describe_scan_access(access) for access in accesses),
        )
    return select