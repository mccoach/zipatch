# -*- coding: utf-8 -*-

"""正式恢复原状态、具体预期及当前观察比较；不访问磁盘或执行恢复。"""

from dataclasses import dataclass
from pathlib import Path

from core.recovery_paths import contains


RESTORE_DIFFERENCE_LABELS = {
    "content_changed": "当前内容变化",
    "extra_object": "存在额外对象",
    "missing_object": "预期对象缺失",
    "type_changed": "对象类型变化",
}

RESTORE_STATUS_LABELS = {
    "no_operation": "已达到原始状态，无需操作",
    "time_pending": "原内容已满足，正式文件时间尚需恢复",
    "expected": "符合已完成原任务的操作后预期",
    "different": "当前状态偏离操作后预期",
    "fact_insufficient": "当前符合预期，但原任务执行事实不足",
}


@dataclass(frozen=True)
class RestoreComparison:
    """固定比较由正式动作拥有，不成为第二计划或持久化执行资格。"""

    status: str
    expected_kind: str
    expected_records: tuple
    differences: tuple
    time_differences: tuple
    uncertain_units: tuple

    @property
    def requires_restore(self):
        return self.status != "no_operation"


def index_restore_units(document):
    """同次规划一次建立对象到正式关联单元的索引。"""
    associations = {
        entry["id"]: [] for entry in document["objects"]
    }
    for unit in document["units"]:
        for identifier in unit["objects"]:
            associations[identifier].append(unit)
    return {
        identifier: tuple(units)
        for identifier, units in associations.items()
    }


def _project_expected(entry, associated):
    """归并祖先联合具体成果及必要目录，不授权未记录的后来子项。"""
    target = Path(entry["target"])
    if not associated:
        raise ValueError("恢复比较缺少原状态对象的正式关联单元")
    records = {}

    def add(relative, kind, fingerprint):
        record = (relative, kind, fingerprint)
        previous = records.setdefault(relative, record)
        if previous != record:
            raise ValueError("关联恢复预期在同一内容位置存在矛盾")

    for unit in associated:
        for expression in unit["parent_directories"]:
            directory = Path(expression)
            if contains(target, directory):
                relative = directory.relative_to(target).as_posix()
                add("" if relative == "." else relative, "directory", "")

        for outcome in unit["expected"]:
            location = Path(outcome["target"])
            if not contains(target, location):
                continue
            state = outcome["state"]
            if state["kind"] == "absent":
                continue
            prefix = location.relative_to(target).as_posix()
            prefix = "" if prefix == "." else prefix
            for relative, kind, fingerprint in state["records"]:
                position = (
                    prefix + "/" + relative
                    if prefix and relative else prefix or relative
                )
                add(position, kind, fingerprint)

    if not records:
        return "absent", ()
    if "" not in records:
        raise ValueError("关联恢复预期缺少归并对象的根目录记录")
    ordered = tuple(
        records[position]
        for position in sorted(records, key=lambda value: value.encode("utf-8"))
    )
    return ordered[0][1], ordered


def _state_differences(target, expected_kind, expected_records, observed):
    if expected_kind != observed.kind:
        category = (
            "missing_object" if observed.kind == "absent"
            else "extra_object" if expected_kind == "absent"
            else "type_changed"
        )
        return ((str(target), category),)

    expected = {record[0]: record for record in expected_records}
    current = {record[0]: record for record in observed.content_records}
    differences = []
    for relative in sorted(
        set(expected) | set(current),
        key=lambda value: value.encode("utf-8"),
    ):
        before = expected.get(relative)
        actual = current.get(relative)
        if before is None:
            category = "extra_object"
        elif actual is None:
            category = "missing_object"
        elif before[1] != actual[1]:
            category = "type_changed"
        elif before[1] == "file" and before[2] != actual[2]:
            category = "content_changed"
        else:
            continue
        differences.append((
            str(target / relative if relative else target), category,
        ))
    return tuple(differences)


def compare_restore_object(entry, associated, observed, current_file_times):
    """完整原状态满足优先；内容、时间及原任务完成事实分别表达。"""
    target = Path(entry["target"])
    original = entry["original"]
    original_kind = original["kind"]
    original_records = tuple(tuple(record) for record in original["records"])
    original_times = tuple(tuple(record) for record in entry["file_times"])
    current_file_times = tuple(current_file_times)
    current_times = dict(current_file_times)
    time_differences = tuple(
        (
            str(target / relative if relative else target),
            modified, current_times[relative],
        )
        for relative, modified in original_times
        if relative in current_times and current_times[relative] != modified
    )

    expected_kind, expected_records = _project_expected(entry, associated)
    uncertain_units = tuple(
        (unit["id"], unit["fact"]["state"], unit["fact"]["changes"])
        for unit in associated if unit["fact"]["state"] != "completed"
    )
    differences = _state_differences(
        target, expected_kind, expected_records, observed,
    )
    original_content_matches = (
        original_kind == observed.kind
        and original_records == observed.content_records
    )
    if original_content_matches:
        status = (
            "no_operation"
            if original_times == current_file_times else "time_pending"
        )
    elif differences:
        status = "different"
    elif uncertain_units:
        status = "fact_insufficient"
    else:
        status = "expected"

    return RestoreComparison(
        status, expected_kind, expected_records,
        differences, time_differences, uncertain_units,
    )


def format_restore_comparisons(comparisons):
    """预演与确认共享说明，只消费固定比较，不重新观察。"""
    lines = []
    for target, comparison in comparisons:
        lines.append(
            f"- {target}：{RESTORE_STATUS_LABELS[comparison.status]}"
        )
        if comparison.status == "no_operation":
            lines.append(
                "  原字节、目录结构及全部正式文件时间已经满足；"
                "不覆盖、不删除、不创建工作目录，也不生成还原前材料。"
            )
        elif comparison.status == "time_pending":
            lines.append(
                "  仍需满足正式时间要求；沿现有完整暂存及发布链恢复。"
                "目录仍按整体原树恢复，不表示就地改时间或零字节写入。"
            )
        if comparison.differences:
            lines.append("  相对于原任务操作后预期的差异：")
        for path, category in comparison.differences:
            lines.append(
                f"  - [{RESTORE_DIFFERENCE_LABELS[category]}] {path}"
            )
        for path, original_time, current_time in comparison.time_differences:
            lines.append(
                f"  - [时间追溯差异，不等于内容变化] {path}；"
                f"正式mtime_ns={original_time}；当前mtime_ns={current_time}"
            )
        if comparison.uncertain_units:
            lines.append(
                "  原任务以下单元没有正常完成事实；"
                "不据字节一致认证实际完成，也不剥夺原状态恢复依据："
            )
            for identifier, state, changes in comparison.uncertain_units:
                lines.extend(
                    "  " + line
                    for line in format_execution_fact(identifier, state, changes)
                )
    return tuple(lines)

_CHANGE_LABELS = {
    "content": "完整内容发布",
    "create_directory": "创建目录",
    "remove_file": "移除文件名称",
    "remove_empty_directory": "移除空目录",
    "move_name": "移动名称",
    "set_directory_attributes": "设置目录基础属性",
}


def format_execution_fact(identifier, state, changes):
    """消费严格version 3事实的只读展示，不读取、校验或授权工作。

    changes来自已严格校验文档或其直接派生索引。
    步骤完成、正文确认和必要确认分别展示，不重新推导成功。
    """
    changes = tuple(changes)
    lines = [
        f"- {identifier}；状态={state}；已记录实际步骤={len(changes)}"
    ]
    if not changes:
        lines.append(
            "  无实际步骤记录；不能据此证明工作目标没有改变。"
            "原状态恢复依据仍由正式原对象和引用材料提供。"
        )
        return tuple(lines)

    for number, change in enumerate(changes, 1):
        kind = change["kind"]
        lines.append(
            f"  {number}. {_CHANGE_LABELS[kind]}；目标={change['target']}；"
            "改变调用已发起="
            + ("是" if change["attempted"] else "否")
            + "；步骤正常完成="
            + ("是" if change["completed"] else "否")
        )
        if kind == "move_name":
            lines.append(f"     来源={change['source']}")
        elif kind == "content":
            content = change["content"]
            lines.extend((
                f"     机制={content['mechanism']}；"
                f"阶段={content['phase']}；"
                f"名称替换={content['replacement']}",
                "     完整正文已确认="
                + ("是" if content["content_completed"] else "否")
                + "；必要确认已完成="
                + ("是" if content["confirmation_completed"] else "否")
                + f"；应急状态={content['emergency_state']}",
            ))
            if content["emergency_path"] is not None:
                lines.append(f"     应急成果={content['emergency_path']}")
    lines.append(
        "  以上只表达已记录事实；未正常完成或补充记录失败"
        "不能证明目标没有副作用，也不触发自动重试或回滚。"
    )
    return tuple(lines)
