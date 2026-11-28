# -*- coding: utf-8 -*-

import tkinter as tk

from core.constants import THEME
from core.exclusion_rules import PreparedExclusions
from core.path_validation import normalize_windows_display_path
from core.paths import resolve_output_path, validate_required_path
from ui.output_path import create_output_path_row
from panels.base_panel import BasePanel
from services.merge_service import merge_files
from ui.demand_editor import SourceListEditor, show_source_list_help
from ui.dialogs import edit_exclude_settings, edit_extra_text_settings
from ui.message_dialogs import safe_show_info, safe_ask_risk_confirm
from ui.newline_settings import edit_newline_settings
from ui.operation_lock import locked_page
from ui.page_settings import create_page_settings
from ui.text_output_settings import edit_output_format_settings
from ui.theme import styled_button, styled_frame
from ui.widgets import (
    browse_folder, create_entry_row,
    open_path_with_default_app, resolve_output_file_conflict,
)


def merge_completion_message(result, mode):
    incomplete = result.read_failure_count or result.mtime_failures
    title = "合并完成，存在缺失信息" if incomplete else "合并完成"
    links = dict(result.link_counts)
    message = (
        f"{mode}成果已发布：成功正文文件 {result.total_files} 个。\n"
        f"已授权链接入口：文件硬链接 {links.get('file_hardlink', 0)}；"
        f"文件软链接 {links.get('file_symlink', 0)}；"
        f"文件夹软链接 {links.get('directory_symlink', 0)}；"
        f"文件夹联接（Junction）{links.get('junction', 0)}。\n\n"
        f"主动忽略：{result.excluded_count}；"
        f"正文失败：{result.read_failure_count}；"
        f"时间缺失：{len(result.mtime_failures)}。\n"
        f"正文引用记录：{result.reference_count}；"
        f"成功文件路径记录：{result.record_count}。\n"
        "同对象正文只保存一次，其他路径保留引用；拆分从容器取得完整正文。\n"
        "忽略与失败名单位于成果末尾，不作为拆分输入。\n\n"
        f"保存位置：\n{result.output_file}"
    )
    if result.mtime_failures:
        message += "\n\n时间未取得：\n" + "\n".join(
            path for path, _ in result.mtime_failures
        )
    return title, message


class MergePanel(BasePanel):
    config_key = "merge"

    def build(self):
        body = self.make_body()
        body.pack_configure(fill="both", expand=True)
        self.relative_base_folder = tk.StringVar(
            master=self, value=self.cfg["relative_base_folder"],
        )
        self.output_path = tk.StringVar(master=self, value=self.cfg["output_path"])
        self.output_entry = create_output_path_row(body, self)

        _, _, variables = create_page_settings(
            body, self.commits, self.cfg,
            (
                ("open_after_done", "完成后打开成果", "完成后打开本次成果文件"),
                ("normalize_text_newlines", "标准化换行", "按换行设置转换正文换行"),
                ("show_actual_path", "显示实际路径", "显示链接来源的附加实际路径"),
                ("force_overwrite", "直接覆盖已有成果", "成果已存在时直接覆盖；未勾选时选择覆盖、自动改名或取消"),
                ("record_file_mtime", "记录修改时间", "在成果中记录来源文件修改时间"),
            ),
            (
                ("exclude", "排除名单", self.open_exclude_dialog),
                ("extra", "附加文本", self.open_extra_text_dialog),
                ("markers", "容器标记", lambda: edit_output_format_settings(
                    self.root, self.commits, self.cfg, "merge",
                )),

                ("newline", "换行设置", lambda: edit_newline_settings(
                    self.root, self.commits,
                )),
            ),
        )
        for field, variable in variables.items():
            setattr(self, field, variable)

        # 底部先占位；列表取得剩余高度，执行入口不被正文挤出窗口。
        bottom = styled_frame(body, bg=THEME["bg_panel"])
        bottom.pack(side="bottom", fill="x", pady=(8, 0))
        bottom.grid_columnconfigure(0, weight=1, uniform="merge_bottom_zones")
        bottom.grid_columnconfigure(1, weight=1, uniform="merge_bottom_zones")
        buttons = styled_frame(bottom, bg=THEME["bg_panel"])
        buttons.grid(row=0, column=1, sticky="e", padx=(10, 0))
        styled_button(
            buttons, "填写规则", lambda: show_source_list_help(self.root), width=9,
        ).pack(side="left", padx=(0, 8))
        from ui.widgets import add_tooltip

        for label, mode, color in (
            ("合并文件夹", "directory", "#6b7c83"),
            ("合并文件", "list", "#718071"),
            ("组合合并", "combined", "#927c63"),
        ):
            button = styled_button(
                buttons, label,
                lambda selected=mode: self.execute_merge(selected), width=11,
            )
            button.configure(
                bg=color, fg=THEME["fg_on_dark"],
                activebackground=color, activeforeground=THEME["fg_on_dark"],
            )
            button._normal_bg = color
            button._normal_fg = THEME["fg_on_dark"]
            add_tooltip(button, {
                "directory": "只使用文件夹列表，递归合并其中的文件，并应用排除名单。",
                "list": "只使用文件列表，不使用文件夹来源的排除名单。",
                "combined": "先处理文件夹列表，再补充文件列表；明确选中的文件不受文件夹来源排除规则影响。",
            }[mode])
            button.pack(side="left", padx=(4, 0))
        base = styled_frame(bottom, bg=THEME["bg_panel"])
        base.grid(row=0, column=0, sticky="ew")
        create_entry_row(
            base, "相对路径基准", self.relative_base_folder,
            commits=self.commits, config=self.cfg,
            field="relative_base_folder", page=self.config_key,
            history_key="merge.relative_base_folder", label_width=11,
            value_normalizer=normalize_windows_display_path,
            browse_command=lambda: browse_folder(
                self.relative_base_folder, "选择相对规则基准", parent=self.root,
                commits=self.commits, history_key="merge.relative_base_folder",
            ),
            open_command=lambda: open_path_with_default_app(
                self.relative_base_folder.get(), self.root,
            ),
            tooltip_text="只解释两份列表的相对规则，不自动加入合并；绝对规则可留空。",
        )

        lists = tk.PanedWindow(
            body, orient="horizontal", bg=THEME["bg_panel"],
            sashwidth=6, sashrelief="flat", bd=0,
        )
        lists.pack(fill="both", expand=True)
        left = styled_frame(lists, bg=THEME["bg_panel"])
        right = styled_frame(lists, bg=THEME["bg_panel"])
        lists.add(left, minsize=280, stretch="always")
        lists.add(right, minsize=280, stretch="always")
        self.directory_editor = SourceListEditor(
            left, self, field="directory_list_text", title="文件夹列表",
            object_kind="directory", favorite_key="merge_directory_list",
            wrap_key="merge.directory_list",
        )
        self.file_editor = SourceListEditor(
            right, self, field="demand_file_list_text", title="文件列表",
            object_kind="file", favorite_key="demand_file_list",
            wrap_key="merge.demand_file_list",
        )

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
        header = validate_required_path(cfg["code_header_line"], "开始标记")
        footer = validate_required_path(cfg["code_footer_line"], "结束标记")
        output = resolve_output_file_conflict(
            resolve_output_path(cfg["output_path"]),
            cfg["force_overwrite"], self.root,
        )
        return output, header, footer

    def execute_merge(self, mode):
        if self.commits.operation_depth:
            return
        with self.commits.operation(), locked_page(self):
            return self._execute_merge(mode)

    def _execute_merge(self, mode):
        self.commits.manager.accept(self.cfg, {"merge_mode": mode})
        fields = {
            "merge_mode", "relative_base_folder", "output_path",
            "preamble_text", "ending_text",
            "code_header_line", "code_footer_line",
            "open_after_done", "force_overwrite", "normalize_text_newlines",
            "record_file_mtime", "show_actual_path",
        }
        if mode in ("directory", "combined"):
            fields |= {"directory_list_text", "exclude_folders", "exclude_files"}
        if mode in ("list", "combined"):
            fields.add("demand_file_list_text")
        cfg = self.prepare(fields)
        if cfg is None:
            return
        try:
            exclusions = None
            if mode in ("directory", "combined"):
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
                ) if cfg["normalize_text_newlines"] else None
            )
            output, header, footer = self.resolve_output_and_markers(cfg)
            if output is None:
                return
            from ui.source_scope import make_source_node_selector

            labels = {
                "directory": "合并文件夹", "list": "合并文件", "combined": "组合合并",
            }
            mode_label = labels[mode]
            self.set_status(f"正在合并：{mode_label}...")
            self.log(f"用户启动：合并（{mode_label}）")
            self.commits.metrics["business_starts"] += 1
            result = merge_files(
                mode=mode,
                relative_base_folder=cfg["relative_base_folder"],
                directory_list_text=cfg.get("directory_list_text", ""),
                list_text=cfg.get("demand_file_list_text", ""),
                output_file=output, exclusions=exclusions,
                preamble_text=cfg["preamble_text"], ending_text=cfg["ending_text"],
                code_header_line=header, code_footer_line=footer,
                log_func=self.log,
                normalize_text_newlines=cfg["normalize_text_newlines"],
                record_file_mtime=cfg["record_file_mtime"],
                show_actual_path=cfg["show_actual_path"],
                output_rules=output_rules,
                select_source_links=make_source_node_selector(self.root, self.commits),
                authorize_merge_scope=lambda title, description:
                safe_ask_risk_confirm(
                    title, description, self.root, danger=True,
                ),
            )
        except (OSError, ValueError) as error:
            self.handle_error("合并", error)
            return
        title, message = merge_completion_message(result, mode_label)
        self.set_status(title)
        safe_show_info(title, message, self.root)
        if cfg["open_after_done"]:
            open_path_with_default_app(result.output_file, self.root)