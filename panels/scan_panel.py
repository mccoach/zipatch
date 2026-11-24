# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from ui.message_dialogs import safe_show_info
from ui.widgets import resolve_output_file_conflict, open_path_with_default_app
from core.path_validation import normalize_windows_display_path
from core.paths import resolve_output_path, validate_required_path
from ui.output_path import create_output_path_row
from core.exclusion_rules import PreparedExclusions
from panels.base_panel import BasePanel
from services.scan_service import panoramic_scan
from ui.dialogs import edit_exclude_settings, edit_extra_text_settings
from ui.theme import styled_button, styled_frame
from ui.page_settings import create_page_settings
from ui.widgets import browse_folder, create_entry_row


class ScanPanel(BasePanel):
    config_key = "scan"

    def build(self):
        body = self.make_body()
        self.source_folder = tk.StringVar(value=self.cfg["source_folder"])
        self.output_path = tk.StringVar(master=self, value=self.cfg["output_path"])

        create_entry_row(
            body, "源文件夹", self.source_folder,
            commits=self.commits, config=self.cfg,
            field="source_folder", page=self.config_key,
            history_key="scan.source_folder",
            value_normalizer=normalize_windows_display_path,
            browse_command=lambda: browse_folder(
                self.source_folder, parent=self.root, commits=self.commits,
                history_key="scan.source_folder",
            ),
            open_command=lambda: open_path_with_default_app(
                self.source_folder.get(), self.root,
            ),
        )
        self.output_entry = create_output_path_row(body, self)

        _, _, variables = create_page_settings(
            body, self.commits, self.cfg,
            (
                ("open_after_done", "完成后打开成果", "完成后打开本次扫描成果文件"),
                ("show_actual_path", "显示实际路径", "显示链接来源的附加实际路径"),
                ("force_overwrite", "直接覆盖已有成果", "成果已存在时直接覆盖；未勾选时选择覆盖、自动改名或取消"),
                ("include_size", "显示文件大小", "在文件清单中显示实际文件大小"),
                ("include_date", "显示修改时间", "显示文件修改时间；经过链接的文件夹显示实际文件夹自身修改时间"),
            ),
            (
                ("exclude", "排除名单", self.open_exclude_dialog),
                ("extra", "附加文本", self.open_extra_text_dialog),
            ),
        )
        for field, variable in variables.items():
            setattr(self, field, variable)

        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(12, 0))
        styled_button(
            row, "开始扫描", self.execute, width=12, accent=True,
        ).pack(side="right")

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
            "source_folder", "output_path",
            "exclude_folders", "exclude_files",
            "preamble_text", "ending_text", "include_size", "include_date",
            "open_after_done", "force_overwrite", "show_actual_path",
        })
        if cfg is None:
            return
        try:
            exclusions = PreparedExclusions(
                self.commits.prepare_rule(
                    self.cfg, "exclude_folders", "directory", cfg["exclude_folders"],
                    semantics="exclusion",
                ),
                self.commits.prepare_rule(
                    self.cfg, "exclude_files", "file", cfg["exclude_files"],
                    semantics="exclusion",
                ),
            )
            source = validate_required_path(cfg["source_folder"], "源文件夹")
            output = resolve_output_file_conflict(
                resolve_output_path(cfg["output_path"]),
                cfg["force_overwrite"], self.root,
            )
            if output is None:
                return

            self.set_status("正在执行全景扫描...")
            self.log("用户启动：路径全景扫描")
            self.commits.metrics["business_starts"] += 1
            from ui.source_scope import make_source_node_selector

            result = panoramic_scan(
                select_source_links=make_source_node_selector(
                    self.root, self.commits,
                ),
                source_folder=source, output_file=output,
                exclusions=exclusions,
                preamble_text=cfg["preamble_text"], ending_text=cfg["ending_text"],
                include_size=cfg["include_size"], include_date=cfg["include_date"],
                show_actual_path=cfg["show_actual_path"],
                log_func=self.log,
            )
        except (OSError, ValueError) as error:
            self.handle_error("全景扫描", error)
            return

        self.set_status("全景扫描完成")
        safe_show_info(
            "成功",
            f"全景扫描完成！\n\n文件夹 {result.dir_count} 个，文件 {result.file_count} 个。\n"
            f"链接入口 {result.link_count} 个："
            f"文件软链接 {result.file_symlink_count}，"
            f"文件夹软链接 {result.directory_symlink_count}，"
            f"文件夹联接（Junction）{result.junction_count}。\n"
            "链接全链授权后按普通实体展开；未选在断点收口，不重复只读确认。\n"
            f"已保存到：\n{result.output_file}",
            self.root,
        )
        if cfg["open_after_done"]:
            open_path_with_default_app(result.output_file, self.root)