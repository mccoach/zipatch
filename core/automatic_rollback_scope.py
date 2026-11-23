# -*- coding: utf-8 -*-

"""自动回滚入场比较：原固定范围、实际步骤和可靠现场约束共同授权。

不读取磁盘、不执行恢复、不保存二次备份。
未知结果不补猜身份；不能安全关联的现场停止并交主动恢复确认。
"""

from core.content_publication import ContentPublicationResult
from core.file_observation import FileObservation, TreeObservation
from core.filesystem_changes import FilesystemChangeResult
from core.name_moves import NativeNameMoveResult
from core.protection_projection import ProtectionProjection
from core.recovery_paths import contains, identity


def _members(observed):
    from core.link_paths import LinkPathObservation, link_definition_digest

    if isinstance(observed, LinkPathObservation):
        if observed.follow_final_link or observed.entry_link is None:
            raise ValueError("回滚结构成员必须是不跟随的链接入口")
        return ((
            observed.actual_path, observed.kind, observed.object_identity,
            link_definition_digest(observed.entry_link),
        ),)
    if isinstance(observed, TreeObservation):
        return (
            *((directory.path, "directory", directory.object_identity, "")
              for _, directory in observed.directories),
            *((file.path, "file", file.object_identity, file.digest)
              for _, file in observed.files),
            *((member.actual_path, member.kind, member.object_identity,
               link_definition_digest(member.entry_link))
              for _, member in observed.links),
        )
    if not isinstance(observed, FileObservation):
        raise TypeError("回滚现场必须是正式文件或完整树观察")
    if observed.kind == "absent":
        return ()
    if observed.kind != "file":
        raise ValueError("回滚文件现场类型非法")
    return ((observed.path, "file", observed.object_identity, observed.digest),)


def validate_automatic_rollback_scope(projection, actions, *, results):
    """全部对象先检查通过，再允许共同恢复核心开始修改。

    原对象未被本任务改变时须保持原身份和内容。
    正常新文件身份来自正式发布观察，新目录身份来自正式创建结果。
    原生移动消费原固定来源身份，不从失败现场补造来源。
    已尝试的原对象写回可能留下部分正文，允许恢复其原内容，
    但必须仍为原身份；检查不是文件锁，不能识别全部并发正文写入。
    """
    if not isinstance(projection, ProtectionProjection):
        raise TypeError("自动回滚范围必须来自正式保护投影")
    if type(results) is not tuple:
        raise TypeError("自动回滚必须消费正式工作步骤元组")
    objects = {identity(member.target): member for member in projection.objects}
    actions = tuple(actions)
    if (
        len(actions) != len(objects)
        or {identity(action.target) for action in actions} != set(objects)
    ):
        raise ValueError("自动回滚必须准确检查原任务全部保护对象")

    originals = {}
    candidates = {}
    for member in projection.objects:
        for path, kind, identifier, digest in _members(member.before):
            key = identity(path)
            originals[key] = (path, kind, identifier, digest)
            candidates.setdefault(key, []).append((kind, identifier, digest))

    effects = {
        identity(effect.before.path): effect
        for unit in projection.units for effect in unit.effects
    }
    generated = {}
    planned_directories = set()
    for unit in projection.units:
        planned_directories.update(map(identity, unit.parent_directories))
        planned_directories.update(map(identity, unit.tree_directories))
        for effect in unit.effects:
            for relative, kind, _ in effect.after.records:
                generated[identity(effect.before.path / relative)] = kind

    permitted_absence = set()
    attempted_content_objects = set()
    failed = False
    seen_receipts = set()
    for receipt in results:
        if not isinstance(receipt, (
            ContentPublicationResult, FilesystemChangeResult,
            NativeNameMoveResult,
        )):
            raise TypeError("回滚授权只接受正式工作步骤")
        if failed or id(receipt) in seen_receipts:
            raise ValueError("回滚必须消费未完成步骤截止的唯一实际结果前缀")
        seen_receipts.add(id(receipt))
        failed = not receipt.succeeded
        attempted = (
            receipt.mutation_attempted
            if isinstance(receipt, ContentPublicationResult) else receipt.attempted
        )
        if not attempted:
            continue
        key = identity(receipt.target)
        if isinstance(receipt, ContentPublicationResult):
            if generated.get(key) != "file":
                raise ValueError("文件步骤越出原任务具体成果范围")
            if receipt.mechanism == "in_place":
                original = originals.get(key)
                if original is None or original[1] != "file":
                    raise ValueError("原对象写回缺少原固定文件身份")
                attempted_content_objects.add(original[2])
            if receipt.succeeded:
                observed = receipt.observation
                if (
                    not isinstance(observed, FileObservation)
                    or observed.kind != "file"
                    or identity(observed.path) != key
                ):
                    raise ValueError("正常文件步骤缺少对应的可靠成果观察")
                candidates.setdefault(key, []).append((
                    "file", observed.object_identity, observed.digest,
                ))
        elif isinstance(receipt, NativeNameMoveResult):
            source_key = identity(receipt.source)
            source_effect = effects.get(source_key)
            target_effect = effects.get(key)
            if (
                source_effect is None or target_effect is None
                or source_effect.after.kind != "absent"
            ):
                raise ValueError("名称步骤不对应原任务固定两端")
            source_members = _members(source_effect.before)
            for path, kind, identifier, digest in source_members:
                permitted_absence.add(identity(path))
                relocated = receipt.target / path.relative_to(receipt.source)
                relocated_key = identity(relocated)
                if generated.get(relocated_key) != kind:
                    raise ValueError("名称步骤目标越出固定成果范围")
                candidates.setdefault(relocated_key, []).append((
                    kind, identifier, digest,
                ))
        elif receipt.kind == "create_directory":
            if key not in planned_directories:
                raise ValueError("目录创建步骤越出原固定计划")
            created = receipt.created_directory
            if receipt.succeeded:
                if created is None or identity(created.path) != key:
                    raise ValueError("正常目录创建缺少可靠身份")
                candidates.setdefault(key, []).append((
                    "directory", created.object_identity, "",
                ))
        elif receipt.kind == "create_link":
            required_kind = generated.get(key)
            if required_kind not in (
                "file_symlink", "directory_symlink", "junction",
            ):
                raise ValueError("链接创建步骤越出原固定成果范围")
            if receipt.succeeded:
                observed = receipt.created_link
                if (
                    observed is None or identity(observed.actual_path) != key
                    or observed.kind != required_kind
                ):
                    raise ValueError("正常链接创建缺少可靠对应成果")
                member = _members(observed)[0]
                desired = next(
                    digest
                    for unit in projection.units for effect in unit.effects
                    for relative, kind, digest in effect.after.records
                    if identity(effect.before.path / relative) == key
                    and kind == required_kind
                )
                if member[3] != desired:
                    raise ValueError("正常链接成果不对应原固定定义")
                candidates.setdefault(key, []).append((
                    member[1], member[2], member[3],
                ))
        elif receipt.kind == "remove_link":
            original = originals.get(key)
            if original is None or original[1] not in (
                "file_symlink", "directory_symlink", "junction",
            ):
                raise ValueError("链接移除步骤越出原固定入口")
            permitted_absence.add(key)
        elif receipt.kind in ("remove_file", "remove_empty_directory"):
            original = originals.get(key)
            expected_kind = (
                "file" if receipt.kind == "remove_file" else "directory"
            )
            if original is None or original[1] != expected_kind:
                raise ValueError("名称移除步骤越出原固定成员")
            permitted_absence.add(key)
        elif receipt.kind == "set_directory_attributes":
            if generated.get(key) != "directory":
                raise ValueError("目录属性步骤越出原固定成果范围")
        else:
            raise ValueError("自动回滚遇到未知工作步骤")

    for action in actions:
        protected = objects[identity(action.target)]
        current_members = _members(action.current)
        current_positions = set()
        for path, kind, identifier, digest in current_members:
            key = identity(path)
            current_positions.add(key)
            matches = candidates.get(key, ())
            if not any(
                kind == expected_kind
                and identifier == expected_identity
                and (
                    kind == "directory"
                    or digest == expected_digest
                    or identifier in attempted_content_objects
                )
                for expected_kind, expected_identity, expected_digest in matches
            ):
                raise ValueError(
                    "自动回滚现场无法对应原固定对象或本任务实际成果，"
                    "禁止覆盖或删除外部内容；请人工预检并确认恢复："
                    f"{path}"
                )
        for key, (path, _, _, _) in originals.items():
            if (
                contains(protected.target, path)
                and key not in current_positions
                and key not in permitted_absence
            ):
                raise ValueError(
                    "原对象消失但没有对应工作名称移除事实，"
                    "禁止用自动回滚替代新的恢复授权："
                    f"{path}"
                )
