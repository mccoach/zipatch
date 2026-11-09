# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.exclusion_rules import PreparedExclusions
from core.path_validation import normalize_windows_display_path
from core.paths import build_output_path, validate_required_path
from panels.base_panel import BasePanel
from services.merge_service import merge_files
from ui.dialogs import edit_exclude_settings, edit_extra_text_settings
from ui.message_dialogs import safe_show_info
from ui.newline_settings import edit_newline_settings
from ui.operation_lock import locked_page
from ui.text_output_settings import edit_output_format_settings, show_output_help
from ui.theme import styled_button, styled_frame
from ui.widgets import (
    browse_folder, create_entry_row, make_checkbutton, make_radiobutton,
    open_output_file, open_path_with_default_app, resolve_output_file_conflict,
)


def merge_completion_message(result, mode):
    incomplete = result.read_failure_count or result.mtime_failures
    title = "合并完成，存在缺失信息" if incomplete else "合并完成"
    message = (
        f"{mode}成果已发布。\n\n处理文件：{result.total_files} 个。\n"
        f"主动忽略：{result.excluded_count} 个。\n"
        f"被动失败：{result.read_failure_count} 个。\n"
        f"修改时间未取得：{len(result.mtime_failures)} 个。\n\n"
        f"已保存到：\n{result.output_file}"
    )
    if result.excluded_count or result.read_failure_count:
        message += (
            "\n\n占位项未提供真实源码；成果保留对应文件路径和占位标识，"
            "请查看运行日志。"
        )
    if result.read_failure_count:
        message += (
            "\n被动失败项未成功读取或解码；请排查后重新合并，"
            "本次没有自动重试。"
        )
    if result.mtime_failures:
        message += "\n\n以下文件的修改时间未取得，追溯信息不完整：\n"
        message += "\n".join(path for path, _ in result.mtime_failures)
    return title, message


class MergePanel(BasePanel):
    config_key = "merge"

    def build(self):
        body = self.make_body()
        for field, label in (
            ("source_folder", "源文件夹"),
            ("output_folder", "输出文件夹"),
            ("output_filename", "输出文件名"),
        ):
            variable = tk.StringVar(value=self.cfg[field])
            setattr(self, field, variable)
            browse = None
            opener = None
            open_fields = None
            normalizer = str.strip
            if field in ("source_folder", "output_folder"):
                browse = lambda current=variable: browse_folder(
                    current, parent=self.root,
                )
                opener = lambda current=variable: open_path_with_default_app(
                    current.get(), self.root,
                )
                normalizer = normalize_windows_display_path
            else:
                opener = lambda: open_output_file(
                    self.output_folder, self.output_filename, self.root,
                )
                open_fields = {"output_folder", "output_filename"}
            create_entry_row(
                body, label, variable, commits=self.commits, config=self.cfg,
                field=field, page=self.config_key, history_key=f"merge.{field}",
                browse_command=browse, open_command=opener,
                open_fields=open_fields, value_normalizer=normalizer,
            )

        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(10, 0))
        self.merge_mode = tk.StringVar(value=self.cfg["merge_mode"])
        tk.Label(
            row, text="合并模式", bg=THEME["bg_panel"],
            fg=THEME["fg_label"], font=THEME["font_main"],
        ).pack(side="left", padx=(0, 4))
        for label, value in (
            ("仅目录", "directory"),
            ("仅列表", "list"),
            ("目录+列表", "combined"),
        ):
            make_radiobutton(
                row, label, self.merge_mode, value,
                commits=self.commits, config=self.cfg, field="merge_mode",
            ).pack(side="left", padx=(0, 4))

        for field, label in (
            ("open_after_done", "完成后立即打开成果文件"),
            ("force_overwrite", "成果文件已存在时强制覆盖"),
            ("normalize_text_newlines", "标准化文本换行"),
            ("record_file_mtime", "记录文件修改时间"),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            make_checkbutton(
                row, label, variable, commits=self.commits,
                config=self.cfg, field=field,
            ).pack(side="left", padx=(0, 6))

        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(12, 0))
        styled_button(
            row, "合并列表", self.open_merge_list_dialog, width=10,
        ).pack(side="left", padx=(0, 8))
        styled_button(
            row, "排除名单", self.open_exclude_dialog, width=10,
        ).pack(side="left", padx=(0, 8))
        styled_button(
            row, "附加文本", self.open_extra_text_dialog, width=10,
        ).pack(side="left", padx=(0, 8))
        styled_button(
            row, "标记与占位",
            lambda: edit_output_format_settings(
                self.root, self.commits, self.cfg, "merge",
            ),
            width=10,
        ).pack(side="left", padx=(0, 8))
        styled_button(
            row, "换行设置",
            lambda: edit_newline_settings(self.root, self.commits),
            width=10,
        ).pack(side="left", padx=(0, 8))
        styled_button(
            row, "正文规则", lambda: show_output_help(self.root), width=10,
        ).pack(side="left", padx=(0, 8))
        styled_button(
            row, "合并", self.execute_merge, width=12, accent=True,
        ).pack(side="right")

    def open_merge_list_dialog(self):
        from ui.demand_editor import open_demand_editor
        return open_demand_editor(self)

    def open_exclude_dialog(self):
        if edit_exclude_settings(
            self.root, "文件代码合并 - 排除名单",
            self.cfg, self.commits, "merge",
        ):
            self.log("文件代码合并 - 排除名单 已保存")

    def open_extra_text_dialog(self):
        if edit_extra_text_settings(
            self.root, "文件代码合并 - 附加文本",
            self.cfg, self.commits, "merge",
        ):
            self.log("文件代码合并 - 附加文本 已保存")

    def resolve_output_and_markers(self, cfg):
        folder = validate_required_path(cfg["output_folder"], "输出文件夹")
        name = validate_required_path(cfg["output_filename"], "输出文件名")
        header = validate_required_path(cfg["code_header_line"], "开始标记")
        footer = validate_required_path(cfg["code_footer_line"], "结束标记")
        output = resolve_output_file_conflict(
            build_output_path(folder, name), cfg["force_overwrite"], self.root,
        )
        return output, header, footer

    def execute_merge(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation(), locked_page(self):
            return self._execute_merge()

    def _execute_merge(self):
        mode = self.cfg["merge_mode"]
        fields = {
            "merge_mode", "output_folder", "output_filename",
            "preamble_text", "ending_text", "code_header_line",
            "code_footer_line", "open_after_done", "force_overwrite",
            "normalize_text_newlines", "record_file_mtime",
            "ignore_text", "failure_text",
        }
        if mode in ("directory", "combined"):
            fields |= {"source_folder", "exclude_folders", "exclude_files"}
        if mode in ("list", "combined"):
            fields |= {"source_folder", "demand_file_list_text"}
        cfg = self.prepare(fields)
        if cfg is None:
            return

        try:
            exclusions = None
            if cfg["merge_mode"] in ("directory", "combined"):
                validate_required_path(cfg["source_folder"], "源文件夹")
                exclusions = PreparedExclusions(
                    self.commits.prepare_rule(
                        self.cfg, "exclude_folders", "directory",
                        cfg["exclude_folders"], semantics="exclusion",
                    ),
                    self.commits.prepare_rule(
                        self.cfg, "exclude_files", "file",
                        cfg["exclude_files"], semantics="exclusion",
                    ),
                )
            output_rules = (
                self.commits.prepare_rule(
                    self.config_data["settings"], "crlf_file_list", "file",
                )
                if cfg["normalize_text_newlines"] else None
            )
            output, header, footer = self.resolve_output_and_markers(cfg)
            if output is None:
                return

            labels = {
                "directory": "仅目录",
                "list": "仅列表",
                "combined": "目录+列表",
            }
            mode_label = labels[cfg["merge_mode"]]
            self.set_status(f"正在合并文件代码：{mode_label}...")
            self.log(f"用户启动：合并（{mode_label}）")
            self.commits.metrics["business_starts"] += 1
            result = merge_files(
                mode=cfg["merge_mode"],
                source_folder=cfg.get("source_folder", ""),
                list_text=cfg.get("demand_file_list_text", ""),
                output_file=output, exclusions=exclusions,
                preamble_text=cfg["preamble_text"],
                ending_text=cfg["ending_text"],
                code_header_line=header, code_footer_line=footer,
                log_func=self.log,
                normalize_text_newlines=cfg["normalize_text_newlines"],
                record_file_mtime=cfg["record_file_mtime"],
                ignore_text=cfg["ignore_text"],
                failure_text=cfg["failure_text"],
                output_rules=output_rules,
            )
        except (OSError, ValueError) as error:
            self.handle_error("合并", error)
            return

        title, message = merge_completion_message(result, mode_label)
        self.set_status(title)
        safe_show_info(title, message, self.root)
        if cfg["open_after_done"]:
            open_path_with_default_app(result.output_file, self.root)
