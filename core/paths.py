# -*- coding: utf-8 -*-

import os
import sys
import subprocess
from pathlib import Path
from tkinter import TclError, messagebox

from core.constants import CONFIG_FILENAME


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parents[1]


def get_config_path():
    return get_app_dir() / CONFIG_FILENAME


def get_asset_path(relative_path):
    """
    兼容开发模式和 PyInstaller 打包后的资源路径。
    """
    return get_app_dir() / relative_path


def center_window(win, width=980, height=760):
    try:
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = int((sw - width) / 2)
        y = int((sh - height) / 3)
        win.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        pass


def raise_and_focus(win):
    try:
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.focus_force()
        win.after(200, lambda: win.attributes("-topmost", False))
    except Exception:
        pass


def open_path_with_default_app(path, parent=None):
    try:
        if not path:
            messagebox.showerror("打开失败", "路径为空。", parent=parent)
            return

        abs_path = Path(path).resolve()

        if not abs_path.exists():
            messagebox.showerror("打开失败", f"路径不存在：\n{abs_path}", parent=parent)
            return

        if sys.platform == "win32":
            os.startfile(str(abs_path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(abs_path)], check=False)
        else:
            subprocess.run(["xdg-open", str(abs_path)], check=False)

    except TclError:
        print(f"[打开失败] {path}", file=sys.stderr)
    except Exception as e:
        try:
            messagebox.showerror("打开失败", f"无法自动打开：\n{path}\n\n错误：{e}", parent=parent)
        except TclError:
            print(f"[打开失败] {path}: {e}", file=sys.stderr)


def get_initial_dir_from_path(path):
    path = (path or "").strip()

    if not path:
        return None

    abs_path = Path(path).expanduser().resolve()

    if abs_path.is_dir():
        return str(abs_path)

    parent = abs_path.parent
    if parent.exists() and parent.is_dir():
        return str(parent)

    return None


def ensure_parent_dir(file_path):
    path = Path(file_path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)


def build_output_path(output_folder, output_filename):
    return str((Path(output_folder) / output_filename).resolve())


def get_available_renamed_path(file_path):
    path = Path(file_path)

    if not path.exists():
        return str(path)

    folder = path.parent
    stem = path.stem
    suffix = path.suffix
    index = 1

    while True:
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return str(candidate)
        index += 1


def validate_required_path(path, label):
    if not path:
        raise ValueError(f"{label}不能为空。")
    return path
