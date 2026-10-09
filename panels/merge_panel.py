# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.message_utils import resolve_output_file_conflict, safe_show_error, safe_show_info
from core.path_validation import normalize_windows_display_path
from core.paths import build_output_path, center_window, open_path_with_default_app, validate_required_path
from core.exclusion_rules import prepare_exclusions
from core.text_io import set_text_value
from panels.base_panel import BasePanel
from services.merge_service import (
    merge_demand_files, merge_project_files,
    summarize_demand_file_list_issues, validate_and_normalize_demand_file_list,
)
from ui.dialogs import (
    TextDraftSession, create_managed_text_box, edit_exclude_settings, edit_extra_text_settings,
)
from ui.theme import styled_button, styled_frame
from ui.widgets import browse_folder, create_entry_row, make_checkbutton, open_output_file
from ui.window_manager import register_popup


class MergePanel(BasePanel):
    config_key = "merge"

    def build(self):
        body = self.make_body()
        for field, label in (
            ("source_folder", "源文件夹"), ("output_folder", "输出文件夹"),
            ("output_filename", "输出文件名"), ("code_header_line", "开始标记"),
            ("code_footer_line", "结束标记"),
        ):
            variable = tk.StringVar(value=self.cfg[field])
            setattr(self, field, variable)
            browse = None
            opener = None
            open_fields = None
            normalizer = str.strip
            if field in ("source_folder", "output_folder"):
                browse = lambda current=variable: browse_folder(current, parent=self.root)
                opener = lambda current=variable: open_path_with_default_app(current.get(), self.root)
                normalizer = normalize_windows_display_path
            elif field == "output_filename":
                opener = lambda: open_output_file(self.output_folder, self.output_filename, self.root)
                open_fields = {"output_folder", "output_filename"}
            create_entry_row(
                body, label, variable, commits=self.commits, config=self.cfg,
                field=field, page=self.config_key, history_key=f"merge.{field}",
                browse_command=browse, open_command=opener,
                open_fields=open_fields, value_normalizer=normalizer,
            )

        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(10, 0))
        for field, label in (
            ("open_after_done", "完成后立即打开成果文件"),
            ("force_overwrite", "成果文件已存在时强制覆盖"),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            make_checkbutton(
                row, label, variable, commits=self.commits, config=self.cfg, field=field,
            ).pack(side="left", padx=(0, 24))

        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(12, 0))
        styled_button(row, "排除名单", self.open_exclude_dialog, width=10).pack(
            side="left", padx=(0, 8),
        )
        styled_button(row, "附加文本", self.open_extra_text_dialog, width=10).pack(
            side="left", padx=(0, 8),
        )
        styled_button(row, "按需合并", self.open_demand_merge_dialog, width=12).pack(
            side="right", padx=(8, 0),
        )
        styled_button(row, "常规合并", self.execute_regular_merge, width=12, accent=True).pack(
            side="right",
        )

    def open_exclude_dialog(self):
        if edit_exclude_settings(
            self.root, "文件代码合并 - 排除名单", self.cfg, self.commits, "merge",
        ):
            self.log("文件代码合并 - 排除名单 已保存")

    def open_extra_text_dialog(self):
        if edit_extra_text_settings(
            self.root, "文件代码合并 - 附加文本", self.cfg, self.commits, "merge",
        ):
            self.log("文件代码合并 - 附加文本 已保存")

    def resolve_common_output_and_markers(self, cfg, parent):
        folder = validate_required_path(cfg["output_folder"], "输出文件夹")
        name = validate_required_path(cfg["output_filename"], "输出文件名")
        header = validate_required_path(cfg["code_header_line"], "开始标记")
        footer = validate_required_path(cfg["code_footer_line"], "结束标记")
        output = resolve_output_file_conflict(
            build_output_path(folder, name), cfg["force_overwrite"], parent,
        )
        return output, header, footer

    def execute_regular_merge(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._execute_regular_merge()

    def _execute_regular_merge(self):
        cfg = self.prepare({
            "source_folder", "output_folder", "output_filename",
            "exclude_folders", "exclude_files",
            "preamble_text", "ending_text", "code_header_line",
            "code_footer_line", "open_after_done", "force_overwrite",
        })
        if cfg is None:
            return
        try:
            exclusions = prepare_exclusions(cfg["exclude_folders"], cfg["exclude_files"])
            source = validate_required_path(cfg["source_folder"], "源文件夹")
            output, header, footer = self.resolve_common_output_and_markers(cfg, self.root)
            if output is None:
                return
            self.set_status("正在常规合并文件代码...")
            self.log("用户启动：常规合并")
            self.commits.metrics["business_starts"] += 1
            result = merge_project_files(
                source_folder=source, output_file=output,
                exclusions=exclusions,
                preamble_text=cfg["preamble_text"], ending_text=cfg["ending_text"],
                code_header_line=header, code_footer_line=footer, log_func=self.log,
            )
        except (OSError, ValueError) as error:
            self.handle_error("常规合并", error)
            return

        self.set_status("常规文件代码合并完成")
        safe_show_info(
            "成功",
            f"常规合并完成！\n\n总共处理了 {result.total_files} 个文件。\n\n"
            f"已保存到：\n{result.output_file}",
            self.root,
        )
        if cfg["open_after_done"]:
            open_path_with_default_app(result.output_file, self.root)

    def open_demand_merge_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("按需合并")
        dialog.configure(bg=THEME["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        center_window(dialog, 980, 740)
        tk.Label(
            dialog,
            text="合并名单每行填写一个文件绝对路径；行首半角分号 ; 可临时取消。"
                 "程序会规范化、去重并高亮错误行。"
                 "快捷键：Ctrl+F、Ctrl+H、Ctrl+Z、Ctrl+Y。",
            bg=THEME["bg"], fg=THEME["fg_dim"], font=THEME["font_main"],
            wraplength=930, anchor="w", justify="left",
        ).pack(fill="x", padx=16, pady=(12, 6))
        body = styled_frame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=6)
        left, right = styled_frame(body), styled_frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        widgets = {}
        for field, label, parent, favorite, wrap_key, height in (
            ("demand_file_list_text", "合并名单", left, "demand_file_list", "merge.demand_file_list", 28),
            ("demand_preamble_text", "前言文本", right, "demand_preamble", "merge.demand_preamble", 12),
            ("demand_ending_text", "后语文本", right, "demand_ending", "merge.demand_ending", 12),
        ):
            widgets[field] = create_managed_text_box(
                parent, label, self.cfg[field], height=height,
                mono=parent is left, commits=self.commits,
                favorite_key=favorite, wrap_config_key=wrap_key,
            )
        file_list = widgets["demand_file_list_text"]
        file_list.tag_configure("invalid_line", background=THEME["danger_bg"])
        file_list.tag_lower("invalid_line")
        session = TextDraftSession(dialog, self.commits, self.cfg, widgets)
        status = tk.StringVar(value="")
        bottom = styled_frame(dialog)
        bottom.pack(fill="x", padx=16, pady=(6, 14))
        tk.Label(
            bottom, textvariable=status, bg=THEME["bg"], fg=THEME["fg_dim"],
            anchor="w", font=THEME["font_main"],
        ).pack(side="left", fill="x", expand=True)
        styled_button(bottom, "取消", session.close, width=10).pack(
            side="right", padx=(8, 0),
        )
        styled_button(
            bottom, "执行按需合并",
            lambda: self.execute_demand_merge(session, status),
            width=14, accent=True,
        ).pack(side="right")
        register_popup(dialog, session.close, prepare_close=session.prepare_close)

    def execute_demand_merge(self, session, status):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._execute_demand_merge(session, status)

    def _execute_demand_merge(self, session, status):
        values = session.values()
        validation = validate_and_normalize_demand_file_list(values["demand_file_list_text"])
        values["demand_file_list_text"] = validation["normalized_text"]
        text = session.widgets["demand_file_list_text"]
        set_text_value(text, validation["normalized_text"])
        text.tag_remove("invalid_line", "1.0", "end")
        for number in validation["invalid_line_numbers"]:
            text.tag_add("invalid_line", f"{number}.0", f"{number}.end")
        text.tag_lower("invalid_line")
        text.tag_raise("search_match")
        text.tag_raise("search_current")

        fields = {"output_folder", "output_filename", "code_header_line", "code_footer_line"}
        self.commits.accept_editors(self.commits.select("merge", fields))
        cfg = {
            key: self.cfg[key] for key in (
                "output_folder", "output_filename", "code_header_line",
                "code_footer_line", "force_overwrite", "open_after_done",
            )
        }
        if not session.accept(values):
            return

        if validation["invalid_line_numbers"]:
            status.set(
                "名单校验未通过。"
                + summarize_demand_file_list_issues(validation["issue_counts"])
            )
            safe_show_error(
                "名单需要修正",
                "名单中存在错误，已红色高亮，请原位修改后再次执行。",
                session.dialog,
            )
            return
        if not validation["valid_paths"]:
            status.set("合并名单为空。")
            safe_show_error("名单为空", "请至少填写一个有效文件路径。", session.dialog)
            return

        try:
            output, header, footer = self.resolve_common_output_and_markers(cfg, session.dialog)
            if output is None:
                return
            self.set_status("正在按需合并文件代码...")
            self.log("用户启动：按需合并")
            self.commits.metrics["business_starts"] += 1
            result = merge_demand_files(
                file_paths=validation["valid_paths"], output_file=output,
                preamble_text=values["demand_preamble_text"],
                ending_text=values["demand_ending_text"],
                code_header_line=header, code_footer_line=footer, log_func=self.log,
            )
        except (OSError, ValueError) as error:
            status.set(f"按需合并失败：{error}")
            self.handle_error("按需合并", error, session.dialog)
            return

        self.set_status("按需文件代码合并完成")
        status.set("按需合并完成。")
        duplicate = validation["duplicate_count"]
        hint = f"\n已自动去重 {duplicate} 条重复路径。" if duplicate else ""
        safe_show_info(
            "成功",
            f"按需合并完成！\n\n总共合并了 {result.total_files} 个文件。{hint}\n\n"
            f"已保存到：\n{result.output_file}",
            session.dialog,
        )
        if cfg["open_after_done"]:
            open_path_with_default_app(result.output_file, self.root)
        session.destroy()