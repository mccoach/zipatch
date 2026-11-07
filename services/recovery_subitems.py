# -*- coding: utf-8 -*-

"""原不存在目录的固定子项、决定及清理投影；不访问磁盘或执行删除。"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from core.recovery_paths import contains


SUBITEM_STATUS_LABELS = MappingProxyType({
    "expected": "本次生成，当前类型及内容符合已完成预期",
    "extra_object": "后来新增或未记录的对象",
    "content_changed": "生成文件内容已经变化",
    "type_changed": "对象类型已经变化",
    "fact_insufficient": "符合预期，但原任务执行事实不足",
    "no_operation": "当前已不存在，无需清理",
})

SUBITEM_DECISION_LABELS = MappingProxyType({
    "undecided": "未决定",
    "restore": "清理当前子项",
    "keep": "保留当前子项",
    "no_operation": "无需操作",
})


@dataclass(frozen=True)
class RestoreSubitem:
    relative: str
    path: Path
    current_kind: str
    expected_kind: str
    current_digest: str
    status: str
    uncertain_units: tuple

    @property
    def requires_decision(self):
        return self.current_kind != "absent"


@dataclass(frozen=True)
class RestoreSubitemScope:
    """固定观察是内容权威；子项只是同次观察及正式预期的派生视图。"""

    object_id: str
    target: Path
    current_records: tuple
    current_file_times: tuple
    items: tuple


@dataclass(frozen=True)
class RestoreCleanupStep:
    """文件只能unlink；目录只能在为空时rmdir，不能递归清理。"""

    item: RestoreSubitem
    removal: str


@dataclass(frozen=True)
class RestoreCleanupPlan:
    """本次决定的不可变投影；不构成正式执行器最终确认资格。"""

    scope: RestoreSubitemScope
    decisions: Mapping
    steps: tuple
    retained_records: tuple
    retained_file_times: tuple
    retained_directories: tuple
    kept_paths: tuple

    @property
    def complete_original_state(self):
        return not self.retained_records


def _records_by_position(records):
    """只检查投影必需的位置唯一性，不代替正式清单结构及材料预检。"""
    indexed = {}
    for record in records:
        if not isinstance(record, tuple) or len(record) != 3:
            raise ValueError("固定子项缺少不可变内容记录")
        relative, kind, digest = record
        if (
            type(relative) is not str
            or type(kind) is not str
            or kind not in ("file", "directory")
            or type(digest) is not str
        ):
            raise ValueError("固定子项内容记录类型非法")
        if relative:
            expression = PurePosixPath(relative)
            if (
                expression.is_absolute()
                or "\\" in relative
                or any(part in ("", ".", "..") for part in relative.split("/"))
                or expression.as_posix() != relative
            ):
                raise ValueError("固定子项相对位置非法")
        if relative in indexed:
            raise ValueError("固定子项相对位置重复")
        indexed[relative] = record
    return indexed


def _parent_position(relative):
    parent = PurePosixPath(relative).parent.as_posix()
    return "" if parent == "." else parent


def _unit_positions(target, associated):
    """一次索引正式单元的具体成果和必要目录，按实际位置关联执行事实。"""
    positions = {}
    for unit in associated:
        fact = unit["fact"]
        uncertain = (
            (unit["id"], fact["state"], fact["changes"])
            if fact["state"] != "completed" else None
        )
        for expression in unit["parent_directories"]:
            path = Path(expression)
            if contains(target, path):
                relative = path.relative_to(target).as_posix()
                relative = "" if relative == "." else relative
                positions.setdefault(relative, []).append(uncertain)
        for outcome in unit["expected"]:
            location = Path(outcome["target"])
            if not contains(target, location):
                continue
            prefix = location.relative_to(target).as_posix()
            prefix = "" if prefix == "." else prefix
            for relative, _, _ in outcome["state"]["records"]:
                position = (
                    prefix + "/" + relative
                    if prefix and relative else prefix or relative
                )
                positions.setdefault(position, []).append(uncertain)
    return positions


def build_restore_subitem_scope(action, associated):
    """消费正式动作的同次比较与观察；只适用于原不存在的当前目录。"""
    original = action.payload["original"]
    current = action.current
    comparison = action.comparison
    if (
        original["kind"] != "absent"
        or current.kind != "directory"
        or comparison is None
        or not comparison.requires_restore
    ):
        raise ValueError("子项清理只适用于原不存在且当前为目录的恢复对象")
    associated = tuple(associated)
    if not associated:
        raise ValueError("固定子项缺少正式关联单元")
    identifier = action.payload["id"]
    if any(identifier not in unit["objects"] for unit in associated):
        raise ValueError("子项关联单元不对应本次原状态对象")

    current_records = tuple(current.content_records)
    expected_records = tuple(comparison.expected_records)
    observed = _records_by_position(current_records)
    expected = _records_by_position(expected_records)
    if not current_records or current_records[0][:2] != ("", "directory"):
        raise ValueError("固定子项缺少当前目录根")
    if any(
        observed.get(_parent_position(relative), (None, None, None))[1]
        != "directory"
        for relative in observed if relative
    ):
        raise ValueError("固定子项缺少当前普通父目录")

    unit_positions = _unit_positions(current.path, associated)
    items = []
    for relative in sorted(
        set(observed) | set(expected),
        key=lambda value: value.encode("utf-8"),
    ):
        actual = observed.get(relative)
        projected = expected.get(relative)
        current_kind = actual[1] if actual is not None else "absent"
        expected_kind = projected[1] if projected is not None else "absent"
        # 同一单元可能经父目录和具体成果重复关联本位置。
        # changes包含只读映射，不是可哈希键；正式编号唯一，
        # 按编号保留首次关联，不复制或重建执行事实。
        uncertain_by_id = {}
        for value in unit_positions.get(relative, ()):
            if value is not None:
                uncertain_by_id.setdefault(value[0], value)
        uncertain = tuple(uncertain_by_id.values())
        if actual is None:
            status = "no_operation"
        elif projected is None:
            status = "extra_object"
        elif actual[1] != projected[1]:
            status = "type_changed"
        elif actual[1] == "file" and actual[2] != projected[2]:
            status = "content_changed"
        else:
            if relative not in unit_positions:
                raise ValueError("生成子项缺少对应的正式执行依据")
            status = "fact_insufficient" if uncertain else "expected"
        items.append(RestoreSubitem(
            relative,
            current.path / relative if relative else current.path,
            current_kind, expected_kind,
            actual[2] if actual is not None else "",
            status, uncertain,
        ))
    return RestoreSubitemScope(
        identifier, current.path, current_records,
        tuple(current.file_times), tuple(items),
    )


def initial_subitem_decisions(scope):
    if not isinstance(scope, RestoreSubitemScope):
        raise TypeError("子项决定必须消费固定子项范围")
    return MappingProxyType({
        item.relative: "undecided" if item.requires_decision else "no_operation"
        for item in scope.items
    })


def validate_subitem_decisions(scope, decisions):
    if not isinstance(scope, RestoreSubitemScope):
        raise TypeError("子项决定必须消费固定子项范围")
    if not isinstance(decisions, Mapping):
        raise TypeError("子项决定必须是位置到决定的映射")
    required = {item.relative: item.requires_decision for item in scope.items}
    if set(decisions) != set(required):
        raise ValueError("子项决定必须准确覆盖本次固定范围")
    for relative, needed in required.items():
        decision = decisions[relative]
        if type(decision) is not str or decision not in SUBITEM_DECISION_LABELS:
            raise ValueError("子项决定非法")
        if needed and decision == "no_operation":
            raise ValueError("当前存在子项不能声明无需操作")
        if not needed and decision != "no_operation":
            raise ValueError("当前缺失子项必须保持无需操作")
    return required


def change_subitem_decisions(
    scope, decisions, relatives, decision, *,
    selection="selected", replace_decisions=False,
):
    """原子接受本批决定；目录清理只指空目录移除，不授权删除其后代。"""
    required = validate_subitem_decisions(scope, decisions)
    if type(decision) is not str or decision not in ("restore", "keep"):
        raise ValueError("子项只能选择清理或保留")
    if type(selection) is not str or selection not in ("selected", "remaining", "all"):
        raise ValueError("子项决定范围非法")
    if type(replace_decisions) is not bool:
        raise TypeError("全部重设许可必须是布尔值")
    if not isinstance(relatives, (tuple, list)):
        raise TypeError("选中子项位置必须是数组或元组")
    relatives = tuple(relatives)
    if any(type(relative) is not str for relative in relatives):
        raise ValueError("选中子项位置必须是字符串")
    if len(set(relatives)) != len(relatives):
        raise ValueError("选中子项位置重复")

    if selection == "selected":
        if not relatives:
            raise ValueError("请先选择至少一个子项")
        if replace_decisions:
            raise ValueError("全部重设许可只用于全部设置")
        if any(relative not in required for relative in relatives):
            raise ValueError("选中子项不属于本次固定范围")
        if any(not required[relative] for relative in relatives):
            raise ValueError("当前缺失子项不能设置清理或保留")
        selected = relatives
    else:
        if relatives:
            raise ValueError("剩余或全部设置不得夹带选中位置")
        if selection == "remaining":
            if replace_decisions:
                raise ValueError("剩余设置不能覆盖已有决定")
            selected = tuple(
                relative for relative, value in decisions.items()
                if value == "undecided"
            )
        else:
            selected = tuple(
                relative for relative, needed in required.items() if needed
            )
            if not replace_decisions and any(
                decisions[relative] in ("restore", "keep")
                and decisions[relative] != decision
                for relative in selected
            ):
                raise ValueError("全部设置将重设已有相反子项决定，必须明确许可")

    candidate = dict(decisions)
    for relative in selected:
        candidate[relative] = decision
    return MappingProxyType(candidate)


def selected_cleanup_plan(scope, decisions):
    """一次投影文件清理和自底向上的空目录清理，不生成执行资格。"""
    validate_subitem_decisions(scope, decisions)
    if any(value == "undecided" for value in decisions.values()):
        raise ValueError("仍有子项未决定，不能形成清理方案")
    indexed = {item.relative: item for item in scope.items}
    current = _records_by_position(scope.current_records)
    retained = {
        relative for relative in current if decisions[relative] == "keep"
    }
    # 每个保留对象的全部当前目录祖先必须留下。
    pending = list(retained)
    while pending:
        relative = pending.pop()
        if relative == "":
            continue
        parent = _parent_position(relative)
        if current.get(parent, (None, None, None))[1] != "directory":
            raise ValueError("保留子项缺少固定普通父目录")
        if parent not in retained:
            retained.add(parent)
            pending.append(parent)

    files = sorted(
        (
            item for item in scope.items
            if item.current_kind == "file"
            and decisions[item.relative] == "restore"
        ),
        key=lambda item: item.relative.encode("utf-8"),
    )
    directories = sorted(
        (
            item for item in scope.items
            if item.current_kind == "directory"
            and decisions[item.relative] == "restore"
            and item.relative not in retained
        ),
        key=lambda item: (
            -len(PurePosixPath(item.relative).parts),
            item.relative.encode("utf-8"),
        ),
    )
    steps = tuple(
        RestoreCleanupStep(item, "file") for item in files
    ) + tuple(
        RestoreCleanupStep(item, "empty_directory") for item in directories
    )
    retained_records = tuple(
        record for record in scope.current_records if record[0] in retained
    )
    retained_file_times = tuple(
        record for record in scope.current_file_times if record[0] in retained
    )
    return RestoreCleanupPlan(
        scope, MappingProxyType(dict(decisions)), steps,
        retained_records, retained_file_times,
        tuple(
            indexed[relative].path
            for relative in sorted(retained, key=lambda value: value.encode("utf-8"))
            if current[relative][1] == "directory"
        ),
        tuple(
            item.path for item in scope.items
            if decisions[item.relative] == "keep"
        ),
    )


def format_subitem_scope(scope):
    """正式预演消费固定分类；只展示，不推定用户决定或删除授权。"""
    if not isinstance(scope, RestoreSubitemScope):
        raise TypeError("子项展示必须消费固定范围")
    lines = [
        f"原不存在目录的固定子项：{scope.object_id}；{scope.target}",
        "以下分类来自本次固定观察，不是子项删除授权。",
    ]
    for item in scope.items:
        lines.append(
            f"- {item.path}；当前={item.current_kind}；"
            f"预期={item.expected_kind}；{SUBITEM_STATUS_LABELS[item.status]}"
        )
        from services.recovery_comparison import format_execution_fact
        for identifier, state, changes in item.uncertain_units:
            lines.append("  原任务事实不足：")
            lines.extend(
                "  " + line
                for line in format_execution_fact(identifier, state, changes)
            )
    return "\n".join(lines)