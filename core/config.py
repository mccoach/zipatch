# -*- coding: utf-8 -*-

import json
import os

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import perf_counter

from core.constants import DEFAULT_CONFIG
from core.paths import get_config_path



def deep_merge_config(defaults, user_config):
    """按当前结构重建配置；所有可变值归本次配置实例所有。"""
    source = user_config if isinstance(user_config, dict) else {}
    result = {}

    for key, default in defaults.items():
        value = source.get(key, default)

        if isinstance(default, dict):
            if not default:
                result[key] = deepcopy(value) if isinstance(value, dict) else {}
            else:
                result[key] = deep_merge_config(default, value)
        elif isinstance(default, bool):
            result[key] = value if isinstance(value, bool) else default
        elif isinstance(default, int):
            result[key] = value if type(value) is int else default
        elif isinstance(default, str):
            result[key] = value if isinstance(value, str) else default
        elif isinstance(default, list):
            result[key] = deepcopy(value) if isinstance(value, list) else deepcopy(default)
        else:
            result[key] = deepcopy(value)

    return result


def normalize_feature_order(config_data, feature_registry):
    known = set(feature_registry)
    order = config_data["ui"]["feature_order"]
    normalized = []
    seen = set()

    if isinstance(order, list):
        for key in order:
            if isinstance(key, str) and key in known and key not in seen:
                normalized.append(key)
                seen.add(key)

    for key, feature in sorted(
        feature_registry.items(),
        key=lambda pair: pair[1].get("default_order", 9999),
    ):
        if key not in seen:
            normalized.append(key)

    changed = normalized != order
    config_data["ui"]["feature_order"] = normalized

    if config_data["active_mode"] not in known:
        config_data["active_mode"] = normalized[0]
        changed = True

    return changed


def normalize_config(config):
    maximum = config["settings"]["entry_history_max_items"]
    if type(maximum) is not int or maximum < 1:
        maximum = DEFAULT_CONFIG["settings"]["entry_history_max_items"]
    config["settings"]["entry_history_max_items"] = maximum

    histories = {}
    for key, values in config["entry_history"].items():
        if not isinstance(key, str) or not isinstance(values, list):
            continue
        unique = []
        for value in values:
            if isinstance(value, str) and value and value not in unique:
                unique.append(value)
        # 删除最后一项后的空列表也是有效历史状态，不在重启时反复修复。
        histories[key] = unique[:maximum]
    config["entry_history"] = histories

    for key, values in config["favorites"].items():
        config["favorites"][key] = [
            {"name": value["name"], "content": value["content"]}
            for value in values
            if isinstance(value, dict)
            and isinstance(value.get("name"), str)
            and isinstance(value.get("content"), str)
        ]

    if config["patch"]["patch_mode"] not in ("apply", "restore"):
        config["patch"]["patch_mode"] = "apply"
    if config["restore"]["existing_file_policy"] not in ("overwrite", "rename", "skip"):
        config["restore"]["existing_file_policy"] = "overwrite"

    return config


def validate_exclusion_config_source(source):
    """结构错误必须暴露，不能把损坏名单或收藏替换为默认值。"""
    if not isinstance(source, dict):
        raise ValueError("配置顶层必须是 JSON 对象；原文件未修改。")

    for page in ("scan", "merge"):
        if page not in source:
            continue
        section = source[page]
        if not isinstance(section, dict):
            raise ValueError(f"配置 {page} 必须是对象；原文件未修改。")
        for field in ("exclude_folders", "exclude_files"):
            if field in section and not isinstance(section[field], str):
                raise ValueError(
                    f"配置 {page}.{field} 必须是字符串；"
                    "不能将错误内容当成空名单或默认名单加载。"
                )

    if "favorites" not in source:
        return
    favorites = source["favorites"]
    if not isinstance(favorites, dict):
        raise ValueError("配置 favorites 必须是对象；原文件未修改。")
    for key in (
        "scan_exclude_folders", "scan_exclude_files",
        "merge_exclude_folders", "merge_exclude_files",
    ):
        if key not in favorites:
            continue
        entries = favorites[key]
        if not isinstance(entries, list):
            raise ValueError(f"排除收藏 favorites.{key} 必须是数组；原文件未修改。")
        for number, entry in enumerate(entries, 1):
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("name"), str)
                or not isinstance(entry.get("content"), str)
            ):
                raise ValueError(
                    f"排除收藏 favorites.{key} 第 {number} 项结构错误："
                    "name 和 content 必须是字符串；原文件未修改。"
                )


def load_user_config(path=None):
    """只有文件不存在才创建默认配置；损坏或读取故障不重建、不覆盖。"""
    path = Path(path) if path is not None else get_config_path()

    try:
        with path.open("r", encoding="utf-8") as stream:
            source = json.load(stream)
    except FileNotFoundError:
        return normalize_config(deep_merge_config(DEFAULT_CONFIG, {})), True
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(
            f"配置内容无法解析：{path}\n{error}\n"
            "原配置文件未修改，请修正配置后重新启动。"
        ) from error

    validate_exclusion_config_source(source)
    config = normalize_config(deep_merge_config(DEFAULT_CONFIG, source))
    return config, config != source


class SaveStatus(Enum):
    UNCHANGED = "unchanged"
    SAVED = "saved"
    FAILED = "failed"
    QUEUED = "queued"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class SaveResult:
    status: SaveStatus
    error: OSError | None = None

    @property
    def persisted(self):
        """整个已接受配置已经确认持久化。排队和会话延后不是成功。"""
        return self.status in (SaveStatus.UNCHANGED, SaveStatus.SAVED)

    @property
    def request_satisfied(self):
        """普通操作无需写盘时，会话字段仍可按正式契约延后。"""
        return self.persisted or self.status is SaveStatus.DEFERRED


class ConfigSaveManager:
    """唯一配置持久化入口。batch 合并请求，不承担配置回滚。"""

    def __init__(self, config_data, path=None, needs_write=False):
        self.config_data = config_data
        self.path = Path(path) if path is not None else get_config_path()
        self.last_save_error = None
        self.last_cleanup_error = None
        self.required_write = needs_write
        self._pending = False
        self._session_pending = False
        self._batch_depth = 0
        self._save_requested = False
        self._include_session_requested = False
        self._batch_aborted = False
        self._batch_original_fields = {}
        self._batch_original_pending = False
        self._batch_original_session_pending = False
        self.metrics = {
            "save_requests": 0,
            "json_generations": 0,
            "write_attempts": 0,
            "successful_replaces": 0,
            "json_seconds": 0.0,
            "compare_seconds": 0.0,
            "write_seconds": 0.0,
            "fsync_seconds": 0.0,
            "replace_seconds": 0.0,
        }
        self._saved_snapshot = None if needs_write else self._serialize()
        self.last_result = SaveResult(SaveStatus.UNCHANGED)

    @property
    def needs_save(self):
        return self.required_write or self._pending

    def accept(self, target, values, persist=True):
        """接受准备完毕的字段；不接受尚未明确恢复的异常批次。"""
        if self._batch_aborted:
            raise RuntimeError("异常批次尚未恢复，禁止继续接受配置或保存。")

        changes = {
            key: value for key, value in values.items()
            if key not in target or target[key] != value
        }
        if not changes:
            return False

        if self._batch_depth:
            for key in changes:
                identifier = (id(target), key)
                if identifier not in self._batch_original_fields:
                    exists = key in target
                    original = deepcopy(target[key]) if exists else None
                    self._batch_original_fields[identifier] = (
                        target, key, exists, original,
                    )

        target.update(changes)
        if persist:
            self._pending = True
        else:
            self._session_pending = True
        return True

    def recover_aborted_batch(self):
        """
        调用方显式放弃异常批次，只恢复该批次实际修改过的字段。

        batch 本身不自动回滚，不复制整份配置。
        恢复完成前所有新接受和保存都被拒绝，防止夹带半成品。
        """
        if self._batch_depth:
            raise RuntimeError("必须在最外层批次退出后恢复。")
        if not self._batch_aborted:
            return False

        for target, key, existed, original in self._batch_original_fields.values():
            if existed:
                target[key] = original
            else:
                target.pop(key, None)

        self._pending = self._batch_original_pending
        self._session_pending = self._batch_original_session_pending
        self._batch_original_fields.clear()
        self._batch_aborted = False
        self._save_requested = False
        self._include_session_requested = False
        return True

    def _serialize(self):
        start = perf_counter()
        snapshot = json.dumps(
            self.config_data,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        self.metrics["json_generations"] += 1
        self.metrics["json_seconds"] += perf_counter() - start
        return snapshot

    def _write_snapshot(self, snapshot):
        temporary = self.path.with_name(self.path.name + ".tmp")
        self.metrics["write_attempts"] += 1
        self.last_cleanup_error = None

        try:
            start = perf_counter()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(snapshot)
                stream.flush()
                self.metrics["write_seconds"] += perf_counter() - start
                start = perf_counter()
                os.fsync(stream.fileno())
                self.metrics["fsync_seconds"] += perf_counter() - start

            start = perf_counter()
            os.replace(temporary, self.path)
            self.metrics["replace_seconds"] += perf_counter() - start
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                self.last_cleanup_error = cleanup_error
            self.last_save_error = error
            # 已经尝试持久化的状态失败后属于待重试内容，
            # 包括原本仅延后的会话字段；普通提交无需重新编辑即可重试。
            self._pending = True
            return SaveResult(SaveStatus.FAILED, error)

        self.metrics["successful_replaces"] += 1
        self._saved_snapshot = snapshot
        self.required_write = False
        self._pending = False
        self._session_pending = False
        self.last_save_error = None
        return SaveResult(SaveStatus.SAVED)

    def save(self, include_session=False):
        self.metrics["save_requests"] += 1
        if self._batch_aborted:
            raise RuntimeError("异常批次尚未恢复，禁止保存半完成配置。")

        if self._batch_depth:
            self._save_requested = True
            self._include_session_requested |= include_session
            return SaveResult(SaveStatus.QUEUED)

        needed = self.needs_save or (include_session and self._session_pending)
        if not needed:
            status = SaveStatus.DEFERRED if self._session_pending else SaveStatus.UNCHANGED
            self.last_result = SaveResult(status)
            return self.last_result

        snapshot = self._serialize()
        start = perf_counter()
        same = snapshot == self._saved_snapshot
        self.metrics["compare_seconds"] += perf_counter() - start

        if same and not self.required_write:
            self._pending = False
            self._session_pending = False
            # 相同状态不抹掉最近保存错误的历史记录。
            self.last_result = SaveResult(SaveStatus.UNCHANGED)
        else:
            self.last_result = self._write_snapshot(snapshot)

        return self.last_result

    @contextmanager
    def batch(self):
        if self._batch_aborted:
            raise RuntimeError("请先显式恢复上一次异常批次。")
        if self._batch_depth == 0:
            self._batch_original_fields.clear()
            self._batch_original_pending = self._pending
            self._batch_original_session_pending = self._session_pending

        self._batch_depth += 1
        completed = False
        try:
            yield self
            completed = True
        finally:
            self._batch_depth -= 1
            if not completed:
                self._batch_aborted = True
                self._save_requested = False
                self._include_session_requested = False

            if self._batch_depth == 0 and completed:
                if self._batch_aborted:
                    raise RuntimeError("嵌套批次已经失败，必须恢复后才能继续。")
                requested = self._save_requested
                include_session = self._include_session_requested
                self._save_requested = False
                self._include_session_requested = False
                self._batch_original_fields.clear()
                if requested:
                    self.save(include_session=include_session)