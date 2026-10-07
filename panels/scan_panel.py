# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import resolve_output_file_conflict, safe_show_info
from core.path_validation import normalize_windows_display_path
from core.paths import build_output_path, open_path_with_default_app, validate_required_path
from core.text_io import parse_list_text
from panels.base_panel import BasePanel
from services.scan_service import panoramic_scan
from ui.dialogs import edit_exclude_settings, edit_extra_text_settings
from ui.theme import styled_button, styled_frame
from ui.widgets import browse_folder, create_entry_row, make_checkbutton, open_output_file


class ScanPanel(BasePanel):
    config_key = "scan"

    def build(self):
        body = self.make_body()
        self.source_folder = tk.StringVar(value=self.cfg["source_folder"])
        self.output_folder = tk.StringVar(value=self.cfg["output_folder"])
        self.output_filename = tk.StringVar(value=self.cfg["output_filename"])

        for field, label, variable in (
            ("source_folder", "源文件夹", self.source_folder),
            ("output_folder", "输出文件夹", self.output_folder),
        ):
            create_entry_row(
                body, label, variable, commits=self.commits, config=self.cfg,
                field=field, page=self.config_key, history_key=f"scan.{field}",
                value_normalizer=normalize_windows_display_path,
                browse_command=lambda current=variable: browse_folder(current, parent=self.root),
                open_command=lambda current=variable: open_path_with_default_app(current.get(), self.root),
            )

        create_entry_row(
            body, "输出文件名", self.output_filename,
            commits=self.commits, config=self.cfg, field="output_filename",
            page=self.config_key, history_key="scan.output_filename",
            open_fields={"output_folder", "output_filename"},
            open_command=lambda: open_output_file(self.output_folder, self.output_filename, self.root),
        )

        options = styled_frame(body, bg=THEME["bg_panel"])
        options.pack(fill="x", pady=(10, 0))
        for field, label in (
            ("include_size", "输出文件大小"),
            ("include_date", "输出文件日期"),
            ("open_after_done", "完成后立即打开成果文件"),
            ("force_overwrite", "成果文件已存在时强制覆盖"),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            make_checkbutton(
                options, label, variable, commits=self.commits, config=self.cfg, field=field,
            ).pack(side="left", padx=(0, 16))

        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(12, 0))
        styled_button(row, "排除名单", self.open_exclude_dialog, width=10).pack(
            side="left", padx=(0, 8),
        )
        styled_button(row, "附加文本", self.open_extra_text_dialog, width=10).pack(
            side="left", padx=(0, 8),
        )
        styled_button(row, "开始扫描", self.execute, width=12, accent=True).pack(side="right")

    def open_exclude_dialog(self):
        if edit_exclude_settings(
            self.root, "全景扫描 - 排除名单", self.cfg, self.commits, "scan",
        ):
            self.log("全景扫描 - 排除名单 已保存")

    def open_extra_text_dialog(self):
        if edit_extra_text_settings(
            self.root, "全景扫描 - 附加文本", self.cfg, self.commits, "scan",
        ):
            self.log("全景扫描 - 附加文本 已保存")

    def execute(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._execute()

    def _execute(self):
        cfg = self.prepare({
            "source_folder", "output_folder", "output_filename",
            "exclude_folders", "exclude_files", "exclude_extensions",
            "preamble_text", "ending_text", "include_size", "include_date",
            "open_after_done", "force_overwrite",
        })
        if cfg is None:
            return
        try:
            source = validate_required_path(cfg["source_folder"], "源文件夹")
            folder = validate_required_path(cfg["output_folder"], "输出文件夹")
            filename = validate_required_path(cfg["output_filename"], "输出文件名")
            output = resolve_output_file_conflict(
                build_output_path(folder, filename), cfg["force_overwrite"], self.root,
            )
            if output is None:
                return

            self.set_status("正在执行全景扫描...")
            self.log("用户启动：路径全景扫描")
            self.commits.metrics["business_starts"] += 1
            result = panoramic_scan(
                source_folder=source, output_file=output,
                exclude_folders=parse_list_text(cfg["exclude_folders"]),
                exclude_files=parse_list_text(cfg["exclude_files"]),
                exclude_extensions=parse_list_text(cfg["exclude_extensions"], normalize_ext=True),
                preamble_text=cfg["preamble_text"], ending_text=cfg["ending_text"],
                include_size=cfg["include_size"], include_date=cfg["include_date"],
                log_func=self.log,
            )
        except (OSError, ValueError) as error:
            self.handle_error("全景扫描", error)
            return

        self.set_status("全景扫描完成")
        safe_show_info(
            "成功",
            f"全景扫描完成！\n\n目录 {result.dir_count} 个，文件 {result.file_count} 个。\n"
            f"已保存到：\n{result.output_file}",
            self.root,
        )
        if cfg["open_after_done"]:
            open_path_with_default_app(result.output_file, self.root)