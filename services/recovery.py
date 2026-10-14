# -*- coding: utf-8 -*-

"""固定路径计划、逐步原状态保护及新格式集中恢复的基础能力。"""

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from core.atomic_file import PublicationError, atomic_text_writer, sync_directory
from core.recovery_paths import (
    checked_chain, checked_root, contains, entity_type, identity,
    overlaps, project_path, relative_path, tree_inventory,
)
from core.time_utils import now_stamp
from services.patch_backup import file_content_sha256, resolve_backup_base_dir
from services.patch_ops import DUAL_PATH_OPS


RECOVERY_FORMAT = "ZIPATCH_RECOVERY"
RECOVERY_VERSION = 1
TREE_ALGORITHM = "sha256-tree-json-v1"


@dataclass(frozen=True)
class PathExpectation:
    path: Path
    kind: str


@dataclass(frozen=True)
class PlannedAction:
    label: str
    payload: object
    execute: bool
    changes: tuple
    reads: tuple
    expectations: tuple
    parents: tuple


@dataclass(frozen=True)
class ProtectedObject:
    path: str
    kind: str


@dataclass(frozen=True)
class TaskPlan:
    root: Path
    actions: tuple
    objects: tuple
    action_objects: tuple


@dataclass(frozen=True)
class ObjectSnapshot:
    kind: str
    inventory: tuple
    integrity: object


def snapshot(path):
    kind, inventory = tree_inventory(path)
    if kind == "absent":
        return ObjectSnapshot(kind, inventory, None)
    if kind == "file":
        return ObjectSnapshot(kind, inventory, {
            "algorithm": "sha256", "digest": file_content_sha256(path),
        })
    digest = hashlib.sha256()
    for relative, child, child_kind in inventory:
        record = json.dumps(
            [
                relative, child_kind,
                file_content_sha256(child) if child_kind == "file" else "",
            ],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(record).to_bytes(8, "big"))
        digest.update(record)
    return ObjectSnapshot(kind, inventory, {
        "algorithm": TREE_ALGORITHM, "digest": digest.hexdigest(),
    })


def sync_file(path):
    # Windows FlushFileBuffers 要求可写句柄。仅临时调整本次准备材料，
    # 不改变业务源；恢复权限后再次请求文件持久化。
    mode = stat.S_IMODE(Path(path).stat().st_mode)
    writable_mode = mode | stat.S_IWUSR
    changed = writable_mode != mode
    if changed:
        os.chmod(path, writable_mode)
    try:
        with Path(path).open("r+b") as stream:
            if changed:
                os.chmod(path, mode)
            os.fsync(stream.fileno())
    finally:
        if changed:
            os.chmod(path, mode)


def sync_snapshot(path, observed):
    if observed.kind == "file":
        sync_file(path)
        return
    for _, child, kind in observed.inventory:
        if kind == "file":
            sync_file(child)
    for _, child, kind in reversed(observed.inventory):
        if kind == "directory":
            sync_directory(child)


def parents_for(root, target):
    parents = []
    current = target.parent
    while identity(current) != identity(root):
        kind = entity_type(current)
        if kind not in ("directory", "absent"):
            raise ValueError(f"必要父目录被非目录阻塞：{current}")
        parents.append(PathExpectation(current, kind))
        current = current.parent
    return tuple(reversed(parents))


def make_action(root, label, payload, execute, changes, reads, targets):
    expectations = tuple(
        PathExpectation(target, tree_inventory(target)[0])
        for target in targets
    )
    parents = {}
    for target in changes:
        for expected in parents_for(root, target):
            parents.setdefault(identity(expected.path), expected)
    return PlannedAction(
        label, MappingProxyType(dict(payload)), execute,
        tuple(changes), tuple(reads), expectations, tuple(parents.values()),
    )


def patch_actions(root, operations):
    actions = []
    for operation in operations:
        kind = operation["op"]
        source = project_path(root, operation["path"])
        targets = [source]
        changes, reads = [source], []
        if kind in DUAL_PATH_OPS:
            target = project_path(root, operation["new_path"])
            targets.append(target)
            execute = not (
                entity_type(target) != "absent"
                and operation.get("if_exists", "error") == "skip"
            )
            if kind in ("copy_file", "copy_dir"):
                changes = [target]
                reads = [source]
            else:
                changes = [source, target]
        elif kind == "write_file":
            execute = not (
                entity_type(source) != "absent"
                and operation.get("if_exists", "error") == "skip"
            )
        elif kind == "create_dir":
            execute = entity_type(source) == "absent"
        else:
            execute = True
        actions.append(make_action(
            root, f'id="{operation["id"]}" {kind}', operation, execute,
            changes if execute else (), reads if execute else (), targets,
        ))
    return tuple(actions)


def build_plan(root, actions):
    objects = {}
    associations = []
    for action in actions:
        keys = []
        for target in action.changes:
            missing_parent = next((
                expected.path for expected in action.parents
                if expected.kind == "absent" and contains(expected.path, target)
            ), None)
            protected = missing_parent or target
            key = identity(protected)
            if key not in objects:
                objects[key] = ProtectedObject(
                    relative_path(root, protected), entity_type(protected),
                )
            keys.append(key)
        associations.append(tuple(keys))
    retained, aliases = {}, {}
    for key, protected in sorted(
        objects.items(),
        key=lambda pair: (len(Path(pair[1].path).parts), pair[1].path),
    ):
        ancestor = next((
            retained_key for retained_key, previous in retained.items()
            if contains(root / previous.path, root / protected.path)
        ), None)
        if ancestor is None:
            retained[key] = protected
            aliases[key] = key
        else:
            if retained[ancestor].kind != "absent":
                raise ValueError(
                    f"保护范围非法重叠：{retained[ancestor].path} / {protected.path}"
                )
            aliases[key] = ancestor
    return TaskPlan(
        root, tuple(actions), tuple(retained.values()),
        tuple(tuple(dict.fromkeys(
            retained[aliases[key]].path for key in keys
        )) for keys in associations),
    )


def plan_patch(root, operations):
    root = checked_root(root)
    return build_plan(root, patch_actions(root, operations))


def plan_restore(root, entries):
    actions = []
    for entry in entries:
        target = project_path(root, entry["path"])
        execute = not (
            entry["original_type"] == "absent" and entity_type(target) == "absent"
        )
        actions.append(make_action(
            root, entry["path"], entry, execute,
            (target,) if execute else (), (), (target,),
        ))
    return build_plan(root, actions)


def created_parent_keys(plan, completed):
    # 只接受前序实际动作必然创建的必要目录，不按整个保护祖先授权。
    created = set()
    for index in completed:
        action = plan.actions[index]
        if not action.execute:
            continue
        created.update(
            identity(expected.path)
            for expected in action.parents if expected.kind == "absent"
        )
        if action.payload.get("op") == "create_dir":
            created.update(identity(target) for target in action.changes)
    return created


def recheck_action(plan, index, completed):
    checked_root(plan.root)
    action = plan.actions[index]
    for expected in action.expectations:
        actual = tree_inventory(expected.path)[0]
        if actual != expected.kind:
            raise ValueError(
                f"{action.label} 规划后对象类型或存在性变化：{expected.path}；"
                f"expected={expected.kind}, actual={actual}"
            )
    created = created_parent_keys(plan, completed)
    for expected in action.parents:
        checked_chain(expected.path)
        actual = entity_type(expected.path)
        if actual == expected.kind:
            continue
        if (
            expected.kind == "absent" and actual == "directory"
            and identity(expected.path) in created
        ):
            continue
        raise ValueError(
            f"{action.label} 必要父目录偏离固定计划：{expected.path}；"
            f"expected={expected.kind}, actual={actual}"
        )
    return action


def recheck_plan(plan):
    # 用户确认后、任何业务改变前检查全部固定范围；不重新规划。
    for index in range(len(plan.actions)):
        recheck_action(plan, index, set())


def check_isolation(plan, task_root=None, source_root=None):
    if task_root is not None:
        checked_chain(task_root)
    for action in plan.actions:
        for target in action.changes:
            if source_root is not None and overlaps(target, source_root):
                raise ValueError(f"恢复动作会破坏源恢复材料：{target}")
        if task_root is not None:
            for source in action.reads:
                if overlaps(source, task_root):
                    raise ValueError(f"只读复制源包含本次备份范围：{source}")
    if task_root is not None:
        for protected in plan.objects:
            if overlaps(plan.root / protected.path, task_root):
                raise ValueError(
                    f"备份目录与保护范围重叠：{protected.path} / {task_root}"
                )
        if source_root is not None and overlaps(task_root, source_root):
            raise ValueError("还原前任务目录与源备份重叠")


class RecoveryProtection:
    """一个任务一个写入者；已发布原状态不可覆盖。"""

    def __init__(self, plan, task_kind, backup_dir, source_root=None):
        task_id = uuid.uuid4().hex
        label = "执行修改" if task_kind == "patch" else "备份还原前"
        self.task_root = (
            resolve_backup_base_dir(plan.root, backup_dir)
            / f"{now_stamp()}_{label}_{task_id}"
        )
        check_isolation(plan, self.task_root, source_root)
        if entity_type(self.task_root) != "absent":
            raise ValueError("任务目录必须是独立新目录")
        self.plan = plan
        self.started = False
        self.entries = {}
        self.header = {
            "format": RECOVERY_FORMAT,
            "version": RECOVERY_VERSION,
            "task_id": task_id,
            "task_kind": task_kind,
            "project": {"root": str(plan.root)},
            "backup_root": "files",
        }

    def start(self):
        if self.started:
            return
        checked_chain(self.task_root)
        self.task_root.mkdir(parents=True, exist_ok=False)
        (self.task_root / "files").mkdir()
        current = self.task_root / "files"
        while True:
            sync_directory(current)
            if current == current.parent:
                break
            current = current.parent
        self.started = True

    def prepare_object(self, protected):
        source = project_path(self.plan.root, protected.path)
        original = snapshot(source)
        if original.kind != protected.kind:
            raise ValueError(f"保护对象原状态变化：{protected.path}")
        entry = {
            "path": protected.path,
            "existed": protected.kind != "absent",
            "original_type": protected.kind,
        }
        if protected.kind == "absent":
            return entry
        destination = checked_chain(
            self.task_root / "files" / protected.path,
        )
        if entity_type(destination) != "absent":
            raise ValueError(f"禁止覆盖正式备份材料：{destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.preparing",
        )
        if original.kind == "file":
            shutil.copy2(source, temporary)
        else:
            shutil.copytree(source, temporary, symlinks=False)
        prepared = snapshot(temporary)
        if (
            prepared.kind != original.kind
            or prepared.integrity != original.integrity
        ):
            raise ValueError(f"备份复制完整性不符：{protected.path}")
        # 复制是新的时间边界，重新观察来源以拒绝已检测到的并发变化。
        confirmed = snapshot(source)
        if (
            confirmed.kind != original.kind
            or confirmed.integrity != original.integrity
        ):
            raise ValueError(f"备份期间源对象变化：{protected.path}")
        sync_snapshot(temporary, prepared)
        os.replace(temporary, destination)
        current = destination.parent
        while True:
            sync_directory(current)
            if identity(current) == identity(self.task_root):
                break
            current = current.parent
        entry["backup_path"] = protected.path
        entry["integrity"] = original.integrity
        return entry

    def protect(self, index):
        required = self.plan.action_objects[index]
        pending = [
            protected for protected in self.plan.objects
            if protected.path in required and protected.path not in self.entries
        ]
        if not pending:
            return
        stage, current_object = "任务目录准备", ""
        try:
            self.start()
            added = []
            for protected in pending:
                current_object = protected.path
                stage = "原状态复制、校验及材料持久化"
                added.append(self.prepare_object(protected))
            stage = "恢复清单正文、flush、fsync、关闭及发布"
            document = {
                **self.header,
                "entries": list(self.entries.values()) + added,
            }
            with atomic_text_writer(
                self.task_root / "recovery.json",
                newline="\n", durable_publication=True,
            ) as stream:
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            self.entries.update((entry["path"], entry) for entry in added)
        except (OSError, ValueError) as error:
            if isinstance(error, PublicationError):
                disk_state = (
                    "正式清单已替换，但目录持久化失败"
                    if error.replacement == "replaced"
                    else "正式清单可能已经更新，替换结果无法确认"
                )
            else:
                disk_state = (
                    "本次未完成清单替换；首次发布可能尚无正式清单，"
                    "此前有效清单不被本次准备覆盖"
                )
            raise OSError(
                f"修改前保护失败：阶段={stage}，对象={current_object}。\n"
                f"{disk_state}。当前业务动作未开始，后续修改停止。\n"
                "保留可能已被清单引用的材料；未引用材料不自动恢复。\n"
                f"备份目录：{self.task_root}\n{error}"
            ) from error


def validate_integrity(integrity, kind):
    algorithm = "sha256" if kind == "file" else TREE_ALGORITHM
    if (
        not isinstance(integrity, dict)
        or set(integrity) != {"algorithm", "digest"}
        or integrity["algorithm"] != algorithm
        or not isinstance(integrity["digest"], str)
        or len(integrity["digest"]) != 64
        or any(c not in "0123456789abcdef" for c in integrity["digest"])
    ):
        raise ValueError("恢复材料完整性字段非法")


def check_material(source, entry):
    if entry["original_type"] == "absent":
        return
    material = project_path(source / "files", entry["backup_path"])
    observed = snapshot(material)
    if (
        observed.kind != entry["original_type"]
        or observed.integrity != entry["integrity"]
    ):
        raise ValueError(
            f"恢复材料缺失、类型错误或损坏：{entry['path']}"
        )


def load_recovery(source_root, project_root):
    root = checked_root(project_root)
    source = checked_chain(source_root)
    if entity_type(source) != "directory":
        raise ValueError("备份来源必须是普通目录")
    manifest = checked_chain(source / "recovery.json")
    if entity_type(manifest) != "file":
        raise ValueError(
            "缺少 recovery.json；旧 manifest 不再支持，不自动转换或升级。"
        )
    raw = manifest.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"恢复清单无法解析：{error}") from error
    required = {
        "format", "version", "task_id", "task_kind",
        "project", "backup_root", "entries",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("恢复清单顶层字段非法")
    if (
        document["format"] != RECOVERY_FORMAT
        or type(document["version"]) is not int
        or document["version"] != RECOVERY_VERSION
    ):
        raise ValueError("未知恢复格式或版本")
    if (
        not isinstance(document["task_id"], str) or not document["task_id"]
        or document["task_kind"] not in ("patch", "restore")
        or document["backup_root"] != "files"
        or not isinstance(document["entries"], list)
    ):
        raise ValueError("恢复任务字段非法")
    project = document["project"]
    if (
        not isinstance(project, dict) or set(project) != {"root"}
        or not isinstance(project["root"], str)
        or not os.path.isabs(project["root"])
        or identity(project["root"]) != identity(root)
    ):
        raise ValueError("恢复清单所属项目不一致；不支持跨项目迁移")
    targets, entries = [], []
    for entry in document["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("恢复条目必须是对象")
        kind = entry.get("original_type")
        if kind not in ("file", "directory", "absent"):
            raise ValueError("恢复条目原类型非法")
        fields = {"path", "existed", "original_type"}
        if kind != "absent":
            fields |= {"backup_path", "integrity"}
        if (
            set(entry) != fields
            or type(entry.get("existed")) is not bool
            or entry["existed"] != (kind != "absent")
            or not isinstance(entry.get("path"), str)
        ):
            raise ValueError("恢复条目字段矛盾")
        target = project_path(root, entry["path"])
        if relative_path(root, target) != entry["path"]:
            raise ValueError("恢复路径必须是规范化项目相对路径")
        if any(overlaps(target, previous) for previous in targets):
            raise ValueError("恢复对象重复或父子重叠")
        if overlaps(target, source):
            raise ValueError("恢复范围会破坏源恢复材料")
        targets.append(target)
        if kind != "absent":
            material_root = checked_chain(source / "files")
            if entity_type(material_root) != "directory":
                raise ValueError("恢复材料 files 不存在或类型错误")
            if entry["backup_path"] != entry["path"]:
                raise ValueError("备份引用必须保持原项目路径和文件名")
            validate_integrity(entry["integrity"], kind)
            check_material(source, entry)
        entries.append(entry)
    return root, source, raw, tuple(entries)


def restore_summary(plan):
    summary = {
        "delete_paths": [], "overwrite_paths": [], "restore_dirs": [],
        "restore_paths": [], "create_parent_paths": [],
        "prebackup_absent_dirs": [],
        "has_risk": any(action.execute for action in plan.actions),
        "later_changes_known": False,
    }
    for action in plan.actions:
        if not action.execute:
            continue
        entry = action.payload
        current = action.expectations[0].kind
        if entry["original_type"] == "absent":
            suffix = "（整个当前目录及全部内容）" if current == "directory" else ""
            summary["delete_paths"].append(entry["path"] + suffix)
        else:
            summary["restore_paths"].append(entry["path"])
            if current != "absent":
                suffix = "（移除整个当前目录）" if current == "directory" else ""
                summary["overwrite_paths"].append(entry["path"] + suffix)
            if entry["original_type"] == "directory":
                summary["restore_dirs"].append(entry["path"] + "（整体原树）")
            for expected in action.parents:
                if expected.kind == "absent":
                    relative = relative_path(plan.root, expected.path)
                    if relative not in summary["create_parent_paths"]:
                        summary["create_parent_paths"].append(relative)
    summary["prebackup_absent_dirs"] = [
        protected.path + "（再次恢复时删除其整个当前内容）"
        for protected in plan.objects
        if protected.kind == "absent" and any(
            identity(plan.root / protected.path) == identity(expected.path)
            for action in plan.actions for expected in action.parents
            if expected.kind == "absent"
        )
    ]
    return summary


def remove_entity(path):
    kind = entity_type(path)
    if kind == "directory":
        shutil.rmtree(path)
    elif kind == "file":
        Path(path).unlink()


def perform_restore(root, source, action):
    target = project_path(root, action.payload["path"])
    entry = action.payload
    if entry["original_type"] == "absent":
        remove_entity(target)
        return
    material = project_path(source / "files", entry["backup_path"])
    remove_entity(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if entry["original_type"] == "file":
        shutil.copy2(material, target)
    else:
        shutil.copytree(material, target, symlinks=False)
    restored = snapshot(target)
    if (
        restored.kind != entry["original_type"]
        or restored.integrity != entry["integrity"]
    ):
        raise OSError(f"恢复成果完整性不符：{entry['path']}")