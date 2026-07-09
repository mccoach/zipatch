# -*- coding: utf-8 -*-

import json
import sys

from core.constants import DEFAULT_CONFIG
from core.paths import get_config_path


def deep_merge_config(defaults, user_config):
    """
    按默认配置重建用户配置。

    规则：
    1. 默认配置中存在、用户配置缺失的字段：按默认值补齐；
    2. 默认配置中存在、用户配置也存在的字段：保留用户值；
    3. 默认配置中不存在的旧字段：自动删除；
    4. 嵌套 dict 递归执行同一规则。

    这一步是配置的唯一清理入口：
    - 不做旧字段兼容；
    - 不保留废弃配置；
    - 最终保存出的配置只包含当前版本唯一有效的配置结构。
    """
    result = {}
    user_config = user_config if isinstance(user_config, dict) else {}

    for key, default_value in defaults.items():
        user_value = user_config.get(key)

        if isinstance(default_value, dict):
            result[key] = deep_merge_config(
                default_value,
                user_value if isinstance(user_value, dict) else {},
            )
        elif key in user_config:
            result[key] = user_value
        else:
            result[key] = default_value

    return result


def load_user_config():
    path = get_config_path()

    if not path.exists():
        return deep_merge_config(DEFAULT_CONFIG, {})

    try:
        with path.open("r", encoding="utf-8") as f:
            user_config = json.load(f)

        return deep_merge_config(DEFAULT_CONFIG, user_config)

    except Exception as e:
        print(f"[配置] 读取失败，使用默认配置：{e}", file=sys.stderr)
        return deep_merge_config(DEFAULT_CONFIG, {})


def save_user_config(config):
    path = get_config_path()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"[配置] 已保存：{path}")
    except Exception as e:
        print(f"[配置] 保存失败：{e}", file=sys.stderr)


def normalize_feature_order(config_data, feature_registry):
    """
    修复功能标签顺序配置。

    规则：
    1. 移除已经不存在的功能 key；
    2. 自动补上新增功能 key；
    3. 如果配置为空，则按 default_order 生成；
    4. 保证顺序稳定、可恢复。
    """
    config_data.setdefault("ui", {})

    known_keys = set(feature_registry.keys())
    saved_order = config_data["ui"].get("feature_order", [])

    if not isinstance(saved_order, list):
        saved_order = []

    normalized = []
    seen = set()

    for key in saved_order:
        if key in known_keys and key not in seen:
            normalized.append(key)
            seen.add(key)

    missing = [
        key
        for key, item in sorted(
            feature_registry.items(),
            key=lambda pair: pair[1].get("default_order", 9999)
        )
        if key not in seen
    ]

    normalized.extend(missing)

    if not normalized:
        normalized = [
            key
            for key, item in sorted(
                feature_registry.items(),
                key=lambda pair: pair[1].get("default_order", 9999)
            )
        ]

    config_data["ui"]["feature_order"] = normalized

    active_mode = config_data.get("active_mode")
    if active_mode not in known_keys:
        config_data["active_mode"] = normalized[0]
