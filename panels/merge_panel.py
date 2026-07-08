# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import TclError

from core.constants import THEME
from core.message_utils import resolve_output_file_conflict, safe_show_error, safe_show_info
from core.paths import (
    build_output_path,
    center_window,
    open_path_with_default_app,
    validate_required_path,
)
from core.text_io import get_text_value, parse_list_text, set_text_value
from panels.base_panel import BasePanel
from services.merge_service import (
    merge_project_files,
    merge_demand_files,
    validate_and_normalize_demand_file_list,
    summarize_demand_file_list_issues,
)
from ui.dialogs import (
    edit_exclude_settings,
    edit_extra_text_settings,
    create_managed_text_box,
    close_text_edit_dialog_with_confirm,
)
from ui.theme import styled_frame, styled_button
from ui.widgets import (
    create_entry_row,
    browse_folder,
    open_output_file,
    bind_autosave,
    make_checkbutton,
)


class MergePanel(BasePanel):
    config_key = "merge"

    def build(self):
        body = self.make_body()

        self.source_folder = tk.StringVar(value=self.cfg["source_folder"])
        self.output_folder = tk.StringVar(value=self.cfg["output_folder"])
        self.output_filename = tk.StringVar(value=self.cfg["output_filename"])
        self.code_header_line = tk.StringVar(value=self.cfg["code_header_line"])
        self.code_footer_line = tk.StringVar(value=self.cfg["code_footer_line"])
        self.open_after_done = tk.BooleanVar(value=self.cfg["open_after_done"])
        self.force_overwrite = tk.BooleanVar(value=self.cfg["force_overwrite"])

        create_entry_row(
            body,
            "源文件夹",
            self.source_folder,
            browse_command=lambda: browse_folder(self.source_folder),
            open_command=lambda: open_path_with_default_app(self.source_folder.get(), self.root),
            config_data=self.config_data,
            history_key="merge.source_folder",
            save_config=self.save_config,
        )

        create_entry_row(
            body,
            "输出文件夹",
            self.output_folder,
            browse_command=lambda: browse_folder(self.output_folder),
            open_command=lambda: open_path_with_default_app(self.output_folder.get(), self.root),
            config_data=self.config_data,
            history_key="merge.output_folder",
            save_config=self.save_config,
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
            history_key="merge.output_filename",
            save_config=self.save_config,
        )

        create_entry_row(
            body,
            "开始标记",
            self.code_header_line,
            config_data=self.config_data,
            history_key="merge.code_header_line",
            save_config=self.save_config,
        )
        create_entry_row(
            body,
            "结束标记",
            self.code_footer_line,
            config_data=self.config_data,
            history_key="merge.code_footer_line",
            save_config=self.save_config,
        )

        option_row = styled_frame(body, bg=THEME["bg_panel"])
        option_row.pack(fill="x", pady=(10, 0))

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
            "按需合并",
            self.open_demand_merge_dialog,
            width=12,
        ).pack(side="right", padx=(8, 0))

        styled_button(
            btn_row,
            "常规合并",
            self.execute_regular_merge,
            width=12,
            accent=True,
        ).pack(side="right")

        bind_autosave(
            self.cfg,
            [
                (self.source_folder, "source_folder"),
                (self.output_folder, "output_folder"),
                (self.output_filename, "output_filename"),
                (self.code_header_line, "code_header_line"),
                (self.code_footer_line, "code_footer_line"),
                (self.open_after_done, "open_after_done"),
                (self.force_overwrite, "force_overwrite"),
            ],
            self.save_config,
        )

    def open_exclude_dialog(self):
        if edit_exclude_settings(
            self.root,
            "文件代码合并 - 排除名单",
            self.cfg,
            config_data=self.config_data,
            folders_favorite_key="merge_exclude_folders",
            files_favorite_key="merge_exclude_files",
            extensions_favorite_key="merge_exclude_extensions",
            save_config_func=self.save_config,
        ):
            self.save_config()
            self.log("文件代码合并 - 排除名单 已保存")

    def open_extra_text_dialog(self):
        if edit_extra_text_settings(
            self.root,
            "文件代码合并 - 附加文本",
            self.cfg,
            config_data=self.config_data,
            preamble_favorite_key="merge_regular_preamble",
            ending_favorite_key="merge_regular_ending",
            save_config_func=self.save_config,
        ):
            self.save_config()
            self.log("文件代码合并 - 附加文本 已保存")

    def collect_config(self):
        self.cfg["source_folder"] = self.source_folder.get().strip()
        self.cfg["output_folder"] = self.output_folder.get().strip()
        self.cfg["output_filename"] = self.output_filename.get().strip()
        self.cfg["code_header_line"] = self.code_header_line.get().strip()
        self.cfg["code_footer_line"] = self.code_footer_line.get().strip()
        self.cfg["open_after_done"] = self.open_after_done.get()
        self.cfg["force_overwrite"] = self.force_overwrite.get()
        self.save_config()
        return self.cfg

    def resolve_common_output_and_markers(self, parent=None):
        cfg = self.collect_config()

        output_folder = validate_required_path(cfg["output_folder"], "输出文件夹")
        output_filename = validate_required_path(cfg["output_filename"], "输出文件名")
        code_header_line = validate_required_path(cfg["code_header_line"], "开始标记")
        code_footer_line = validate_required_path(cfg["code_footer_line"], "结束标记")

        output_file = build_output_path(output_folder, output_filename)
        output_file = resolve_output_file_conflict(
            output_file,
            cfg["force_overwrite"],
            parent=parent or self.root,
        )

        return cfg, output_file, code_header_line, code_footer_line

    def execute_regular_merge(self):
        try:
            cfg = self.collect_config()

            source_folder = validate_required_path(cfg["source_folder"], "源文件夹")
            cfg, output_file, code_header_line, code_footer_line = self.resolve_common_output_and_markers()

            self.set_status("正在常规合并文件代码...")
            self.log("用户启动：常规合并")

            result = merge_project_files(
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
                code_header_line=code_header_line,
                code_footer_line=code_footer_line,
                log_func=self.log,
            )

            self.set_status("常规文件代码合并完成")

            safe_show_info(
                "成功",
                f"常规合并完成！\n\n总共处理了 {result.total_files} 个文件。\n\n已保存到：\n{result.output_file}",
                parent=self.root,
            )

            if cfg["open_after_done"]:
                self.log("正在打开成果文件")
                open_path_with_default_app(result.output_file, self.root)

        except Exception as e:
            self.handle_error("常规合并", e)

    def open_demand_merge_dialog(self):
        self.collect_config()

        dialog = tk.Toplevel(self.root)
        dialog.title("按需合并")
        dialog.configure(bg=THEME["bg"])
        center_window(dialog, 980, 740)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog,
            text="按需合并：左侧每行一个文件绝对路径。程序会自动修正常见格式问题、自动去重；错误行会红色高亮，请原位修改后再次执行。",
            bg=THEME["bg"],
            fg=THEME["fg_dim"],
            font=THEME["font_main"],
            anchor="w",
            justify="left",
            wraplength=930,
        ).pack(fill="x", padx=16, pady=(12, 3))

        tk.Label(
            dialog,
            text="快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。收藏按钮位于各文本框标题栏右侧。",
            bg=THEME["bg"],
            fg=THEME["fg_dim"],
            font=THEME["font_main"],
            anchor="w",
            justify="left",
            wraplength=930,
        ).pack(fill="x", padx=16, pady=(0, 6))

        status_var = tk.StringVar(value="")

        body = styled_frame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=6)

        left = styled_frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right = styled_frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        file_list_text = create_managed_text_box(
            parent=left,
            label_text="合并名单",
            initial_value=self.cfg.get("demand_file_list_text", ""),
            height=28,
            mono=True,
            config_data=self.config_data,
            favorite_key="demand_file_list",
            save_config_func=self.save_config,
        )

        file_list_text.tag_configure("invalid_line", background=THEME["danger_bg"])
        file_list_text.tag_lower("invalid_line")

        preamble_text = create_managed_text_box(
            parent=right,
            label_text="前言文本",
            initial_value=self.cfg.get("demand_preamble_text", ""),
            height=12,
            mono=False,
            config_data=self.config_data,
            favorite_key="demand_preamble",
            save_config_func=self.save_config,
        )

        ending_text = create_managed_text_box(
            parent=right,
            label_text="后语文本",
            initial_value=self.cfg.get("demand_ending_text", ""),
            height=12,
            mono=False,
            config_data=self.config_data,
            favorite_key="demand_ending",
            save_config_func=self.save_config,
        )

        bottom = styled_frame(dialog)
        bottom.pack(fill="x", padx=16, pady=(6, 14))

        tk.Label(
            bottom,
            textvariable=status_var,
            bg=THEME["bg"],
            fg=THEME["fg_dim"],
            font=THEME["font_main"],
            anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)

        def save_changes():
            self.cfg["demand_file_list_text"] = get_text_value(file_list_text)
            self.cfg["demand_preamble_text"] = get_text_value(preamble_text)
            self.cfg["demand_ending_text"] = get_text_value(ending_text)
            self.save_config()

        def on_execute():
            self.execute_demand_merge(
                dialog,
                file_list_text,
                preamble_text,
                ending_text,
                status_var,
            )

        close_dialog = close_text_edit_dialog_with_confirm(
            dialog,
            [file_list_text, preamble_text, ending_text],
            save_changes,
        )

        styled_button(bottom, "取消", close_dialog, width=10).pack(
            side="right",
            padx=(8, 0),
        )

        styled_button(
            bottom,
            "执行按需合并",
            on_execute,
            width=14,
            accent=True,
        ).pack(side="right")

    def highlight_demand_file_list_errors(self, file_list_text, invalid_line_numbers):
        file_list_text.tag_remove("invalid_line", "1.0", "end")

        for line_number in invalid_line_numbers:
            file_list_text.tag_add(
                "invalid_line",
                f"{line_number}.0",
                f"{line_number}.end",
            )

        try:
            file_list_text.tag_lower("invalid_line")
            file_list_text.tag_raise("search_match")
            file_list_text.tag_raise("search_current")
        except TclError:
            pass

    def execute_demand_merge(
        self,
        dialog,
        file_list_text,
        preamble_text,
        ending_text,
        status_var,
    ):
        try:
            validation = validate_and_normalize_demand_file_list(
                get_text_value(file_list_text)
            )

            set_text_value(file_list_text, validation["normalized_text"])
            self.highlight_demand_file_list_errors(
                file_list_text,
                validation["invalid_line_numbers"],
            )

            self.cfg["demand_file_list_text"] = get_text_value(file_list_text)
            self.cfg["demand_preamble_text"] = get_text_value(preamble_text)
            self.cfg["demand_ending_text"] = get_text_value(ending_text)
            self.save_config()

            if validation["invalid_line_numbers"]:
                issue_summary = summarize_demand_file_list_issues(
                    validation["issue_counts"]
                )
                status_var.set(
                    f"名单校验未通过，已高亮 {len(validation['invalid_line_numbers'])} 行。{issue_summary}"
                )
                safe_show_error(
                    "名单需要修正",
                    f"合并名单中有 {len(validation['invalid_line_numbers'])} 行需要修正，已在左侧用红色高亮。\n\n请直接在原名单中修改或删除后，再次点击【执行按需合并】。",
                    parent=dialog,
                )
                return

            file_paths = validation["valid_paths"]

            if not file_paths:
                status_var.set("合并名单为空。")
                safe_show_error("名单为空", "请至少填写一个有效文件路径。", parent=dialog)
                return

            cfg, output_file, code_header_line, code_footer_line = self.resolve_common_output_and_markers(
                parent=dialog
            )

            self.set_status("正在按需合并文件代码...")
            self.log("用户启动：按需合并")

            duplicate_hint = ""

            if validation["duplicate_count"]:
                duplicate_hint = f"\n已自动去重 {validation['duplicate_count']} 条重复路径。"
                self.log(f"按需合并名单已自动去重 {validation['duplicate_count']} 条")

            result = merge_demand_files(
                file_paths=file_paths,
                output_file=output_file,
                preamble_text=get_text_value(preamble_text),
                ending_text=get_text_value(ending_text),
                code_header_line=code_header_line,
                code_footer_line=code_footer_line,
                log_func=self.log,
            )

            self.set_status("按需文件代码合并完成")
            status_var.set("按需合并完成。")

            safe_show_info(
                "成功",
                f"按需合并完成！\n\n总共合并了 {result.total_files} 个文件。{duplicate_hint}\n\n已保存到：\n{result.output_file}",
                parent=dialog,
            )

            if cfg["open_after_done"]:
                self.log("正在打开成果文件")
                open_path_with_default_app(result.output_file, self.root)

            dialog.destroy()

        except Exception as e:
            self.set_status("按需合并失败")
            status_var.set(f"按需合并失败：{e}")
            self.log(f"按需合并失败：{e}")
            safe_show_error("错误", str(e), parent=dialog)
