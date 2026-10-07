# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_show_info
from core.path_validation import normalize_windows_display_path
from core.paths import open_path_with_default_app, validate_required_path
from panels.base_panel import BasePanel
from services.restore_service import split_and_restore
from ui.theme import styled_button, styled_frame, styled_label_frame
from ui.widgets import browse_folder, browse_open_file, create_entry_row, make_checkbutton, make_radiobutton


class RestorePanel(BasePanel):
    config_key = "restore"

    def build(self):
        body = self.make_body()
        for field, label in (
            ("source_txt_file", "源txt文件"),
            ("target_folder", "目标文件夹"),
            ("code_header_line", "开始标记"),
            ("code_footer_line", "结束标记"),
        ):
            variable = tk.StringVar(value=self.cfg[field])
            setattr(self, field, variable)
            browse = None
            opener = None
            normalizer = str.strip
            if field == "source_txt_file":
                browse = lambda current=variable: browse_open_file(current, parent=self.root)
            elif field == "target_folder":
                browse = lambda current=variable: browse_folder(current, parent=self.root)
            if browse is not None:
                normalizer = normalize_windows_display_path
                opener = lambda current=variable: open_path_with_default_app(current.get(), self.root)
            create_entry_row(
                body, label, variable, commits=self.commits, config=self.cfg,
                field=field, page=self.config_key, history_key=f"restore.{field}",
                value_normalizer=normalizer, browse_command=browse, open_command=opener,
            )

        self.existing_file_policy = tk.StringVar(value=self.cfg["existing_file_policy"])
        policy = styled_label_frame(body, "已有文件处理")
        policy.pack(fill="x", pady=(10, 0))
        for label, value in (
            ("覆盖已有文件", "overwrite"), ("自动改名", "rename"), ("跳过已有文件", "skip"),
        ):
            make_radiobutton(
                policy, label, self.existing_file_policy, value,
                commits=self.commits, config=self.cfg, field="existing_file_policy",
            ).pack(side="left", padx=12, pady=6)

        self.open_after_done = tk.BooleanVar(value=self.cfg["open_after_done"])
        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(10, 0))
        make_checkbutton(
            row, "完成后立即打开目标文件夹", self.open_after_done,
            commits=self.commits, config=self.cfg, field="open_after_done",
        ).pack(side="left")
        buttons = styled_frame(body, bg=THEME["bg_panel"])
        buttons.pack(fill="x", pady=(12, 0))
        styled_button(buttons, "开始还原", self.execute, width=12, accent=True).pack(side="right")

    def execute(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._execute()

    def _execute(self):
        cfg = self.prepare({
            "source_txt_file", "target_folder", "code_header_line",
            "code_footer_line", "existing_file_policy", "open_after_done",
        })
        if cfg is None:
            return
        try:
            source = validate_required_path(cfg["source_txt_file"], "源txt文件")
            target = validate_required_path(cfg["target_folder"], "目标文件夹")
            header = validate_required_path(cfg["code_header_line"], "开始标记")
            footer = validate_required_path(cfg["code_footer_line"], "结束标记")
            self.set_status("正在拆分还原代码...")
            self.log("用户启动：代码拆分还原")
            self.commits.metrics["business_starts"] += 1
            result = split_and_restore(
                source_txt_file=source, target_root_folder=target,
                code_header_line=header, code_footer_line=footer,
                existing_file_policy=cfg["existing_file_policy"], log_func=self.log,
            )
        except (OSError, ValueError) as error:
            self.handle_error("代码拆分还原", error)
            return

        self.set_status("代码拆分还原完成")
        message = (
            f"还原任务完成！\n\n成功还原了 {result.restored_count} 个文件。\n"
            f"跳过了 {result.skipped_count} 个文件。\n\n目标目录：\n{result.target_folder}"
        )
        if result.log_path:
            message += f"\n\n跳过日志：\n{result.log_path}"
        safe_show_info("操作成功", message, self.root)
        if cfg["open_after_done"]:
            open_path_with_default_app(result.target_folder, self.root)