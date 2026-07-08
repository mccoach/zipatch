# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_show_info
from core.paths import (
    open_path_with_default_app,
    validate_required_path,
)
from panels.base_panel import BasePanel
from services.restore_service import split_and_restore
from ui.theme import styled_frame, styled_label_frame, styled_button
from ui.widgets import (
    create_entry_row,
    browse_folder,
    browse_open_file,
    bind_autosave,
    make_checkbutton,
    make_radiobutton,
)


class RestorePanel(BasePanel):
    config_key = "restore"

    def build(self):
        body = self.make_body()

        self.source_txt_file = tk.StringVar(value=self.cfg["source_txt_file"])
        self.target_folder = tk.StringVar(value=self.cfg["target_folder"])
        self.code_header_line = tk.StringVar(value=self.cfg["code_header_line"])
        self.code_footer_line = tk.StringVar(value=self.cfg["code_footer_line"])
        self.existing_file_policy = tk.StringVar(value=self.cfg["existing_file_policy"])
        self.open_after_done = tk.BooleanVar(value=self.cfg["open_after_done"])

        create_entry_row(
            body,
            "源txt文件",
            self.source_txt_file,
            browse_command=lambda: browse_open_file(self.source_txt_file),
            open_command=lambda: open_path_with_default_app(self.source_txt_file.get(), self.root),
            config_data=self.config_data,
            history_key="restore.source_txt_file",
            save_config=self.save_config,
        )

        create_entry_row(
            body,
            "目标文件夹",
            self.target_folder,
            browse_command=lambda: browse_folder(self.target_folder),
            open_command=lambda: open_path_with_default_app(self.target_folder.get(), self.root),
            config_data=self.config_data,
            history_key="restore.target_folder",
            save_config=self.save_config,
        )

        create_entry_row(
            body,
            "开始标记",
            self.code_header_line,
            config_data=self.config_data,
            history_key="restore.code_header_line",
            save_config=self.save_config,
        )
        create_entry_row(
            body,
            "结束标记",
            self.code_footer_line,
            config_data=self.config_data,
            history_key="restore.code_footer_line",
            save_config=self.save_config,
        )

        policy_frame = styled_label_frame(body, "已有文件处理")
        policy_frame.pack(fill="x", pady=(10, 0))

        for text, value in [
            ("覆盖已有文件", "overwrite"),
            ("自动改名", "rename"),
            ("跳过已有文件", "skip"),
        ]:
            make_radiobutton(
                policy_frame,
                text,
                self.existing_file_policy,
                value,
            ).pack(side="left", padx=12, pady=6)

        option_row = styled_frame(body, bg=THEME["bg_panel"])
        option_row.pack(fill="x", pady=(10, 0))

        make_checkbutton(
            option_row,
            "完成后立即打开目标文件夹",
            self.open_after_done,
        ).pack(side="left")

        btn_row = styled_frame(body, bg=THEME["bg_panel"])
        btn_row.pack(fill="x", pady=(12, 0))

        styled_button(
            btn_row,
            "开始还原",
            self.execute,
            width=12,
            accent=True,
        ).pack(side="right")

        bind_autosave(
            self.cfg,
            [
                (self.source_txt_file, "source_txt_file"),
                (self.target_folder, "target_folder"),
                (self.code_header_line, "code_header_line"),
                (self.code_footer_line, "code_footer_line"),
                (self.existing_file_policy, "existing_file_policy"),
                (self.open_after_done, "open_after_done"),
            ],
            self.save_config,
        )

    def collect_config(self):
        self.cfg["source_txt_file"] = self.source_txt_file.get().strip()
        self.cfg["target_folder"] = self.target_folder.get().strip()
        self.cfg["code_header_line"] = self.code_header_line.get().strip()
        self.cfg["code_footer_line"] = self.code_footer_line.get().strip()
        self.cfg["existing_file_policy"] = self.existing_file_policy.get()
        self.cfg["open_after_done"] = self.open_after_done.get()
        self.save_config()
        return self.cfg

    def execute(self):
        try:
            cfg = self.collect_config()

            source_txt_file = validate_required_path(cfg["source_txt_file"], "源txt文件")
            target_folder = validate_required_path(cfg["target_folder"], "目标文件夹")
            code_header_line = validate_required_path(cfg["code_header_line"], "开始标记")
            code_footer_line = validate_required_path(cfg["code_footer_line"], "结束标记")

            self.set_status("正在拆分还原代码...")
            self.log("用户启动：代码拆分还原")

            result = split_and_restore(
                source_txt_file=source_txt_file,
                target_root_folder=target_folder,
                code_header_line=code_header_line,
                code_footer_line=code_footer_line,
                existing_file_policy=cfg["existing_file_policy"],
                log_func=self.log,
            )

            self.set_status("代码拆分还原完成")

            msg = (
                f"还原任务完成！\n\n"
                f"成功还原了 {result.restored_count} 个文件。\n"
                f"跳过了 {result.skipped_count} 个文件。\n\n"
                f"目标目录：\n{result.target_folder}"
            )

            if result.log_path:
                msg += f"\n\n跳过日志：\n{result.log_path}"

            safe_show_info("操作成功", msg, parent=self.root)

            if cfg["open_after_done"]:
                self.log("正在打开目标文件夹")
                open_path_with_default_app(result.target_folder, self.root)

        except Exception as e:
            self.handle_error("代码拆分还原", e)
