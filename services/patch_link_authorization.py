# -*- coding: utf-8 -*-

"""修改包链接正文访问的固定范围展示，不读取正文或授予权限。"""

from core.link_paths import LinkPathObservation
from core.recovery_paths import contains


def format_link_content_access(operation, access, root):
    if not isinstance(access, LinkPathObservation):
        raise TypeError("正文范围展示必须消费正式链接路径观察")
    if not access.follow_final_link or access.entry_link is not None:
        raise ValueError("正文授权要求明确解析的实际普通对象位置")
    lines = [
        f'id="{operation["id"]}" {operation["op"]}',
        f'修改包相对路径：{operation["path"]}',
        f"名义位置：{access.nominal_path}",
        f"实际正文位置：{access.actual_path}",
        "实际访问范围：" + (
            "项目根内" if contains(root, access.actual_path) else "项目根外"
        ),
        f"实际对象类型：{access.kind}",
        "",
        "【本次经过的完整链接定义】",
    ]
    for definition in access.followed_links:
        lines.extend((
            f"- 入口：{definition.entry_path}",
            f"  类型：{definition.kind}",
            f"  实际原定义：{definition.raw_target!r}",
            f"  相对定义：{definition.relative}",
            f"  直接引用位置：{definition.direct_target}",
            f"  入口对象身份：{definition.object_identity}",
        ))
    lines.extend((
        "",
        "授权允许读取和编辑上述实际文件正文，不移除或重建链接入口。",
        "访问依赖不追加为备份或恢复对象；只保护实际工作对象。",
        "现存硬链接继续写回原对象，不扫描其他关联名称。",
        "悬空访问不在本批创建目标；入口复制、移动和删除不由此授权。",
        "授权后仍检查固定路径、定义、对象身份和原正文。",
    ))
    return "\n".join(lines)


def format_link_structure_access(operation, observations, root):
    """完整固定入口及结构范围；引用目标不作为工作或恢复对象。"""
    from core.file_observation import FileObservation, TreeObservation
    from core.link_paths import specification_from_link, validate_link_specification

    from services.patch_scope import PatchScopeTree

    observations = tuple(observations)
    lines = [
        f'id="{operation["id"]}" {operation["op"]}',
        f'修改包相对路径：{operation["path"]}',
    ]
    if "new_path" in operation:
        lines.append(f'修改包目标相对路径：{operation["new_path"]}')
    lines.extend((
        "",
        "本次只操作以下固定入口或普通目录结构。",
        "文件夹软链接和文件夹联接（Junction）作为叶子，不读取、复制或删除目标正文。",
        "复制保留实际原定义；相对定义在目标入口位置解释，不改成绝对定义。",
        "移动保留实际原定义，不压平中间链接链。",
        "入口复制覆盖只替换指定名称，不写入被覆盖硬链接共享正文。",
        "",
    ))
    for number, observed in enumerate(observations):
        role = (
            "来源／指定入口" if number == 0 else "目标入口"
        )
        if isinstance(observed, (TreeObservation, PatchScopeTree)):
            kind = "directory"
            definitions = tuple(
                (relative, specification_from_link(member.entry_link))
                for relative, member in observed.links
            )
            members = observed.content_records
        elif isinstance(observed, LinkPathObservation):
            kind = observed.kind
            definitions = (("", specification_from_link(observed.entry_link)),)
            members = (("", kind, ""),)
        elif isinstance(observed, FileObservation):
            kind, definitions = observed.kind, ()
            members = () if kind == "absent" else (("", kind, ""),)
        else:
            raise TypeError("结构范围必须消费正式完整观察")
        lines.extend((
            f"【{role}】",
            f"位置：{observed.path}",
            f"类型：{kind}",
            "相对于项目根：" + (
                "根内入口" if contains(root, observed.path) else "根外入口"
            ),
        ))
        for relative, member_kind, _ in members:
            lines.append(f"- {observed.path / relative}；类型={member_kind}")
        for relative, specification in definitions:
            entry = observed.path / relative
            direct = validate_link_specification(specification, entry)
            lines.extend((
                f"  链接入口：{entry}",
                f"  实际原定义：{specification.raw_target!r}",
                f"  相对定义：{specification.relative}",
                f"  直接引用位置：{direct}",
                "  引用范围：" + (
                    "项目根内" if contains(root, direct) else "项目根外"
                ) + "；不取得目标正文访问或恢复资格。",
            ))
            if (
                number == 0 and "new_path" in operation
                and operation["op"] in (
                    "copy_file", "copy_dir", "move_file", "move_dir",
                    "rename_file", "rename_dir",
                )
            ):
                destination = observations[1].path / relative
                lines.extend((
                    f"  操作后目标入口：{destination}",
                    "  原定义在目标位置的直接引用："
                    f"{validate_link_specification(specification, destination)}",
                ))
        lines.append("")
    lines.extend((
        "本OP范围属于本包统一决定；全部预演通过后仍须正式执行确认。",
        "跳过当前OP会裁剪它的正文、保护对象及访问依赖。",
        "校验失败不可跳过；执行只复核固定范围，不增加现场恢复对象。",
        "目录链接父链只定位已展示的实际入口；访问依赖不成为恢复对象。",
        "单链接跨卷移动在准备时固定为原定义重建及来源移除组合；"
        "目标创建并完整核验成功后，才允许移除来源入口。",
        "相对原定义保持原样；目标父目录改变可能改变直接引用位置。",
    ))
    return "\n".join(lines)