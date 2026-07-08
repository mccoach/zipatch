# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_show_error
from ui.theme import styled_frame


class BasePanel(tk.Frame):
    """
    所有功能面板的基类。

    面板只通过 app 提供的能力与主窗口交互：
    - log_func：写全局日志；
    - set_status：改底部状态；
    - save_config：保存配置。

    各功能自己的按钮、输入框、浏览器、文本框都放自己面板里。
    """

    config_key = None

    def __init__(
        self,
        parent,
        app,
        config_data,
        log_func,
        set_status,
        save_config,
    ):
        super().__init__(parent, bg=THEME["bg_panel"])

        self.app = app
        self.root = app.root
        self.config_data = config_data
        self.log = log_func
        self.set_status = set_status
        self.save_config = save_config

        if self.config_key:
            self.cfg = self.config_data[self.config_key]
        else:
            self.cfg = {}

        self.build()

    def build(self):
        raise NotImplementedError

    def make_body(self):
        body = styled_frame(self, bg=THEME["bg_panel"])
        body.pack(fill="x")
        return body

    def handle_error(self, title, error, parent=None):
        self.set_status("执行失败")
        self.log(f"{title}失败：{error}")
        safe_show_error(title, str(error), parent=parent or self.root)
