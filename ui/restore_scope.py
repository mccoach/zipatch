# -*- coding: utf-8 -*-

"""拆分和恢复的固定链接对象选择；普通工作静默保留。

共享正文关系与整树工作边界由业务固定事实投影，
不在窗口查询磁盘或重新规划恢复。
"""

from core.file_observation import FileObservation
from core.link_paths import validate_link_specification
from core.recovery_document import content_state_from_value
from services.link_authorization import (
    collect_link_nodes, content_selection_groups,
    project_link_selection, related_node_groups,
)
from ui.source_scope import select_source_tree


def _select(parent, commits, nodes, count, groups, title, introduction):
    if not nodes:
        return ("authorize",) * count
    decisions = select_source_tree(
        parent, commits, nodes,
        related_groups=related_node_groups(nodes, groups),
        title=title, introduction=introduction,
    )
    if decisions is None:
        return None
    return project_link_selection(nodes, decisions, count, groups)


def make_split_selector(parent, commits):
    def decide(candidates):
        candidates = tuple(candidates)
        observations = tuple(
            () if metadata is None else (metadata,)
            for _, _, metadata in candidates
        )
        written = {
            metadata.object_identity
            for block, _, metadata in candidates
            if block.kind == "file"
            and isinstance(metadata, FileObservation)
            and metadata.kind == "file"
        }
        groups = content_selection_groups(observations, written)
        nodes = collect_link_nodes(
            ((access,) for _, access, _ in candidates),
            observations,
            (
                f"容器路径：{block.path}；"
                "有正文时写入实际文件，现有链接入口保持；"
                "缺失位置建立普通实体，不重建来源链接。"
                for block, _, _ in candidates
            ),
        )
        return _select(
            parent, commits, nodes, len(candidates), groups,
            "选择本次拆分目标链接",
            "这里只列目标现场的链接对象，不以来源链接定义授权创建链接。\n"
            "同一实际正文的相关工作共同选择；取消任何相关风险对象，"
            "对应共享正文工作共同跳过。\n"
            "普通工作自动保留；未选工作不获取旧正文、不备份、不写入。"
            "范围外硬链接名称不枚举，正文写回可能被它们共同看到。\n"
            "完整预演通过后仍须执行确认。",
        )
    return decide


def make_recovery_selector(parent, commits):
    def decide(entries, accesses, metadata):
        entries, accesses, metadata = tuple(entries), tuple(accesses), tuple(metadata)
        observations = tuple((member,) for member in metadata)
        written = {
            member.object_identity
            for entry, member in zip(entries, metadata)
            if isinstance(member, FileObservation)
            and member.kind == "file"
            and entry["original"]["kind"] == "file"
        }
        groups = content_selection_groups(observations, written)
        saved = []
        for number, (entry, access) in enumerate(zip(entries, accesses)):
            desired = content_state_from_value(
                entry["original"], target=access.actual_path,
            )
            for relative, specification in desired.link_definitions:
                position = access.actual_path / relative
                # 保存原定义直接来自严格清单，不伪造当前入口身份或版本。
                validate_link_specification(specification, position)
                saved.append((
                    number, specification, position,
                    f"清单对象：{entry['id']}；恢复保存的链接原定义，"
                    "不读取其引用目标。取消任何成员取消整个清单对象。",
                ))
        nodes = collect_link_nodes(
            ((access,) for access in accesses),
            observations,
            (
                f"清单对象：{entry['id']}；恢复其完整原状态。"
                "目录树整体恢复，不执行树内子集；当前额外内容可能被移除。"
                for entry in entries
            ),
        )
        # 保存与当前同位置定义可能不同，分开列出而不覆盖现场事实。
        if saved:
            from services.link_authorization import LinkAuthorizationNode
            nodes = (
                *nodes,
                *(LinkAuthorizationNode(
                    None, nominal, work_indices=(work,), impact=impact,
                    specification=specification,
                ) for work, specification, nominal, impact in saved),
            )
        return _select(
            parent, commits, nodes, len(entries), groups,
            "选择本次备份还原链接对象",
            "已扫描本次清单目标的必要元信息，尚未读取旧目标正文。\n"
            "这里只列当前链接及保存状态中的待恢复链接定义；普通对象静默保留。\n"
            "一个完整目录树仍是一个恢复单位，取消其中任何风险对象取消整树恢复。"
            "范围内共享正文的相关恢复单位共同选择。\n"
            "未选单位不获取旧正文、不保护、不恢复；材料完整性仍整体严格检查。\n"
            "范围外关联名称和反向引用不扫描；完整预演后仍须执行确认。",
        )
    return decide