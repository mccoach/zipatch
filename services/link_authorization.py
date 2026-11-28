# -*- coding: utf-8 -*-

"""固定链接对象的展示投影和工作选择；不读磁盘、不执行工作。

软链接定义与普通文件的硬链接事实分别表达。
工作编号只用于把对象选择投影到所属业务的不可拆分工作单位。
"""

from dataclasses import dataclass
from pathlib import Path

from core.file_observation import FileObservation, TreeObservation
from core.link_paths import LinkPathObservation
from core.recovery_paths import identity
from core.work_selection import (
    group_related_indices, validate_hierarchical_decisions,
)


@dataclass(frozen=True)
class LinkAuthorizationNode:
    definition: object | None
    nominal_path: Path
    parent_index: int | None = None
    metadata: FileObservation | None = None
    work_indices: tuple = ()
    impact: str = ""
    specification: object | None = None

    @property
    def kind(self):
        if self.metadata is not None:
            return "file_hardlink"
        if self.specification is not None:
            return self.specification.kind
        return self.definition.kind

    @property
    def entry_path(self):
        if self.metadata is not None:
            return self.metadata.path
        if self.specification is not None:
            return self.nominal_path
        return self.definition.entry_path

    @property
    def target_description(self):
        if self.metadata is not None:
            return f"共享同一文件对象；链接数量 {self.metadata.link_count}"
        if self.specification is not None:
            return "保存原定义：" + self.specification.raw_target
        return self.definition.raw_target


def count_link_nodes(nodes):
    """按不同实际入口分类；硬链接计名称入口，不把共享身份当名称。"""
    positions = {
        (node.kind, identity(node.entry_path)) for node in nodes
    }
    return tuple(
        (kind, sum(member_kind == kind for member_kind, _ in positions))
        for kind in (
            "file_hardlink", "file_symlink", "directory_symlink", "junction",
        )
    )


def observed_files(observed):
    if isinstance(observed, FileObservation):
        return (observed,) if observed.kind == "file" else ()
    if isinstance(observed, TreeObservation) or (
        hasattr(observed, "files") and hasattr(observed, "links")
    ):
        return tuple(member for _, member in observed.files)
    return ()


def content_selection_groups(observations_by_work, written_objects):
    """只将会被本次正文写回影响的已知同对象工作联动。

    树中的名称也可被其他工作共享正文写回影响；
    该树仍是一个整体单位，不赋予其成员独立执行资格。
    """
    written_objects = frozenset(written_objects)
    return group_related_indices(
        tuple(
            ("shared_content", file.object_identity)
            for observed in observations
            for file in observed_files(observed)
            if file.object_identity in written_objects
        )
        for observations in observations_by_work
    )


def collect_link_nodes(accesses_by_work, observations_by_work, impacts):
    """从已有固定事实生成对象树；不推测普通父路径为授权对象。"""
    accesses_by_work = tuple(tuple(values) for values in accesses_by_work)
    observations_by_work = tuple(tuple(values) for values in observations_by_work)
    impacts = tuple(impacts)
    if not (
        len(accesses_by_work) == len(observations_by_work) == len(impacts)
    ):
        raise ValueError("链接展示必须准确对应全部工作单位")
    nodes = []
    positions = {}

    def add(definition, nominal, parent, work, impact, metadata=None):
        kind = "file_hardlink" if metadata is not None else definition.kind
        entry = metadata.path if metadata is not None else definition.entry_path
        key = (kind, identity(entry), identity(nominal), parent)
        number = positions.get(key)
        if number is None:
            number = len(nodes)
            positions[key] = number
            nodes.append(LinkAuthorizationNode(
                definition, nominal, parent, metadata, (work,), impact,
            ))
        else:
            from dataclasses import replace
            node = nodes[number]
            if node.definition != definition or node.metadata != metadata:
                raise ValueError("同一展示对象具有矛盾的固定事实")
            members = tuple(dict.fromkeys((*node.work_indices, work)))
            explanation = node.impact
            if impact not in explanation.split("\n"):
                explanation += "\n" + impact
            nodes[number] = replace(
                node, work_indices=members, impact=explanation,
            )
        return number

    for work, (accesses, observations, impact) in enumerate(zip(
        accesses_by_work, observations_by_work, impacts,
    )):
        roots = {}
        for access in accesses:
            parent = None
            for definition in access.followed_links:
                # 展示保留本次经过的真实入口，不将最终对象冒充定义。
                parent = add(
                    definition, definition.entry_path, parent, work, impact,
                )
            if access.entry_link is not None:
                parent = add(
                    access.entry_link, access.nominal_path,
                    parent, work, impact,
                )
            roots[identity(access.actual_path)] = (access.nominal_path, parent)

        for observed in observations:
            path = observed.path
            nominal, parent = roots.get(identity(path), (path, None))
            if isinstance(observed, LinkPathObservation):
                if observed.entry_link is not None:
                    # 同一末级入口可能已由access登记为当前parent。
                    # 对象观察只补充尚未展示的入口，不把入口列为自己的子项。
                    if (
                        parent is None
                        or nodes[parent].definition != observed.entry_link
                    ):
                        add(observed.entry_link, nominal, parent, work, impact)
                continue
            for file in observed_files(observed):
                if file.link_count > 1:
                    relative = file.path.relative_to(path)
                    add(
                        None, nominal / relative, parent, work, impact,
                        metadata=file,
                    )
            if hasattr(observed, "links"):
                for relative, member in observed.links:
                    add(
                        member.entry_link, nominal / relative,
                        parent, work, impact,
                    )

    return tuple(nodes)


def related_node_groups(nodes, work_groups):
    """不可拆分工作及共享正文工作，投影为展示节点的共同开关组。"""
    owners = {}
    for group_number, members in enumerate(work_groups):
        for member in members:
            owners[member] = group_number
    return group_related_indices(
        tuple(("work_group", owners[work]) for work in node.work_indices)
        for node in nodes
    )


def project_link_selection(nodes, decisions, count, work_groups):
    """任何风险对象拒绝，其整体工作及共享正文相关工作共同跳过。"""
    selected = set(validate_hierarchical_decisions(
        decisions, tuple(node.parent_index for node in nodes),
        description="链接对象选择",
    ))
    denied = {
        work
        for number, node in enumerate(nodes) if number not in selected
        for work in node.work_indices
    }
    result = ["authorize"] * count
    for members in work_groups:
        if denied.intersection(members):
            for member in members:
                result[member] = "skip"
    return tuple(result)