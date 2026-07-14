# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import resolve_output_file_conflict, safe_show_info
from core.path_validation import normalize_windows_display_path
from core.paths import (
    build_output_path,
    open_path_with_default_app,
    validate_required_path,
)
from core.text_io import parse_list_text
from panels.base_panel import BasePanel
from services.scan_service import panoramic_scan
from ui.dialogs import edit_exclude_settings, edit_extra_text_settings
from ui.theme import styled_frame, styled_button
from ui.widgets import (
    create_entry_row,
    browse_folder,
    open_output_file,
    bind_autosave,
    make_checkbutton,
)


class ScanPanel(BasePanel):
    config_key = "scan"

    def build(self):
        body = self.make_body()

        self.source_folder = tk.StringVar(value=self.cfg["source_folder"])
        self.output_folder = tk.StringVar(value=self.cfg["output_folder"])
        self.output_filename = tk.StringVar(value=self.cfg["output_filename"])
        self.include_size = tk.BooleanVar(value=self.cfg.get("include_size", False))
        self.include_date = tk.BooleanVar(value=self.cfg.get("include_date", False))
        self.open_after_done = tk.BooleanVar(value=self.cfg["open_after_done"])
        self.force_overwrite = tk.BooleanVar(value=self.cfg["force_overwrite"])

        create_entry_row(
            body,
            "源文件夹",
            self.source_folder,
            browse_command=lambda: browse_folder(self.source_folder),
            open_command=lambda: open_path_with_default_app(self.source_folder.get(), self.root),
            config_data=self.config_data,
            history_key="scan.source_folder",
            save_config=self.save_config,
            value_normalizer=normalize_windows_display_path,
        )

        create_entry_row(
            body,
            "输出文件夹",
            self.output_folder,
            browse_command=lambda: browse_folder(self.output_folder),
            open_command=lambda: open_path_with_default_app(self.output_folder.get(), self.root),
            config_data=self.config_data,
            history_key="scan.output_folder",
            save_config=self.save_config,
            value_normalizer=normalize_windows_display_path,
        )

        create_entry_row(
            body,
            "输出文件名",
            self.output_filename,
            open_command=lambda: open_output_file(
                self.output_folder,
                self.output_filename,
                self.root,
            ),
            config_data=self.config_data,
            history_key="scan.output_filename",
            save_config=self.save_config,
        )

        option_row = styled_frame(body, bg=THEME["bg_panel"])
        option_row.pack(fill="x", pady=(10, 0))

        make_checkbutton(
            option_row,
            "输出文件大小",
            self.include_size,
        ).pack(side="left", padx=(0, 10))

        make_checkbutton(
            option_row,
            "输出文件日期",
            self.include_date,
        ).pack(side="left", padx=(0, 24))

        make_checkbutton(
            option_row,
            "完成后立即打开成果文件",
            self.open_after_done,
        ).pack(side="left", padx=(0, 24))

        make_checkbutton(
            option_row,
            "成果文件已存在时强制覆盖",
            self.force_overwrite,
        ).pack(side="left")

        btn_row = styled_frame(body, bg=THEME["bg_panel"])
        btn_row.pack(fill="x", pady=(12, 0))

        styled_button(
            btn_row,
            "排除名单",
            self.open_exclude_dialog,
            width=10,
        ).pack(side="left", padx=(0, 8))

        styled_button(
            btn_row,
            "附加文本",
            self.open_extra_text_dialog,
            width=10,
        ).pack(side="left", padx=(0, 8))

        styled_button(
            btn_row,
            "开始扫描",
            self.execute,
            width=12,
            accent=True,
        ).pack(side="right")

        bind_autosave(
            self.cfg,
            [
                (self.source_folder, "source_folder"),
                (self.output_folder, "output_folder"),
                (self.output_filename, "output_filename"),
                (self.include_size, "include_size"),
                (self.include_date, "include_date"),
                (self.open_after_done, "open_after_done"),
                (self.force_overwrite, "force_overwrite"),
            ],
            self.save_config,
        )

    def open_exclude_dialog(self):
        if edit_exclude_settings(
            self.root,
            "全景扫描 - 排除名单",
            self.cfg,
            config_data=self.config_data,
            folders_favorite_key="scan_exclude_folders",
            files_favorite_key="scan_exclude_files",
            extensions_favorite_key="scan_exclude_extensions",
            save_config_func=self.save_config,
            folders_wrap_config_key="scan.exclude_folders",
            files_wrap_config_key="scan.exclude_files",
            extensions_wrap_config_key="scan.exclude_extensions",
        ):
            self.save_config()
            self.log("全景扫描 - 排除名单 已保存")

    def open_extra_text_dialog(self):
        if edit_extra_text_settings(
            self.root,
            "全景扫描 - 附加文本",
            self.cfg,
            config_data=self.config_data,
            preamble_favorite_key="scan_preamble",
            ending_favorite_key="scan_ending",
            save_config_func=self.save_config,
            preamble_wrap_config_key="scan.preamble",
            ending_wrap_config_key="scan.ending",
        ):
            self.save_config()
            self.log("全景扫描 - 附加文本 已保存")

    def collect_config(self):
        self.cfg["source_folder"] = self.source_folder.get().strip()
        self.cfg["output_folder"] = self.output_folder.get().strip()
        self.cfg["output_filename"] = self.output_filename.get().strip()
        self.cfg["include_size"] = self.include_size.get()
        self.cfg["include_date"] = self.include_date.get()
        self.cfg["open_after_done"] = self.open_after_done.get()
        self.cfg["force_overwrite"] = self.force_overwrite.get()
        self.save_config()
        return self.cfg

    def execute(self):
        try:
            cfg = self.collect_config()

            source_folder = validate_required_path(cfg["source_folder"], "源文件夹")
            output_folder = validate_required_path(cfg["output_folder"], "输出文件夹")
            output_filename = validate_required_path(cfg["output_filename"], "输出文件名")

            output_file = build_output_path(output_folder, output_filename)
            output_file = resolve_output_file_conflict(
                output_file,
                cfg["force_overwrite"],
                parent=self.root,
            )

            self.set_status("正在执行全景扫描...")
            self.log("用户启动：路径全景扫描")

            result = panoramic_scan(
                source_folder=source_folder,
                output_file=output_file,
                exclude_folders=parse_list_text(cfg["exclude_folders"]),
                exclude_files=parse_list_text(cfg["exclude_files"]),
                exclude_extensions=parse_list_text(
                    cfg["exclude_extensions"],
                    normalize_ext=True,
                ),
                preamble_text=cfg["preamble_text"],
                ending_text=cfg["ending_text"],
                include_size=cfg.get("include_size", False),
                include_date=cfg.get("include_date", False),
                log_func=self.log,
            )

            self.set_status("全景扫描完成")

            safe_show_info(
                "成功",
                f"全景扫描完成！\n\n目录 {result.dir_count} 个，文件 {result.file_count} 个。\n已保存到：\n{result.output_file}",
                parent=self.root,
            )

            if cfg["open_after_done"]:
                self.log("正在打开成果文件")
                open_path_with_default_app(result.output_file, self.root)

        except Exception as e:
            self.handle_error("全景扫描", e)
