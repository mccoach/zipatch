# -*- coding: utf-8 -*-

"""现有Windows素材的只读链接分类、原定义及项目路径观察诊断。

不修改权限、链接或正文，不跟随目录链接进行递归枚举。
耗时仅表达当前现场的路径观察，不代表完整业务性能。
"""

import argparse
import json
import os
import stat
import sys
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter


PROJECT_ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.link_paths import inspect_link_path


DEFAULT_SOURCE = Path(
    r"C:\Users\micha\AppData\Local\Temp\Zipatch_UI_i8cnkukq"
)
LINK_KINDS = ("file_symlink", "directory_symlink", "junction")
REPARSE_POINT = 0x400
DIRECTORY_ATTRIBUTE = 0x10
SYMLINK_TAG = 0xA000000C
MOUNT_POINT_TAG = 0xA0000003


def emit(title, facts):
    print(
        title + " " + json.dumps(
            facts, ensure_ascii=False, default=str, allow_nan=False,
        ),
        flush=True,
    )


def error_facts(error):
    return {
        "异常类型": type(error).__name__,
        "原因": str(error),
        "errno": getattr(error, "errno", None),
        "winerror": getattr(error, "winerror", None),
        "原始异常": (
            str(error.__cause__) if error.__cause__ is not None else None
        ),
    }


def raw_kind(attributes):
    """以不跟随属性及Windows标签作独立分类对照。"""
    flags = getattr(attributes, "st_file_attributes", 0)
    tag = getattr(attributes, "st_reparse_tag", None)
    if flags & REPARSE_POINT or stat.S_ISLNK(attributes.st_mode):
        if tag == SYMLINK_TAG:
            return (
                "directory_symlink"
                if flags & DIRECTORY_ATTRIBUTE else "file_symlink"
            )
        if tag == MOUNT_POINT_TAG and flags & DIRECTORY_ATTRIBUTE:
            return "junction"
        return "unknown_reparse"
    if stat.S_ISREG(attributes.st_mode):
        return "file"
    if stat.S_ISDIR(attributes.st_mode):
        return "directory"
    return "special_entity"


def revision(attributes):
    return (
        attributes.st_dev,
        attributes.st_ino,
        attributes.st_mode,
        attributes.st_size,
        attributes.st_mtime_ns,
        attributes.st_ctime_ns,
        getattr(attributes, "st_file_attributes", 0),
        getattr(attributes, "st_reparse_tag", None),
    )


def timed_call(operation):
    start = perf_counter()
    try:
        result = operation()
    except (OSError, ValueError) as error:
        return None, error, (perf_counter() - start) * 1000
    return result, None, (perf_counter() - start) * 1000


def observation_facts(observed):
    return {
        "类别": observed.kind,
        "名义位置": str(observed.nominal_path),
        "实际位置": str(observed.actual_path),
        "对象身份": observed.object_identity,
        "最先缺失位置": observed.first_missing,
        "经过链接数量": len(observed.followed_links),
        "经过链接": [
            {
                "入口": str(definition.entry_path),
                "类别": definition.kind,
                "原定义": definition.raw_target,
                "相对定义": definition.relative,
                "直接引用": str(definition.direct_target),
            }
            for definition in observed.followed_links
        ],
    }


def audit(source):
    counts = Counter()
    outcomes = Counter()
    timings = defaultdict(list)
    failures = []
    pending = [source]
    start = perf_counter()

    while pending:
        folder = pending.pop()
        try:
            # 使用目录之前再核对；不把后来出现的目录链接用于枚举。
            attributes = folder.lstat()
            if raw_kind(attributes) != "directory":
                raise ValueError("待枚举位置不再是普通目录，停止进入")
            folder_revision = revision(attributes)
            with os.scandir(folder) as iterator:
                names = sorted(
                    (entry.name for entry in iterator),
                    key=lambda name: (name.casefold(), name),
                )
            if revision(folder.lstat()) != folder_revision:
                raise ValueError("目录在本次枚举期间发生变化，结果不稳定")
        except (OSError, ValueError) as error:
            outcomes["目录枚举失败"] += 1
            facts = {"路径": str(folder), **error_facts(error)}
            failures.append(facts)
            emit("[目录失败]", facts)
            continue

        for name in names:
            path = folder / name
            try:
                attributes = path.lstat()
            except OSError as error:
                outcomes["入口属性失败"] += 1
                facts = {"路径": str(path), **error_facts(error)}
                failures.append(facts)
                emit("[属性失败]", facts)
                continue

            kind = raw_kind(attributes)
            counts[kind] += 1
            if kind == "directory":
                pending.append(path)
                continue
            if kind == "file":
                continue

            # 三类链接、未知重解析点和特殊对象均显示，不隐藏拒绝结果。
            facts = {
                "路径": str(path),
                "原始属性类别": kind,
                "mode": oct(attributes.st_mode),
                "Windows属性": hex(
                    getattr(attributes, "st_file_attributes", 0)
                ),
                "重解析标签": hex(
                    getattr(attributes, "st_reparse_tag", 0) or 0
                ),
                "入口身份": [attributes.st_dev, attributes.st_ino],
            }

            raw, raw_error, elapsed = timed_call(lambda: os.readlink(path))
            timings["Python原定义读取"].append(elapsed)
            facts["Python定义读取"] = {
                "成功": raw_error is None,
                "耗时毫秒": round(elapsed, 3),
                "原定义": raw,
            }
            if raw_error is not None:
                facts["Python定义读取"].update(error_facts(raw_error))
            outcomes["Python定义成功" if raw_error is None else "Python定义失败"] += 1

            entry, entry_error, elapsed = timed_call(
                lambda: inspect_link_path(path, follow_final_link=False)
            )
            timings["项目入口观察"].append(elapsed)
            facts["项目入口观察"] = {
                "成功": entry_error is None,
                "耗时毫秒": round(elapsed, 3),
            }
            if entry_error is not None:
                facts["项目入口观察"].update(error_facts(entry_error))
                outcomes["项目入口失败"] += 1
                failures.append({
                    "路径": str(path), "阶段": "项目入口观察",
                    **error_facts(entry_error),
                })
            else:
                facts["项目入口观察"].update(observation_facts(entry))
                definition = entry.entry_link
                classification_matches = entry.kind == kind
                definition_matches = (
                    definition is not None
                    and raw_error is None
                    and definition.raw_target == raw
                )
                facts["项目入口观察"]["分类与原始标签一致"] = classification_matches
                facts["项目入口观察"]["原定义与readlink一致"] = (
                    definition_matches if raw_error is None else None
                )
                facts["项目入口观察"]["入口原定义"] = (
                    definition.raw_target if definition is not None else None
                )
                outcomes["项目入口成功"] += 1
                if not classification_matches:
                    outcomes["分类不一致"] += 1
                if raw_error is None and not definition_matches:
                    outcomes["定义不一致"] += 1

            followed, follow_error, elapsed = timed_call(
                lambda: inspect_link_path(path, follow_final_link=True)
            )
            timings["项目跟随元信息解析"].append(elapsed)
            facts["项目跟随解析"] = {
                "成功": follow_error is None,
                "耗时毫秒": round(elapsed, 3),
                "说明": "只查询元信息，不读取正文，不枚举目标目录",
            }
            if follow_error is not None:
                facts["项目跟随解析"].update(error_facts(follow_error))
                outcomes["项目跟随失败"] += 1
                failures.append({
                    "路径": str(path), "阶段": "项目跟随解析",
                    **error_facts(follow_error),
                })
            else:
                facts["项目跟随解析"].update(observation_facts(followed))
                outcomes["项目跟随成功"] += 1
                if followed.kind == "absent":
                    outcomes["跟随结果目标缺失"] += 1

            try:
                stable = revision(path.lstat()) == revision(attributes)
            except OSError as error:
                stable = False
                facts["结束属性错误"] = error_facts(error)
            facts["检查期间入口稳定"] = stable
            if not stable:
                outcomes["入口现场变化"] += 1
            emit("[入口诊断]", facts)

    elapsed = perf_counter() - start
    timing_summary = {}
    for name, values in timings.items():
        ordered = sorted(values)
        timing_summary[name] = {
            "调用数": len(values),
            "总毫秒": round(sum(values), 3),
            "平均毫秒": round(sum(values) / len(values), 3),
            "中位毫秒": round(ordered[len(ordered) // 2], 3),
            "最大毫秒": round(max(values), 3),
        }

    emit("[最终统计]", {
        "素材根": str(source),
        "根目录计数说明": "统计不包含素材根本身",
        "已观察类别数量": dict(counts),
        "本次未发现的链接类别": [
            kind for kind in LINK_KINDS if not counts[kind]
        ],
        "检查结果数量": dict(outcomes),
        "调用耗时": timing_summary,
        "整体耗时秒": round(elapsed, 3),
        "耗时限制": "整体含终端打印；单调用耗时不含对应结果打印",
        "覆盖限制": "未发现的类别没有验证；权限失败不等于类别不存在",
        "副作用边界": "未写正文、未改权限、未重建链接；只读访问可能影响atime",
        "失败清单": failures,
    })

    problem_keys = (
        "目录枚举失败", "入口属性失败", "项目入口失败",
        "项目跟随失败", "分类不一致", "定义不一致", "入口现场变化",
    )
    return 1 if any(outcomes[key] for key in problem_keys) else 0


def main():
    if os.name != "nt":
        raise SystemExit("本诊断只支持Windows")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    arguments = parser.parse_args()
    source = Path(arguments.source)
    if not source.is_absolute() or ".." in source.parts:
        raise SystemExit("素材根必须是明确绝对路径，不能包含..")
    if os.path.normcase(os.path.abspath(source)) != os.path.normcase(
        os.path.abspath(DEFAULT_SOURCE)
    ):
        raise SystemExit("本脚本限定检查用户指定的现有素材根，不接受其他现场")
    try:
        # 检查完整父链，避免通过链接根或链接祖先意外枚举其他现场。
        for position in (*reversed(source.parents), source):
            if raw_kind(position.lstat()) != "directory":
                raise ValueError(f"素材根父链不是普通目录：{position}")
    except (OSError, ValueError) as error:
        emit("[启动失败]", error_facts(error))
        return 2

    emit("[环境]", {
        "Python": sys.version,
        "Windows": str(sys.getwindowsversion()),
        "项目根": str(PROJECT_ROOT),
        "素材根": str(source),
        "政策": "只递归普通目录；链接目标只作元信息解析",
        "异常政策": "具体I/O及状态故障逐项记录；程序缺陷自然暴露",
    })
    return audit(source)


if __name__ == "__main__":
    raise SystemExit(main())