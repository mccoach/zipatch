# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_show_error
from ui.theme import styled_frame


class BasePanel(tk.Frame):
    """面板组织自己的操作；编辑和保存通过应用提交控制器协作。"""

    config_key = None

    def __init__(self, parent, app):
        super().__init__(parent, bg=THEME["bg_panel"])
        self.app = app
        self.root = app.root
        self.config_data = app.config_data
        self.cfg = self.config_data[self.config_key]
        self.commits = app.commits
        self.log = app.log
        self.set_status = app.status_var.set
        self.build()

    def build(self):
        raise NotImplementedError

    def make_body(self):
        body = styled_frame(self, bg=THEME["bg_panel"])
        body.pack(fill="x")
        return body

    def prepare(self, fields=None, parent=None):
        return self.commits.prepare_business(self.config_key, fields, parent)

    def handle_error(self, title, error, parent=None):
        self.set_status("执行失败")
        self.log(f"{title}失败：{error}")
        safe_show_error(title, str(error), parent=parent or self.root)