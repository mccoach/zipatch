# -*- coding: utf-8 -*-

"""最终version 3严格结构、编码及实际步骤事实投影。

不读取旧版本，不查询磁盘，不发布依据或执行恢复。
结构校验不是文件系统执行证明；完成仍须由所属协调
消费真实步骤并完成必要最终内容、来源和时间核验。
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from core.content_publication import ContentPublicationResult
from core.filesystem_changes import FilesystemChangeResult
from core.internal_materials import InternalMaterialResult
from core.name_moves import NativeNameMoveResult
from core.protection_projection import (
    ContentState, ProtectionProjection, validate_absolute_path,
    validate_content_state,
)
from core.recovery_paths import contains, identity, overlaps


FORMAT = "ZIPATCH_RECOVERY"
VERSION = 3
_OBJECT_ID = re.compile(r"o[0-9]{6,}")
_UNIT_ID = re.compile(r"u[0-9]{6,}")
_FACT_STATES = frozenset(("prepared", "started", "completed", "uncertain"))
_CHANGE_KINDS = frozenset((
    "content", "create_directory", "remove_file", "remove_empty_directory",
    "move_name", "set_directory_attributes",
))
_CONTENT_PHASES = frozenset((
    "completed",
    "完整成果及固定前提复核",
    "原子名称替换",
    "正式成果完整性核验",
    "正式成果目录持久化确认",
    "原对象打开",
    "打开句柄身份及固定原内容核验",
    "破坏性写入前业务复核",
    "首次写入前完整独立应急成果复核",
    "原对象完整内容写入",
    "原对象准确长度调整",
    "原对象刷新及文件同步",
    "正式文件修改时间设置",
    "原对象完整成果及身份核验",
    "原对象基础权限设置及核验",
    "原对象关闭",
    "原对象关闭后属性核验",
    "原对象所在目录持久化确认",
    "完整应急成果清理",
))


@dataclass(frozen=True, init=False)
class RecoveryDocument:
    value: Mapping
    raw: bytes

    def __init__(self, *arguments, **keywords):
        raise TypeError("清单必须通过正式构建或严格读取创建")


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({
            key: _freeze(member) for key, member in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(member) for member in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(member) for key, member in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(member) for member in value]
    return value


def _document(value, raw):
    document = object.__new__(RecoveryDocument)
    object.__setattr__(document, "value", _freeze(value))
    object.__setattr__(document, "raw", raw)
    return document


def _fields(value, names, description):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError(f"{description}必须具有准确字段")


def _boolean(value, description):
    if type(value) is not bool:
        raise ValueError(f"{description}必须是布尔值")


def _absolute(value):
    if type(value) is not str or not value:
        raise ValueError("恢复位置必须是明确绝对字符串")
    path = Path(value)
    validate_absolute_path(path)
    if str(path) != value:
        raise ValueError("恢复位置必须采用规范路径表达")
    return path


def _strings(value, description, *, nonempty=False):
    if (
        type(value) is not list or nonempty and not value
        or any(type(member) is not str or not member for member in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{description}必须是唯一有效字符串数组")


def _state_value(state):
    return {
        "kind": state.kind,
        "records": _plain(state.records),
        "integrity": _plain(state.integrity),
    }


def _validate_state(value):
    _fields(value, ("kind", "records", "integrity"), "内容状态")
    if type(value["records"]) is not list or any(
        type(record) is not list for record in value["records"]
    ):
        raise ValueError("内容记录必须是数组")
    if value["integrity"] is not None:
        _fields(value["integrity"], ("algorithm", "digest"), "内容完整性")
    state = ContentState(
        value["kind"], tuple(tuple(record) for record in value["records"]),
        value["integrity"],
    )
    validate_content_state(state)


def _positions(target, state):
    return {
        identity(target / relative): (target / relative, kind, digest)
        for relative, kind, digest in state["records"]
    }


def _within(position, root):
    return contains(root, position)


def _validate_content(change):
    content = change["content"]
    _fields(content, (
        "mechanism", "phase", "mutation_attempted", "replacement",
        "content_completed", "confirmation_completed",
        "emergency_path", "emergency_state",
    ), "内容步骤")
    for field in (
        "mutation_attempted", "content_completed", "confirmation_completed",
    ):
        _boolean(content[field], field)
    mechanism = content["mechanism"]
    replacement = content["replacement"]
    emergency = content["emergency_state"]
    phase = content["phase"]
    if type(phase) is not str or phase not in _CONTENT_PHASES:
        raise ValueError("内容阶段不是正式有限阶段")
    if type(mechanism) is not str or mechanism not in ("atomic_replace", "in_place"):
        raise ValueError("内容机制非法")
    if type(replacement) is not str or replacement not in (
        "not_attempted", "unknown", "replaced",
    ):
        raise ValueError("名称替换状态非法")
    if type(emergency) is not str or emergency not in (
        "not_required", "retained", "removed", "unknown",
    ):
        raise ValueError("应急状态非法")
    if change["attempted"] != content["mutation_attempted"]:
        raise ValueError("内容尝试事实矛盾")
    if content["content_completed"] and not content["mutation_attempted"]:
        raise ValueError("零改变尝试不能确认完整正文")
    if content["confirmation_completed"] and not content["content_completed"]:
        raise ValueError("确认完成必须先具有完整正文")
    if mechanism == "in_place":
        if replacement != "not_attempted" or emergency == "not_required":
            raise ValueError("原地步骤不得具有名称替换或缺少应急")
        path = _absolute(content["emergency_path"])
        target = _absolute(change["target"])
        if identity(path.parent) != identity(target.parent) or identity(path) == identity(target):
            raise ValueError("应急必须是目标同目录不同位置")
    else:
        if content["emergency_path"] is not None or emergency != "not_required":
            raise ValueError("普通替换不得携带应急")
        if content["mutation_attempted"] != (replacement != "not_attempted"):
            raise ValueError("普通替换尝试与名称状态矛盾")
        if content["content_completed"] and replacement != "replaced":
            raise ValueError("普通正文完成缺少正常名称替换事实")
    if change["completed"] and not (
        content["content_completed"]
        and content["confirmation_completed"]
        and emergency in ("not_required", "removed")
        and phase == "completed"
    ):
        raise ValueError("正常内容步骤仍有正文、确认或清理未完成")


def _initial_positions(objects, references, effect_targets):
    """保护范围不是步骤授权；只取本单元具体effect内的最初成员。"""
    positions = {}
    for number in references:
        root, state = objects[number]
        for key, record in _positions(root, state).items():
            if any(_within(record[0], target) for target in effect_targets):
                positions[key] = record
    return positions


def _validate_changes(
    fact, initial, desired, effect_targets, parent_keys, tree_keys,
    previous_created,
):
    """单元类型流消费前序真实创建，不由计划补造目录存在事实。"""
    current = dict(initial)
    created = dict(previous_created)
    for key, path in previous_created.items():
        if key in parent_keys:
            current[key] = (path, "directory", "")
    failed = False
    changed_positions = set()
    for change in fact["changes"]:
        _fields(change, (
            "kind", "source", "target", "attempted", "completed", "content",
        ), "实际步骤")
        kind = change["kind"]
        if type(kind) is not str or kind not in _CHANGE_KINDS:
            raise ValueError("实际步骤类别非法")
        _boolean(change["attempted"], "attempted")
        _boolean(change["completed"], "completed")
        if change["completed"] and not change["attempted"]:
            raise ValueError("正常步骤不能未尝试")
        if failed:
            raise ValueError("未完成步骤之后不得记录后续工作")
        target = _absolute(change["target"])
        key = identity(target)
        if kind == "content":
            if (
                change["source"] is not None
                or key not in desired
                or desired[key][1] != "file"
                or current.get(key, (None, "absent", ""))[1] == "directory"
            ):
                raise ValueError("内容步骤不对应当前可发布的具体预期文件")
            _validate_content(change)
            required_parents = {
                parent for parent in parent_keys
                if contains(Path(parent), target)
            }
            if not required_parents.issubset(created):
                raise ValueError("内容步骤之前缺少必要父目录真实正常创建事实")
        else:
            if change["content"] is not None:
                raise ValueError("非内容步骤不得携带内容事实")
            if kind == "move_name":
                source = _absolute(change["source"])
                source_key = identity(source)
                source_record = current.get(source_key)
                target_desired = desired.get(key)
                if (
                    overlaps(source, target)
                    or source_record is None
                    or target_desired is None
                    or source_record[1] != target_desired[1]
                    or not any(identity(source) == identity(root) for root in effect_targets)
                    or not any(identity(target) == identity(root) for root in effect_targets)
                    or source_key in desired
                ):
                    raise ValueError("名称步骤两端不对应本单元固定effect")
                source_members = [
                    record for record in current.values() if contains(source, record[0])
                ]
                relocated = {
                    identity(target / path.relative_to(source)): (
                        target / path.relative_to(source), member_kind, digest,
                    )
                    for path, member_kind, digest in source_members
                }
                expected_members = {
                    position: record for position, record in desired.items()
                    if contains(target, record[0])
                }
                if relocated != expected_members:
                    raise ValueError("原生名称结果与固定来源内容或结构矛盾")
                if source_record[1] == "directory" and any(
                    contains(target, record[0]) for record in current.values()
                ):
                    raise ValueError("原生目录名称移动前目标必须已不存在")
            else:
                if change["source"] is not None:
                    raise ValueError("只有名称移动可以携带source")
                actual_kind = current.get(key, (None, "absent", ""))[1]
                if kind == "create_directory":
                    if (
                        key not in parent_keys | tree_keys
                        or actual_kind != "absent" or key in created
                    ):
                        raise ValueError("目录创建越出固定计划、目标已存在或重复登记")
                elif kind == "set_directory_attributes":
                    if key not in tree_keys or actual_kind != "directory":
                        raise ValueError("目录属性不对应已存在成果目录")
                elif kind == "remove_file":
                    if actual_kind != "file" or key not in initial:
                        raise ValueError("文件移除越出本单元最初具体范围")
                elif kind == "remove_empty_directory":
                    if actual_kind != "directory" or key not in initial:
                        raise ValueError("目录移除越出本单元最初具体范围")
                    if any(
                        position != key and contains(target, record[0])
                        for position, record in current.items()
                    ):
                        raise ValueError("空目录步骤之前仍有未移除后代")

        failed = not change["completed"]
        if failed:
            continue
        changed_positions.add(key)
        if kind == "content":
            current[key] = desired[key]
        elif kind == "create_directory":
            current[key] = (target, "directory", "")
            created[key] = target
        elif kind in ("remove_file", "remove_empty_directory"):
            del current[key]
        elif kind == "move_name":
            for position in tuple(current):
                if contains(source, current[position][0]) or contains(target, current[position][0]):
                    del current[position]
            current.update(relocated)
            changed_positions.add(source_key)

    if fact["state"] != "completed":
        return created
    if failed or not fact["changes"]:
        raise ValueError("正常单元缺少完整实际步骤")
    if not parent_keys.issubset(created):
        raise ValueError("正常单元必要父目录缺少真实正常创建事实")
    actual_effects = {
        key: record for key, record in current.items()
        if any(contains(target, record[0]) for target in effect_targets)
    }
    if actual_effects != desired:
        raise ValueError("正常单元实际步骤未覆盖具体预期")
    # 即使字节预期与原状态相同，实际改变单元仍须具有自己的工作步骤。
    for target in effect_targets:
        if not any(
            contains(target, Path(position))
            or position == identity(target)
            for position in changed_positions
        ):
            raise ValueError("正常单元遗漏具体effect的工作步骤")
    return created


def validate_document(value):
    _fields(value, (
        "format", "version", "task_id", "task_kind", "objects", "units",
    ), "恢复清单")
    if (
        value["format"] != FORMAT
        or type(value["version"]) is not int
        or value["version"] != VERSION
    ):
        raise ValueError("只支持严格version 3；旧格式不转换或适配")
    if (
        type(value["task_id"]) is not str or not value["task_id"]
        or type(value["task_kind"]) is not str
        or value["task_kind"] not in ("patch", "restore", "split")
        or type(value["objects"]) is not list
        or type(value["units"]) is not list
    ):
        raise ValueError("恢复任务字段非法")

    objects = {}
    roots = []
    for member in value["objects"]:
        _fields(member, (
            "id", "target", "original", "material", "file_times",
        ), "原状态对象")
        number = member["id"]
        if (
            type(number) is not str or _OBJECT_ID.fullmatch(number) is None
            or number in objects
        ):
            raise ValueError("原状态编号非法或重复")
        target = _absolute(member["target"])
        if any(overlaps(target, root) for root in roots):
            raise ValueError("原状态对象重复或父子重叠")
        roots.append(target)
        _validate_state(member["original"])
        if member["material"] != (
            None if member["original"]["kind"] == "absent" else number
        ):
            raise ValueError("原材料必须由对象编号唯一定位")
        times = member["file_times"]
        if type(times) is not list or any(
            type(record) is not list or len(record) != 2
            or type(record[0]) is not str or type(record[1]) is not int
            for record in times
        ):
            raise ValueError("正式文件时间记录非法")
        if [record[0] for record in times] != [
            record[0] for record in member["original"]["records"]
            if record[1] == "file"
        ]:
            raise ValueError("正式时间必须准确覆盖所有原文件")
        objects[number] = (target, member["original"])

    seen_units = set()
    seen_members = set()
    referenced = set()
    expected_targets = []
    created_directories = {}
    for unit in value["units"]:
        _fields(unit, (
            "id", "members", "objects", "expected",
            "parent_directories", "tree_directories", "fact",
        ), "执行单元")
        number = unit["id"]
        if (
            type(number) is not str or _UNIT_ID.fullmatch(number) is None
            or number in seen_units
        ):
            raise ValueError("执行单元编号非法或重复")
        seen_units.add(number)
        _strings(unit["members"], "共同成员", nonempty=True)
        if seen_members.intersection(unit["members"]):
            raise ValueError("共同成员属于多个单元")
        seen_members.update(unit["members"])
        references = unit["objects"]
        _strings(references, "原状态引用", nonempty=True)
        if any(identifier not in objects for identifier in references):
            raise ValueError("未知原状态引用")
        referenced.update(references)
        if type(unit["expected"]) is not list or not unit["expected"]:
            raise ValueError("单元缺少具体预期")

        targets = []
        desired = {}
        generated = []
        related = set()
        for expected in unit["expected"]:
            _fields(expected, ("target", "state"), "具体预期")
            target = _absolute(expected["target"])
            if any(overlaps(target, previous) for previous in expected_targets):
                raise ValueError("具体预期重复或父子重叠")
            expected_targets.append(target)
            targets.append(target)
            matching = {
                identifier for identifier in references
                if contains(objects[identifier][0], target)
            }
            if not matching:
                raise ValueError("具体预期越出关联保护对象")
            related.update(matching)
            _validate_state(expected["state"])
            desired.update(_positions(target, expected["state"]))
            if expected["state"]["kind"] != "absent":
                generated.append(target)
        if related != set(references):
            raise ValueError("原状态引用没有对应具体预期")

        _strings(unit["parent_directories"], "必要父目录")
        _strings(unit["tree_directories"], "成果树目录")
        parent_keys = set()
        for expression in unit["parent_directories"]:
            parent = _absolute(expression)
            key = identity(parent)
            if key in parent_keys or not any(
                identity(parent) != identity(target) and contains(parent, target)
                for target in generated
            ):
                raise ValueError("必要父目录重复或不是生成目标严格祖先")
            parent_keys.add(key)
        required_trees = {
            key for key, record in desired.items() if record[1] == "directory"
        }
        tree_keys = set()
        for expression in unit["tree_directories"]:
            key = identity(_absolute(expression))
            if key in tree_keys or key in parent_keys or key not in required_trees:
                raise ValueError("成果树目录重复或不属于具体预期")
            tree_keys.add(key)
        if tree_keys != required_trees:
            raise ValueError("成果树目录必须准确覆盖具体预期")

        fact = unit["fact"]
        _fields(fact, ("state", "changes"), "执行事实")
        if type(fact["state"]) is not str or fact["state"] not in _FACT_STATES:
            raise ValueError("执行事实状态非法")
        if type(fact["changes"]) is not list:
            raise ValueError("实际步骤必须是有序数组")
        if fact["state"] == "prepared" and fact["changes"]:
            raise ValueError("prepared不得带工作步骤")
        initial = _initial_positions(objects, references, targets)
        created_directories = _validate_changes(
            fact, initial, desired, targets, parent_keys, tree_keys,
            created_directories,
        )
    if referenced != set(objects):
        raise ValueError("原状态对象必须关联正式单元")


def _build_validated(value):
    validate_document(value)
    raw = (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    return _document(value, raw)


def build_document(
    projection, task_root, task_id, task_kind, material_results, *,
    unit_ids=None, previous_document=None,
):
    """最初状态直接消费唯一投影；引用只消费正常内部材料结果。"""
    if not isinstance(projection, ProtectionProjection):
        raise TypeError("清单必须消费正式ProtectionProjection")
    task = _absolute(str(task_root))
    if not isinstance(material_results, Mapping):
        raise TypeError("材料结果必须按对象编号关联")
    selected = (
        tuple(unit.unit_id for unit in projection.units)
        if unit_ids is None else tuple(unit_ids)
    )
    if (
        any(type(number) is not str for number in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("所选单元编号非法或重复")
    units = tuple(unit for unit in projection.units if unit.unit_id in selected)
    if tuple(unit.unit_id for unit in units) != selected:
        raise ValueError("所选单元必须来自原投影并保持原顺序")
    needed = {number for unit in units for number in unit.object_ids}
    objects = tuple(
        member for member in projection.objects if member.object_id in needed
    )
    required = {
        member.object_id for member in objects if member.original_type != "absent"
    }
    if set(material_results) != required:
        raise ValueError("材料结果必须准确覆盖本次已保护既有对象")
    entries = []
    for member in objects:
        if overlaps(member.target, task):
            raise ValueError("任务材料与工作原对象重叠")
        if member.original_type != "absent":
            receipt = material_results[member.object_id]
            if not isinstance(receipt, InternalMaterialResult):
                raise TypeError("材料引用必须消费InternalMaterialResult")
            if (
                not receipt.succeeded
                or identity(receipt.source) != identity(member.target)
                or identity(receipt.target) != identity(task / "files" / member.object_id)
                or receipt.kind != member.original_type
            ):
                raise ValueError("材料未正常完成或不对应固定对象及编号")
        entries.append({
            "id": member.object_id, "target": str(member.target),
            "original": _state_value(member.original),
            "material": None if member.original_type == "absent" else member.object_id,
            "file_times": _plain(member.file_times),
        })
    value = {
        "format": FORMAT, "version": VERSION,
        "task_id": task_id, "task_kind": task_kind,
        "objects": entries,
        "units": [{
            "id": unit.unit_id,
            "members": list(unit.members),
            "objects": list(unit.object_ids),
            "expected": [{
                "target": str(effect.before.path),
                "state": _state_value(effect.after),
            } for effect in unit.effects],
            "parent_directories": list(map(str, unit.parent_directories)),
            "tree_directories": list(map(str, unit.tree_directories)),
            "fact": {"state": "prepared", "changes": []},
        } for unit in units],
    }
    if previous_document is not None:
        if not isinstance(previous_document, RecoveryDocument):
            raise TypeError("扩展依据必须消费已发布RecoveryDocument")
        previous = previous_document.value
        if (
            previous["task_id"] != task_id
            or previous["task_kind"] != task_kind
        ):
            raise ValueError("不得把其他任务的执行事实并入本次依据")
        old_objects = {
            member["id"]: _plain(member) for member in previous["objects"]
        }
        new_objects = {member["id"]: member for member in entries}
        if any(
            new_objects.get(number) != member
            for number, member in old_objects.items()
        ):
            raise ValueError("扩展依据不得删除或改变已发布最初状态")
        old_units = tuple(previous["units"])
        if len(old_units) > len(value["units"]):
            raise ValueError("扩展依据不得删除已发布执行单元")
        for old, current in zip(old_units, value["units"]):
            before = _plain(old)
            old_fact = before.pop("fact")
            description = {
                key: member for key, member in current.items()
                if key != "fact"
            }
            if before != description:
                raise ValueError("扩展依据不得改变已发布单元及其顺序")
            current["fact"] = old_fact
    # 前序事实与新增单元归入同一最终候选后，只校验编码一次。
    return _build_validated(value)


def project_change(result):
    if isinstance(result, ContentPublicationResult):
        return {
            "kind": "content", "source": None, "target": str(result.target),
            "attempted": result.mutation_attempted,
            "completed": result.succeeded,
            "content": {
                "mechanism": result.mechanism, "phase": result.phase,
                "mutation_attempted": result.mutation_attempted,
                "replacement": result.replacement,
                "content_completed": result.content_completed,
                "confirmation_completed": result.confirmation_completed,
                "emergency_path": (
                    None if result.emergency_path is None else str(result.emergency_path)
                ),
                "emergency_state": result.emergency_state,
            },
        }
    if isinstance(result, FilesystemChangeResult):
        return {
            "kind": result.kind, "source": None, "target": str(result.target),
            "attempted": result.attempted, "completed": result.succeeded,
            "content": None,
        }
    if isinstance(result, NativeNameMoveResult):
        return {
            "kind": "move_name", "source": str(result.source),
            "target": str(result.target),
            "attempted": result.attempted, "completed": result.succeeded,
            "content": None,
        }
    raise TypeError("工作事实必须消费正式工作步骤结果，不接受材料、日志或计划")


def with_execution_fact(document, unit_id, state, results=()):
    if not isinstance(document, RecoveryDocument):
        raise TypeError("事实更新必须消费RecoveryDocument")
    value = _plain(document.value)
    unit = next((
        member for member in value["units"] if member["id"] == unit_id
    ), None)
    if unit is None:
        raise ValueError("事实单元不属于当前依据")
    results = tuple(results)
    # 类型先于属性消费，非法输入不以AttributeError代替契约拒绝。
    changes = [project_change(result) for result in results]
    previous = unit["fact"]["state"]
    if state == "started":
        if previous != "prepared" or results:
            raise ValueError("started必须在首次工作改变前从prepared形成")
    elif state in ("completed", "uncertain"):
        if previous != "started":
            raise ValueError("结束事实只能消费已started单元")
        if state == "completed" and (
            not changes or any(not change["completed"] for change in changes)
        ):
            raise ValueError("正常单元必须消费正常实际步骤")
    else:
        raise ValueError("不允许事实回退、重设或未知转换")
    unit["fact"] = {"state": state, "changes": changes}
    return _build_validated(value)


def encode_document(document):
    if not isinstance(document, RecoveryDocument):
        raise TypeError("清单编码必须消费RecoveryDocument")
    return document.raw


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("清单包含重复JSON字段")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"清单不接受非JSON数值常量：{value}")


def decode_document(raw):
    if not isinstance(raw, bytes):
        raise TypeError("清单必须消费不可变bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"恢复清单无法解析：{error}") from error
    validate_document(value)
    return _document(value, raw)
