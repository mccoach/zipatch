# -*- coding: utf-8 -*-

import sys
import tkinter as tk
from tkinter import TclError

from app import ZipatchApp


def run_gui_flow():
    print("[启动] 初始化 Tk ...")
    try:
        root = tk.Tk()
    except TclError as error:
        print(f"[错误] Tk 初始化失败：{error}", file=sys.stderr)
        return

    try:
        ZipatchApp(root)
        root.mainloop()
    finally:
        try:
            root.destroy()
        except TclError:
            # 正常关闭已经销毁解释器时，无须重复销毁。
            pass


if __name__ == "__main__":
    run_gui_flow()