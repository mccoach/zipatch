# -*- coding: utf-8 -*-

import json
import sys

from core.content_publication import (
    ContentPublicationError, ContentPublicationResult,
    dispose_content_artifact, prepare_content_artifact, publish_content,
)
from core.file_observation import FileObservation, observe_file
from core.filesystem_changes import (
    FilesystemChangeError, execute_filesystem_change,
    prepare_directory_creation,
)

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import perf_counter

from core.constants import DEFAULT_CONFIG
from core.favorite_order import order_favorites
from core.merged_format import OutputSettingsError, validate_output_settings
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

    # 加载前已经完整校验收藏结构；这里只规范化合法条目，
    # 不过滤错误数据，也不把损坏类别替换为空列表。
    for key, values in config["favorites"].items():
        config["favorites"][key] = order_favorites([
            {
                "name": value["name"],
                "content": value["content"],
                **({"pinned": True} if value.get("pinned", False) else {}),
            }
            for value in values
        ])

    if config["patch"]["patch_mode"] not in ("apply", "restore"):
        config["patch"]["patch_mode"] = "apply"
    validate_restore_settings(config["restore"])

    return config


def validate_restore_settings(settings):
    """正式拆分配置不接受已废弃政策或未知目标模式。"""
    if not isinstance(settings, dict):
        raise OutputSettingsError("配置 restore 必须是对象；原文件未修改。")
    for field in ("source_text", "last_result_text"):
        if field in settings and type(settings[field]) is not str:
            raise OutputSettingsError(
                f"restore.{field} 必须是字符串；原文件未修改。"
            )
    if (
        "backup_enabled" in settings
        and type(settings["backup_enabled"]) is not bool
    ):
        raise OutputSettingsError(
            "restore.backup_enabled 必须是布尔值；原文件未修改。"
        )
    if "backup_dir" in settings and type(settings["backup_dir"]) is not str:
        raise OutputSettingsError(
            "restore.backup_dir 必须是字符串；原文件未修改。"
        )

    execution_mode = settings.get(
        "execution_mode", DEFAULT_CONFIG["restore"]["execution_mode"],
    )
    if type(execution_mode) is not str or execution_mode not in ("split", "restore"):
        raise OutputSettingsError(
            "restore.execution_mode 必须是 split 或 restore；原文件未修改。"
        )

    mode = settings.get("target_mode", DEFAULT_CONFIG["restore"]["target_mode"])
    policy = settings.get(
        "existing_file_policy",
        DEFAULT_CONFIG["restore"]["existing_file_policy"],
    )
    if type(mode) is not str or mode not in ("directory", "in_place"):
        raise OutputSettingsError(
            "restore.target_mode 必须是 directory 或 in_place；原文件未修改。"
        )
    if type(policy) is not str or policy not in ("overwrite", "skip"):
        raise OutputSettingsError(
            "restore.existing_file_policy 必须是 overwrite 或 skip；"
            "原文件未修改。"
        )


def validate_rule_config_source(source):
    """名单结构先于默认合并检查；非法规则正文仍保留。"""
    if not isinstance(source, dict):
        raise ValueError("配置顶层必须是 JSON 对象；原文件未修改。")
    for section_name in ("merge", "restore"):
        if section_name in source:
            validate_output_settings(section_name, source[section_name])
    if "restore" in source:
        validate_restore_settings(source["restore"])
    for section_name, fields in (
        ("scan", ("exclude_folders", "exclude_files")),
        ("merge", ("exclude_folders", "exclude_files")),
        ("settings", ("crlf_file_list",)),
    ):
        if section_name not in source:
            continue
        section = source[section_name]
        if not isinstance(section, dict):
            raise ValueError(f"配置 {section_name} 必须是对象；原文件未修改。")
        for field in fields:
            if field in section and not isinstance(section[field], str):
                raise ValueError(
                    f"配置 {section_name}.{field} 必须是字符串；"
                    "不能将错误内容当成空名单或默认名单加载。"
                )

    if "patch" in source:
        patch_settings = source["patch"]
        if not isinstance(patch_settings, dict):
            raise ValueError("配置 patch 必须是对象；原文件未修改。")
        if (
            "normalize_text_newlines" in patch_settings
            and type(patch_settings["normalize_text_newlines"]) is not bool
        ):
            raise ValueError(
                "配置 patch.normalize_text_newlines 必须是布尔值；"
                "原文件未修改。"
            )

    if "text_wrap" in source:
        wraps = source["text_wrap"]
        if not isinstance(wraps, dict):
            raise ValueError("配置 text_wrap 必须是对象；原文件未修改。")
        for field in (
            "settings.crlf_file_list", "merge.ignore_text", "restore.ignore_text",
            "merge.failure_text", "restore.failure_text",
            "merge.code_header_line", "merge.code_footer_line",
            "restore.code_header_line", "restore.code_footer_line",
        ):
            if field in wraps and type(wraps[field]) is not bool:
                raise ValueError(
                    f"配置 text_wrap.{field} 必须是布尔值；原文件未修改。"
                )

    for section_name in ("patch", "restore"):
        if section_name not in source:
            continue
        section = source[section_name]
        if not isinstance(section, dict):
            raise ValueError(
                f"配置 {section_name} 必须是对象；原文件未修改。"
            )
        for field, expected_type in (
            ("restore_source_dir", str),
            ("keep_restore_source_path", bool),
            ("restore_backup_enabled", bool),
            ("open_backup_after_done", bool),
        ):
            if field in section and type(section[field]) is not expected_type:
                raise ValueError(
                    f"配置 {section_name}.{field} 类型无效；原文件未修改。"
                )

    if "favorites" not in source:
        return
    favorites = source["favorites"]
    if not isinstance(favorites, dict):
        raise ValueError("配置 favorites 必须是对象；原文件未修改。")

    # 必须先于默认合并检查，防止未知类别或字段被重建过程静默丢弃。
    # 类别权威来自当前正式配置；名称按收藏消费者的精确字符串定位。
    supported_categories = DEFAULT_CONFIG["favorites"]
    allowed_fields = {"name", "content", "pinned"}
    for key, entries in favorites.items():
        if key not in supported_categories:
            raise ValueError(
                f"收藏 favorites.{key} 是未知类别；原文件未修改。"
            )
        if not isinstance(entries, list):
            raise ValueError(
                f"收藏 favorites.{key} 必须是数组；原文件未修改。"
            )
        first_names = {}
        for number, entry in enumerate(entries, 1):
            location = f"收藏 favorites.{key} 第 {number} 项"
            if not isinstance(entry, dict):
                raise ValueError(
                    f"{location} 必须是对象；原文件未修改。"
                )
            unknown_fields = set(entry) - allowed_fields
            if unknown_fields:
                fields = "、".join(sorted(unknown_fields))
                raise ValueError(
                    f"{location} 包含未知字段：{fields}；原文件未修改。"
                )
            for field in ("name", "content"):
                if field not in entry:
                    raise ValueError(
                        f"{location} 缺少必要字段 {field}；原文件未修改。"
                    )
                if not isinstance(entry[field], str):
                    raise ValueError(
                        f"{location} 字段 {field} 必须是字符串；"
                        "原文件未修改。"
                    )
            if "pinned" in entry and type(entry["pinned"]) is not bool:
                raise ValueError(
                    f"{location} pinned 必须是布尔值；原文件未修改。"
                )
            name = entry["name"]
            if name in first_names:
                raise ValueError(
                    f"{location} 字段 name 与第 {first_names[name]} 项"
                    f"精确重名：{name!r}；原文件未修改。"
                )
            first_names[name] = number


def load_user_config(path=None, *, notices=None):
    """只有文件不存在才创建默认配置；损坏或读取故障不重建、不覆盖。"""
    path = Path(path) if path is not None else get_config_path()

    observed = observe_file(path, include_bytes=True)
    if observed.kind == "absent":
        return normalize_config(deep_merge_config(DEFAULT_CONFIG, {})), True

    try:
        source = json.loads(observed.source_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(
            f"配置内容无法解析：{path}\n{error}\n"
            "原配置文件未修改，请修正配置后重新启动。"
        ) from error

    # 仅在加载边界转换明确的旧保存值；其他未知值仍按正式契约拒绝。
    restore_source = source.get("restore") if isinstance(source, dict) else None
    converted = (
        isinstance(restore_source, dict)
        and restore_source.get("existing_file_policy") == "rename"
    )
    if converted:
        restore_source["existing_file_policy"] = "skip"
    validate_rule_config_source(source)
    config = normalize_config(deep_merge_config(DEFAULT_CONFIG, source))
    if converted and notices is not None:
        notices.append(
            "代码拆分已取消自动改名；原“自动改名”设置"
            "已调整为“跳过已有文件”，请核对。"
        )
    return config, converted or config != source


class SaveStatus(Enum):
    UNCHANGED = "unchanged"
    SAVED = "saved"
    FAILED = "failed"
    QUEUED = "queued"
    DEFERRED = "deferred"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SaveResult:
    status: SaveStatus
    error: OSError | ValueError | None = None
    publication: ContentPublicationResult | None = None
    directory_results: tuple = ()
    phase: str = ""

    @property
    def failure_description(self):
        """消费本次目标及目录结果，不把零改变等同于磁盘已确认。"""
        if self.status is not SaveStatus.FAILED:
            raise ValueError("只有失败保存结果可以提供失败说明")

        publication = self.publication
        if publication is None:
            fact = (
                "配置目标的正式替换尚未发起，也未开始原对象正文写回。\n"
                "本次配置内容未由保存流程发布。"
            )
        else:
            fact = (
                f"配置目标：{publication.target}\n"
                f"发布机制：{publication.mechanism}\n"
                f"失败阶段：{publication.phase}\n"
                "目标改变调用已发起："
                f"{'是' if publication.mutation_attempted else '否'}\n"
                f"名称替换状态：{publication.replacement}\n"
                "完整配置正文已确认："
                f"{'是' if publication.content_completed else '否'}\n"
                "本次必要确认已完成："
                f"{'是' if publication.confirmation_completed else '否'}\n"
                f"应急成果状态：{publication.emergency_state}"
            )
            if publication.emergency_path is not None:
                fact += f"\n应急成果位置：{publication.emergency_path}"
            if not publication.mutation_attempted:
                fact += (
                    "\n本次没有发起配置目标改变；"
                    "这不能证明当前磁盘仍等于旧成功快照。"
                )
            fact += (
                "\n本次不算保存成功；旧快照不再提供免写资格。\n"
                "下一次保存必须实际发布当前已接受内存配置，"
                "即使草稿已放弃或内存值已改回旧值。"
            )
            if publication.mechanism == "in_place":
                fact += (
                    "\n没有名称替换不代表没有原地改变；"
                    "关联硬链接名称共享同一对象正文。\n"
                    "发布失败后的完整应急不自动删除。"
                )

        if self.phase:
            fact += f"\n配置保存流程阶段：{self.phase}"
        if self.directory_results:
            fact += "\n必要目录步骤："
            for result in self.directory_results:
                fact += (
                    f"\n- {result.target}；改变调用已发起="
                    f"{'是' if result.attempted else '否'}；"
                    f"正常完成={'是' if result.succeeded else '否'}"
                )
            fact += "\n目录调用失败不能证明该目录没有创建副作用。"

        return (
            fact
            + "\n当前已接受内存配置仍可再次保存；"
            "没有自动重试、磁盘回滚或重新执行业务。"
            + f"\n\n{self.error}"
        )

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
            "publication_seconds": 0.0,
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

        if target is self.config_data.get("restore"):
            validate_restore_settings({**target, **values})

        output_fields = {
            "code_header_line", "code_footer_line", "ignore_text", "failure_text",
            "normalize_text_newlines", "record_file_mtime", "merge_mode",
        }
        if output_fields.intersection(values):
            for section_name in ("merge", "restore"):
                if target is self.config_data.get(section_name):
                    validate_output_settings(section_name, {**target, **values})
                    break

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
        """固定目标、必要目录和完整bytes直接消费唯一正式发布。"""
        content = snapshot.encode("utf-8")
        self.metrics["write_attempts"] += 1
        start = perf_counter()
        artifact = None
        publication = None
        directory_results = []
        phase = "配置目标固定观察"

        try:
            expected = observe_file(self.path, include_digest=True)
            phase = "固定必要配置目录创建"
            for index, (directory, kind) in enumerate(expected.chain[:-1]):
                if kind != "absent":
                    continue
                absent = FileObservation(
                    directory, expected.chain[:index + 1], "absent",
                    None, None, None, None, None, None,
                )
                try:
                    result = execute_filesystem_change(
                        prepare_directory_creation(absent),
                    )
                except FilesystemChangeError as error:
                    directory_results.append(error.result)
                    raise
                directory_results.append(result)

            phase = "完整配置成果准备"
            artifact = prepare_content_artifact(expected, content)
            phase = "配置成果正式发布"
            try:
                publication = publish_content(artifact)
            except ContentPublicationError as error:
                publication = error.result
                raise

        except (OSError, ValueError) as error:
            self.last_save_error = error
            self._pending = True
            if publication is not None:
                # 零本次改变也可能来自固定目标漂移拒绝。
                # 不增加失败现场读取去重新认证旧快照，
                # 后续明确保存直接重新发布当前已接受配置。
                self.required_write = True
            return SaveResult(
                SaveStatus.FAILED, error, publication,
                tuple(directory_results), phase,
            )
        finally:
            if artifact is not None:
                # 程序缺陷可能在改变目标后自然传播，缺少正式结果
                # 不能恢复免写资格。成功结果在下面统一收口。
                if artifact._lifecycle in ("attempted", "completed"):
                    self.required_write = True
                    self._pending = True
                if artifact._lifecycle not in ("completed", "disposed"):
                    if not (
                        artifact.mechanism == "in_place"
                        and artifact._lifecycle == "attempted"
                    ):
                        try:
                            dispose_content_artifact(artifact)
                        except (OSError, ValueError) as cleanup_error:
                            print(
                                f"[配置自有暂存清理失败] {artifact.path}："
                                f"{cleanup_error}",
                                file=sys.stderr,
                            )
            self.metrics["publication_seconds"] += perf_counter() - start

        # 名称替换指标不把硬链接原对象写回冒充os.replace。
        if publication.mechanism == "atomic_replace":
            self.metrics["successful_replaces"] += 1
        self._saved_snapshot = snapshot
        self.required_write = False
        self._pending = False
        self._session_pending = False
        self.last_save_error = None
        return SaveResult(
            SaveStatus.SAVED, publication=publication,
            directory_results=tuple(directory_results), phase="completed",
        )

    def save(self, include_session=False):

        self.metrics["save_requests"] += 1
        if self._batch_aborted:
            raise RuntimeError("异常批次尚未恢复，禁止保存半完成配置。")

        for section_name in ("merge", "restore"):
            if section_name in self.config_data:
                validate_output_settings(section_name, self.config_data[section_name])
        if "restore" in self.config_data:
            validate_restore_settings(self.config_data["restore"])

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