# -*- coding: utf-8 -*-

import hashlib
import json
import shutil
from pathlib import Path

from core.path_validation import resolve_path
from core.time_utils import now_stamp


def file_content_sha256(path: Path):
    path = Path(path)

    if not path.is_file():
        return ""

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def file_sha256(path: Path):
    """
    返回文件或目录的稳定哈希。

    - 文件：计算文件内容 SHA256；
    - 目录：计算目录树 SHA256，纳入相对路径和文件内容哈希；
    - 不存在：返回空字符串。
    """
    path = Path(path)

    if not path.exists():
        return ""

    if path.is_file():
        return file_content_sha256(path)

    if not path.is_dir():
        return ""

    h = hashlib.sha256()
    root = path.resolve()

    for item in sorted(root.rglob("*"), key=lambda p: str(p.relative_to(root)).replace("\\", "/").lower()):
        rel = str(item.relative_to(root)).replace("\\", "/")

        if item.is_dir():
            h.update(f"DIR:{rel}\n".encode("utf-8"))
            continue

        if item.is_file():
            h.update(f"FILE:{rel}:".encode("utf-8"))
            h.update(file_content_sha256(item).encode("utf-8"))
            h.update(b"\n")

    return h.hexdigest()


def copy_path_for_backup(source: Path, backup_path: Path):
    source = Path(source)
    backup_path = Path(backup_path)

    if source.is_dir():
        if backup_path.exists():
            remove_path(backup_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, backup_path)
        return

    if source.is_file():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
        return

    raise ValueError(f"无法备份非文件/非目录路径：{source}")


def remove_path(path: Path):
    path = Path(path)

    if not path.exists():
        return

    if path.is_dir():
        shutil.rmtree(path)
        return

    path.unlink()


def restore_path_from_backup(source: Path, target: Path):
    source = Path(source)
    target = Path(target)

    if source.is_dir():
        if target.exists():
            remove_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        return

    if source.is_file():
        if target.exists() and target.is_dir():
            remove_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return

    raise ValueError(f"备份源不是文件或目录：{source}")


def path_type(path: Path):
    path = Path(path)

    if path.is_file():
        return "file"

    if path.is_dir():
        return "dir"

    return ""


def backup_path_to_manifest_value(backup_root: Path, backup_path):
    if not backup_path:
        return ""

    return str(Path(backup_path).relative_to(backup_root)).replace("\\", "/")


def make_path_state(path: Path, backup_root: Path = None, backup_path=None):
    path = Path(path)

    return {
        "exists": path.exists(),
        "type": path_type(path),
        "sha256": file_sha256(path),
        "backup_file": (
            backup_path_to_manifest_value(backup_root, backup_path)
            if backup_root and backup_path
            else ""
        ),
    }


def current_state_matches_expected(path: Path, expected_state: dict):
    path = Path(path)
    expected_exists = bool(expected_state.get("exists", False))

    if path.exists() != expected_exists:
        return False

    if not expected_exists:
        return True

    return (
        path_type(path) == expected_state.get("type", "")
        and file_sha256(path) == expected_state.get("sha256", "")
    )


def describe_state_mismatch(path: Path, expected_state: dict):
    path = Path(path)
    expected_exists = bool(expected_state.get("exists", False))

    if path.exists() != expected_exists:
        if expected_exists:
            return "当前路径不存在，但备份记录显示此时应存在"
        return "当前路径存在，但备份记录显示此时应不存在"

    if not expected_exists:
        return ""

    current_type = path_type(path)
    expected_type = expected_state.get("type", "")

    if current_type != expected_type:
        return f"当前路径类型不一致：expected={expected_type}, actual={current_type}"

    current_hash = file_sha256(path)
    expected_hash = expected_state.get("sha256", "")

    if current_hash != expected_state.get("sha256", ""):
        return "当前路径内容指纹与备份记录不一致"

    return ""


def resolve_backup_base_dir(project_root, backup_dir):
    result = resolve_path(
        backup_dir,
        base_dir=project_root,
        allow_relative=True,
    )

    if not result.is_valid:
        raise ValueError(f"备份目录无效：{result.issue}")

    return Path(result.resolved)


def make_backup_root(project_root, backup_dir, action_name=""):
    stamp = now_stamp()
    safe_action_name = str(action_name or "").strip().replace("/", "_").replace("\\", "_")

    if safe_action_name:
        folder_name = f"{stamp}_{safe_action_name}"
    else:
        folder_name = stamp

    return resolve_backup_base_dir(project_root, backup_dir) / folder_name


def load_manifest(backup_root):
    manifest_path = Path(backup_root) / "manifest.json"

    if not manifest_path.is_file():
        raise ValueError(f"备份目录中未找到 manifest.json：{manifest_path}")

    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"manifest.json 读取失败：{e}")


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def remove_empty_parent_dirs_until_root(path: Path, root: Path):
    """
    删除新增文件后，向上清理空目录。

    只清理 root 内部的空目录；
    遇到非空目录或项目根目录即停止。
    """
    root = Path(root).resolve()
    current = Path(path).resolve().parent

    while current != root:
        try:
            current.relative_to(root)
        except ValueError:
            return

        try:
            current.rmdir()
        except OSError:
            return

        current = current.parent