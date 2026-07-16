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


def is_frozen_app():
    """
    判断当前是否为 PyInstaller 封装后的程序。

    用途：
    - 开发模式：允许维护类功能，例如从 Markdown 更新内置协议文档；
    - 封装 exe：隐藏维护入口，避免用户误以为可以修改 exe 内部资源。
    """
    return bool(getattr(sys, "frozen", False))


def get_asset_path(relative_path):
    """
    获取应用静态资源路径。

    兼容三种运行场景：
    1. 开发模式：从项目根目录读取；
    2. PyInstaller onefile/onedir：从 sys._MEIPASS 读取打包内置资源；
    3. 后台外置覆盖：exe 同级目录存在同名资源时，优先使用外置资源。

    查找顺序：
    - 先查应用目录 / exe 同级目录，允许后台直接替换资源；
    - 再查 PyInstaller 解包临时目录 sys._MEIPASS；
    - 都不存在时返回应用目录路径，用于生成清晰报错。
    """
    external_path = get_app_dir() / relative_path

    if external_path.exists():
        return external_path

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled_path = Path(sys._MEIPASS) / relative_path

        if bundled_path.exists():
            return bundled_path

    return external_path


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
