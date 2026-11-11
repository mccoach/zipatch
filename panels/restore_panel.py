# -*- coding: utf-8 -*-

"""持久化正文输入、固定预演确认及唯一RestoreTask消费。"""

import traceback
import tkinter as tk

from core.config import SaveStatus
from core.constants import THEME
from core.time_utils import current_timestamp_text
from ui.operation_lock import locked_page
from ui.text_editor import (
    get_text_value, replace_text_keep_undo, replace_text_preserve_view,
)
from core.path_validation import normalize_windows_display_path
from panels.base_panel import BasePanel
from services.restore_service import RestoreTask, prepare_restore
from ui.dialogs import create_managed_text_box
from ui.backup_restore import (
    open_last_backup_folder, open_restore_source, run_backup_restore,
)
from ui.message_dialogs import (
    safe_ask_risk_confirm, safe_show_error, safe_show_info,
)
from panels.base_panel import publication_failure_title
from ui.newline_settings import edit_newline_settings
from ui.text_output_settings import edit_output_format_settings, show_output_help
from ui.theme import styled_button, styled_frame
from ui.widgets import (
    browse_folder, create_entry_row, make_checkbutton,
    make_radiobutton, open_path_with_default_app,
)



def format_split_preview(plan):
    """只展示固定计划，不读取正文、访问磁盘或取得执行资格。"""
    created = [str(member.target) for member in plan.files if member.kind == "absent"]
    overwritten = [str(member.target) for member in plan.files if member.kind == "file"]
    lines = [
        "【代码拆分完整预演】",
        f"来源：{plan.source_description}",
        "位置模式：" + (
            "指定目录范围内还原" if plan.mode == "directory" else "原位还原"
        ),
        f"指定目录／相对路径基准：{plan.base or '未使用'}",
        f"待写 {len(plan.files)} 个；跳过 {len(plan.skipped)} 个。",
        f"主动忽略 {plan.ignored_count} 个；"
        f"来源被动失败 {plan.source_failure_count} 个。",
        "",
        "全部正文已解析、完成适用换行转换并编码。",
        "本预演未创建工作目录、发布文件或保存正式备份材料。",
        "执行消费本次固定任务；必要新边界复核保持。",
        "关闭、取消或执行尝试后，本任务不能再次执行。",
        "源码只来自本次已接受的输入框正文；导入文件后来变化与任务无关。",
        "正文只解析、转换和编码一次；执行逐文件消费固定内存成果。",
        "执行开始前复核全部固定目标一次，之后只复核当前文件及其必要父目录。",
        "已完成文件不在每个后续动作中反复读取；不提供外部并发监控。",
    ]
    if plan.mode == "directory":
        lines.append(
            "指定目录模式：完整盘符绝对路径映射到指定目录下的"
            "盘符字母子目录，保留其后全部层级；相对路径直接拼接指定目录。"
        )
    else:
        lines.append("原位模式使用实际绝对位置，可能跨目录或盘符。")
    if plan.backup_enabled:
        lines.extend((
            "",
            "自动备份已开启：每个动作必要原状态及started依据"
            "保存成功之后，才开始该动作工作。",
            f"绝对备份目录：{plan.backup_dir}",
            "恢复依据使用严格version 4；材料保留原名及目录结构。",
            "指定目录模式以指定目录为基准；原位模式按盘符分组，"
            "各组去掉共同父目录，不压平不同位置的同名文件。",
            "必要保护对象：",
        ))
        lines.extend(
            f"- {member.target}；原类型={member.original_type}"
            for member in plan.projection.objects
        )
        if not plan.projection.objects:
            lines.append("无待写动作，不生成任务材料。")
    else:
        lines.extend((
            "",
            "自动备份已关闭，不保存覆盖前状态。",
            "覆盖已有文件可能永久丢失当前内容，请核对完整范围。",
        ))
    for title, paths in (
        ("新增文件", created),
        ("覆盖已有文件", overwritten),
        ("必要创建目录", list(map(str, plan.missing_directories))),
    ):
        lines.extend(("", f"{title}：{len(paths)} 个"))
        lines.extend("- " + path for path in paths)
    if plan.skipped:
        lines.extend(("", "固定跳过："))
        lines.extend(f"- {path}：{reason}" for path, reason in plan.skipped)
    if plan.log_path is not None:
        lines.extend((
            "", f"计划拆分跳过日志：{plan.log_path}",
            "辅助日志在工作正常结束后发布，不取得恢复资格。",
        ))
    lines.extend((
        "",
        "任一失败停止后续；前序文件、目录及材料不自动撤销。",
        "普通目标完整暂存后替换；硬链接目标通过原对象写回。",
        "不保证整批事务、全部竞态或断电恢复。",
        "确认和执行期间请避免外部修改。",
    ))
    return "\n".join(lines)


class RestorePanel(BasePanel):
    config_key = "restore"

    def build(self):
        body = self.make_body()
        for field, label in (
            ("target_folder", "指定目录"),
            ("backup_dir", "备份目录"),
        ):
            variable = tk.StringVar(value=self.cfg[field])
            setattr(self, field, variable)
            entry = create_entry_row(
                body, label, variable, commits=self.commits, config=self.cfg,
                field=field, page=self.config_key,
                history_key=f"restore.{field}",
                value_normalizer=normalize_windows_display_path,
                browse_command=lambda current=variable: browse_folder(
                    current, parent=self.root,
                ),
                open_command=lambda current=variable: open_path_with_default_app(
                    current.get(), self.root,
                ),
                tooltip_text=(
                    "指定目录模式：写入范围及相对路径基准，可以不存在。"
                    "原位模式：只为相对路径提供绝对基准；"
                    "全为绝对目标时可留空。"
                    if field == "target_folder" else
                    "启用备份时必须填写有效绝对位置；关闭时不使用该设置。"
                ),
            )

            if field == "target_folder":
                self.target_entry = entry
            else:
                self.backup_dir_entry = entry

        self.execution_mode = tk.StringVar(value=self.cfg["execution_mode"])
        self.mode_row = styled_frame(body, bg=THEME["bg_panel"])
        self.mode_row.pack(fill="x", pady=(10, 0))
        tk.Label(
            self.mode_row, text="执行模式", width=10, anchor="w",
            bg=THEME["bg_panel"], fg=THEME["fg_label"], font=THEME["font_main"],
        ).pack(side="left", padx=(0, 8))
        for label, value, danger in (
            ("执行拆分", "split", False),
            ("备份还原", "restore", True),
        ):
            make_radiobutton(
                self.mode_row, label, self.execution_mode, value,
                commits=self.commits, config=self.cfg, field="execution_mode",
                command=self.refresh_mode_ui, danger=danger,
            ).pack(side="left", padx=(0, 8))
        self.mode_hint = tk.Label(
            self.mode_row, text="", bg=THEME["bg_panel"],
            fg=THEME["danger"], font=THEME["font_title"],
        )
        self.mode_hint.pack(side="right", padx=(8, 0))
        self.split_checks = styled_frame(self.mode_row, bg=THEME["bg_panel"])
        self.split_checks.pack(side="left")

        # 位置和已有文件政策也归执行模式行，按模式显示所属选项。
        options = styled_frame(self.mode_row, bg=THEME["bg_panel"])
        self.split_options = options
        self.target_mode = tk.StringVar(value=self.cfg["target_mode"])
        self.existing_file_policy = tk.StringVar(
            value=self.cfg["existing_file_policy"],
        )
        for label, variable, field, choices in (
            (
                "还原位置：", self.target_mode, "target_mode",
                (("指定目录", "directory"), ("原位还原", "in_place")),
            ),
            (
                "已有文件：", self.existing_file_policy, "existing_file_policy",
                (("覆盖", "overwrite"), ("跳过", "skip")),
            ),
        ):
            group = styled_frame(options, bg=THEME["bg_panel"])
            group.pack(side="left", padx=(0, 16))
            tk.Label(
                group, text=label, bg=THEME["bg_panel"],
                fg=THEME["fg_label"], font=THEME["font_main"],
            ).pack(side="left")
            for text, value in choices:
                make_radiobutton(
                    group, text, variable, value,
                    commits=self.commits, config=self.cfg, field=field,
                ).pack(side="left", padx=(0, 6))

        for field, label in (
            ("backup_enabled", "自动备份"),
            ("normalize_text_newlines", "标准化文本换行"),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            make_checkbutton(
                self.split_checks, label, variable, commits=self.commits,
                config=self.cfg, field=field,
            ).pack(side="left", padx=(0, 6))

        area = styled_frame(body, bg=THEME["bg_panel"])
        area.pack(fill="both", expand=True, pady=(10, 0))
        self.source_area = area
        area.grid_columnconfigure(0, weight=1, uniform="split_text_columns")
        area.grid_columnconfigure(1, weight=1, uniform="split_text_columns")
        area.grid_rowconfigure(0, weight=1)
        left = styled_frame(area)
        right = styled_frame(area)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.source_text = create_managed_text_box(
            left, "拆分源码",
            self.cfg["source_text"], height=22, mono=True,
            show_statistics=True,
            commits=self.commits, config=self.cfg,
            field="source_text", page=self.config_key,
            enable_favorites=False, enable_import_text=True, wrap="none",
            preserve_trailing_newlines=True, preserve_import_newlines=True,
            wrap_config_key="restore.source_text",
        )
        self.result_text = create_managed_text_box(
            right, "预演 / 执行结果", self.cfg["last_result_text"],
            height=22, mono=True, readonly=True, commits=self.commits,
            enable_favorites=False, wrap="none",
            wrap_config_key="restore.result_text",
            on_content_changed=self.accept_result_control,
        )

        self.last_backup_root = ""
        self.restore_source_dir = tk.StringVar(value=self.cfg["restore_source_dir"])
        self.restore_source_entry = create_entry_row(
            body, "备份来源", self.restore_source_dir,
            commits=self.commits, config=self.cfg,
            field="restore_source_dir", page=self.config_key,
            history_key="restore.restore_source_dir",
            value_normalizer=normalize_windows_display_path,
            browse_command=lambda: browse_folder(
                self.restore_source_dir,
                "选择包含 recovery.json 的备份任务目录",
                parent=self.root,
            ),
            open_command=lambda: open_restore_source(self),
            tooltip_text=(
                "填写完整绝对任务目录；仅支持严格version 4 recovery.json。"
                "可以恢复修改包、拆分或还原前保护产生的材料。"
                "恢复目标使用清单绝对位置，不使用拆分指定目录。"
            ),
        )

        self.recovery_options = styled_frame(
            self.mode_row, bg=THEME["bg_panel"],
        )
        self.recovery_options.pack(side="left")
        for field, label, tip in (
            (
                "restore_backup_enabled", "还原前保护",
                "只保护本次恢复最终实际改变的当前状态；"
                "使用上方绝对备份目录，不受拆分自动备份开关控制。",
            ),
            (
                "keep_restore_source_path", "保留备份来源路径",
                "未勾选时，仅完整原状态恢复成功后清空来源字段；"
                "部分保留、取消或失败不清空，不删除备份文件。",
            ),
            (
                "open_backup_after_done", "恢复完成后打开本次保护备份",
                "仅打开本次实际生成的还原前保护任务；"
                "没有生成时不自动打开来源或历史目录。",
            ),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            make_checkbutton(
                self.recovery_options, label, variable,
                commits=self.commits, config=self.cfg, field=field,
                tooltip_text=tip,
            ).pack(side="left", padx=(0, 24))

        self.buttons = styled_frame(body, bg=THEME["bg_panel"])
        self.buttons.pack(fill="x", pady=(10, 0))
        styled_button(
            self.buttons, "一键清空", self.clear_all, width=10,
        ).pack(side="left", padx=(0, 8))
        self.format_settings_button = styled_button(
            self.buttons, "标记与占位",
            lambda: edit_output_format_settings(
                self.root, self.commits, self.cfg, "restore",
            ), width=10,
        )
        self.format_settings_button.pack(side="left", padx=(0, 8))
        self.newline_settings_button = styled_button(
            self.buttons, "换行设置", self.open_newline_settings, width=10,
        )
        self.newline_settings_button.pack(side="left", padx=(0, 8))
        self.output_help_button = styled_button(
            self.buttons, "正文规则",
            lambda: show_output_help(self.root), width=10,
        )
        self.output_help_button.pack(side="left", padx=(0, 12))
        self.open_after_done = tk.BooleanVar(value=self.cfg["open_after_done"])
        self.open_after_done_check = make_checkbutton(
            self.split_checks, "完成后打开成果目录", self.open_after_done,
            commits=self.commits, config=self.cfg, field="open_after_done",
            tooltip_text=(
                "只在指定目录拆分成功后打开成果目录；"
                "原位拆分不自动打开相对路径基准目录。"
            ),
        )
        self.open_after_done_check.pack(side="left", padx=(0, 6))
        self.open_backup_button = styled_button(
            self.buttons, "打开本次备份",
            lambda: open_last_backup_folder(self), width=14,
        )
        self.apply_button = styled_button(
            self.buttons, "预演并执行", self.execute, width=14, danger=True,
        )
        self.apply_button.pack(side="right", padx=(8, 0))
        self.preview_button = styled_button(
            self.buttons, "独立预演", self.preview, width=14, accent=True,
        )
        self.preview_button.pack(side="right")
        self.refresh_mode_ui()

    def refresh_mode_ui(self):
        """模式只决定可见入口；隐藏正文保留，不改变已接受设置。"""
        restore = self.execution_mode.get() == "restore"
        for row, visible in (
            (self.target_entry._row_frame, not restore),
            (self.restore_source_entry._row_frame, restore),
        ):
            if visible:
                if not row.winfo_manager():
                    row.pack(fill="x", pady=4, before=self.mode_row)
            else:
                row.pack_forget()

        # 备份目录两模式都使用；始终处于路径区，不隐藏保护位置。
        self.backup_dir_entry._row_frame.pack(
            fill="x", pady=4, before=self.mode_row,
        )
        self.split_options.pack_forget()
        self.source_area.pack_forget()
        self.split_checks.pack_forget()
        self.recovery_options.pack_forget()
        if restore:
            self.recovery_options.pack(side="left")
            self.source_text.configure(state="disabled")
        else:
            self.split_checks.pack(side="left")
            self.split_options.pack(
                side="left", before=self.split_checks,
            )
            self.source_text.configure(state="normal")
        # 两模式结果都留在右侧；恢复不消费左侧拆分正文。
        self.source_area.pack(
            fill="both", expand=True, pady=(10, 0), before=self.buttons,
        )

        for widget in (
            self.open_backup_button, self.newline_settings_button,
            self.format_settings_button, self.output_help_button,
        ):
            widget.pack_forget()
        # 一键清空保持最左；之后按统一顺序重新排列。
        self.open_backup_button.pack(side="left", padx=(0, 8))
        if not restore:
            for widget in (
                self.newline_settings_button, self.format_settings_button,
                self.output_help_button,
            ):
                widget.pack(side="left", padx=(0, 8))

        self.mode_hint.configure(
            text="当前为备份还原模式，请谨慎操作" if restore else "",
        )
        self.preview_button.configure(
            text="预演还原" if restore else "独立预演",
        )
        color = THEME["danger"] if restore else THEME["warning"]
        self.apply_button.configure(
            text="预演并还原" if restore else "预演并执行",
            bg=color, fg=THEME["fg_on_dark"], activebackground=color,
        )
        self.apply_button._normal_bg = color
        self.apply_button._normal_fg = THEME["fg_on_dark"]

    def run_backup_recovery(self, execute):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            cfg = self.prepare({
                "restore_source_dir", "keep_restore_source_path",
                "restore_backup_enabled", "backup_dir",
                "open_backup_after_done",
            })
            if cfg is not None:
                return run_backup_restore(self, cfg, execute=execute)

    def display_result(self, text):
        self.result_text.configure(state="normal")
        try:
            replace_text_preserve_view(self.result_text, text)
        finally:
            self.result_text.configure(state="disabled")
        return text

    def accept_result_control(self):
        if self.commits.operation_depth:
            return False
        return self.commits.submit_values(
            self.cfg, {"last_result_text": get_text_value(self.result_text)},
            self.root,
        ).request_satisfied

    def clear_all(self):
        """只清空左右正文，输入可撤销；恢复模式的只读状态保持。"""
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            previous = str(self.source_text.cget("state"))
            self.source_text.configure(state="normal")
            try:
                replace_text_keep_undo(self.source_text, "")
                self.source_text._config_binding.commit("")
            finally:
                self.source_text.configure(state=previous)
            self.display_result("")
            self.commits.manager.accept(self.cfg, {"last_result_text": ""})
            self.commits.finish(self.root, include_session=True)
            self.log("代码拆分还原：已一键清空输入和结果")

    def result_section(self, title, content, cfg):
        mode = (
            "备份还原" if cfg["execution_mode"] == "restore" else "执行拆分"
        )
        return (
            "=" * 60 + "\n"
            f"【{current_timestamp_text()}｜{title}｜{mode}】\n"
            + "=" * 60 + "\n" + content
        )

    def finish_result(self, text, updates=None, outcome="预演已完成"):
        """配置拥有结果历史；重试保存不再次追加或执行工作。"""
        history = self.cfg["last_result_text"]
        final = self.display_result(
            history + "\n\n" + text if history else text,
        )
        values = {"last_result_text": final}
        if updates:
            values.update(updates)
        self.commits.manager.accept(self.cfg, values)
        saved = self.commits.manager.save(include_session=True)
        if saved.status is SaveStatus.FAILED:
            safe_show_error(
                "结果未持久化",
                f"{outcome}。\n\n" + saved.failure_description
                + "\n\n完整结果和关联设置仍保留在已接受内存中。"
                "\n保存重试不会再次追加记录或重新执行任务。",
                self.root,
            )
        return saved

    def record_backup_restore_result(self, text, updates, outcome):
        """共享恢复显示于右侧，来源清空仍消费正式恢复成功结论。"""
        if "restore_source_dir" in updates:
            self.restore_source_dir.set(updates["restore_source_dir"])
            self.restore_source_entry._config_binding.dirty = False
        return self.finish_result(
            self.result_section(
                outcome, text, {"execution_mode": "restore"},
            ),
            updates, outcome=outcome,
        )

    def open_newline_settings(self):
        if edit_newline_settings(self.root, self.commits):
            self.log("换行设置已保存")

    def prepare_parameters(self):
        return self.prepare({
            "source_text", "target_folder", "target_mode", "code_header_line",
            "code_footer_line", "existing_file_policy", "open_after_done",
            "normalize_text_newlines", "ignore_text", "failure_text",
            "backup_enabled", "backup_dir",
        })

    def prepare_task(self, cfg):
        rules = (
            self.commits.prepare_rule(
                self.config_data["settings"], "crlf_file_list", "file",
            ) if cfg["normalize_text_newlines"] else None
        )
        # prepare_parameters已经接受精确正文并完成保存屏障；
        # 本次任务直接消费该固定值，不第二次读取Text。
        plan = prepare_restore(
            rules, cfg["target_folder"],
            cfg["code_header_line"], cfg["code_footer_line"],
            cfg["existing_file_policy"],
            target_mode=cfg["target_mode"],
            normalize_text_newlines=cfg["normalize_text_newlines"],
            ignore_text=cfg["ignore_text"], failure_text=cfg["failure_text"],
            backup_enabled=cfg["backup_enabled"], backup_dir=cfg["backup_dir"],
            source_text=cfg["source_text"],
        )
        return RestoreTask(plan)

    def confirm_split_execution(self, plan):
        """完整技术报告留右侧；摘要只消费同一本次固定计划。"""
        created = tuple(
            str(member.target) for member in plan.files if member.kind == "absent"
        )
        overwritten = tuple(
            str(member.target) for member in plan.files if member.kind == "file"
        )
        high_risk = not plan.backup_enabled and bool(overwritten)
        lines = [
            "即将执行本次固定拆分任务。",
            "",
            f"文件块总数：{len(plan.files) + len(plan.skipped)} 个。",
            f"实际待写：{len(plan.files)} 个；固定跳过：{len(plan.skipped)} 个。",
            f"新增：{len(created)} 个；覆盖：{len(overwritten)} 个。",
            f"主动忽略：{plan.ignored_count} 个；"
            f"来源被动失败：{plan.source_failure_count} 个。",
            "还原位置：" + (
                "指定目录" if plan.mode == "directory" else "原位还原"
            ),
            f"指定目录／相对路径基准：{plan.base or '未使用'}",
            (
                f"自动备份已开启，计划任务目录：{plan.backup_root}"
                if plan.backup_enabled else "自动备份已关闭。"
            ),
            "完整正文已准备；确认后直接执行同一本次固定任务。",
            "必要前提变化或任一失败时停止，不自动回滚或重试。",
        ]
        if high_risk:
            lines.extend((
                "",
                "高风险：未启用自动备份，将覆盖已有文件。",
                "当前内容可能永久丢失，工具无法自动恢复到执行前状态。",
            ))
        for title, paths in (
            ("新增文件", created), ("覆盖已有文件", overwritten),
        ):
            if paths:
                lines.extend(("", f"【{title}：{len(paths)} 个】"))
                lines.extend("- " + path for path in paths)
        if plan.skipped:
            lines.extend(("", "【固定跳过】"))
            lines.extend(
                f"- {path}：{reason}" for path, reason in plan.skipped
            )
        lines.extend((
            "",
            "完整预演和必要目录明细已显示在右侧结果框。",
            "确认和执行期间请避免外部修改。确认执行？",
        ))
        return safe_ask_risk_confirm(
            "高风险确认：未启用自动备份" if high_risk else "执行确认",
            "\n".join(lines), self.root, danger=high_risk,
        )

    def preview(self):
        return self.run(False)

    def execute(self):
        return self.run(True)

    def run(self, execute):
        if self.commits.operation_depth:
            return
        if self.execution_mode.get() == "restore":
            return self.run_backup_recovery(execute)
        return self.run_split(execute)

    def run_split(self, execute):
        if self.commits.operation_depth:
            return
        with self.commits.operation(), locked_page(self):
            task = None
            report = ""
            cfg = None
            try:
                cfg = self.prepare_parameters()
                if cfg is None:
                    return
                cfg["execution_mode"] = "split"
                self.set_status("正在完整准备拆分预演...")
                self.log(
                    "用户启动：拆分预演并执行"
                    if execute else "用户启动：拆分独立预演"
                )
                self.commits.metrics["business_starts"] += 1
                task = self.prepare_task(cfg)
                for path, reason in task.plan.skipped:
                    self.log(f"预演跳过：{path}；{reason}")
                report = self.result_section(
                    "拆分预演", format_split_preview(task.plan), cfg,
                )
                history = self.cfg["last_result_text"]
                self.display_result(
                    history + "\n\n" + report if history else report,
                )

                if not execute:
                    count = len(task.plan.files)
                    skipped = len(task.plan.skipped)
                    task.invalidate()
                    saved = self.finish_result(
                        report, outcome="独立预演已完成，任务已作废",
                    )
                    self.set_status("拆分独立预演完成，任务已作废")
                    self.log("拆分独立预演完成；未写工作文件或正式备份材料")
                    if saved.persisted:
                        safe_show_info(
                            "预演完成",
                            f"完整检查完成：待写 {count} 个，跳过 {skipped} 个。\n"
                            "请查看右侧完整预演结果。\n"
                            "本次任务已作废；执行时会重新准备并确认。",
                            self.root,
                        )
                    return

                if not task.plan.files:
                    task.invalidate()
                    saved = self.finish_result(
                        report, outcome="无待写文件，任务已作废",
                    )
                    self.set_status("无待写文件，任务已作废")
                    self.log("拆分未执行；没有创建工作目录或备份材料")
                    if saved.persisted:
                        safe_show_info(
                            "无待写文件",
                            "本次没有可执行文件，请查看右侧完整跳过原因。\n"
                            "没有创建工作目录或备份材料。",
                            self.root,
                        )
                    return

                if not self.confirm_split_execution(task.plan):
                    task.invalidate()
                    self.finish_result(
                        report, outcome="拆分已取消，任务已作废，工作目标未改变",
                    )
                    self.set_status("拆分已取消，任务已作废")
                    self.log("拆分未执行；工作目标未写入")
                    return

                self.set_status("正在执行本次固定拆分任务...")
                self.commits.metrics["business_starts"] += 1
                result = task.execute(self.log)
                self.last_backup_root = result.backup_root
                self.present_result(result, cfg, report)
            except (OSError, ValueError) as error:
                title = publication_failure_title(error) or "拆分预演未完成"
                content = str(error) + "\n\n" + traceback.format_exc()
                if cfg is not None:
                    failure = self.result_section(title, content, cfg)
                    self.finish_result(
                        report + "\n\n" + failure if report else failure,
                        outcome="流程已停止；以下保存仅针对本次结果记录",
                    )
                self.handle_error("代码拆分还原", error)
            finally:
                if task is not None:
                    task.invalidate()

    def present_result(self, result, cfg, preview_report):
        """完成反馈直接消费正式结果，报告保存失败不重复拆分。"""
        message = (
            f"还原任务完成。\n成功还原 {result.restored_count} 个文件；"
            f"跳过 {result.skipped_count} 个。\n"
            f"主动忽略 {result.ignored_count} 个；"
            f"来源被动失败 {result.source_failure_count} 个。\n"
            f"实际创建必要目录 {len(result.created_directories)} 个。"
        )
        if result.backup_root:
            message += (
                f"\n本次备份目录：\n{result.backup_root}"
                "\n严格version 4材料，保留原名及目录结构；请保留完整任务目录。"
                "\n可在本页“备份还原”或修改包页面预检和恢复。"
            )
        elif result.backup_enabled:
            message += "\n本次没有生成备份材料。"
        else:
            message += "\n自动备份已关闭，本次没有保存覆盖前状态。"
        if result.source_failure_count:
            message += "\n来源被动失败项未提供真实正文，请排查后重新合并。"
        if result.log_path:
            message += f"\n跳过日志：\n{result.log_path}"
        lines = [message, "", "【正常完成的工作文件】"]
        lines.extend("- " + str(path) for path in result.completed_files)
        lines.extend((
            "",
            "每个工作文件已在自己的正式发布结束边界完成核验。",
            "结果历史不提供再次执行资格；保存重试不会重新拆分。",
        ))
        saved = self.finish_result(
            preview_report + "\n\n"
            + self.result_section("执行结果", "\n".join(lines), cfg),
            outcome="拆分文件工作已经完成",
        )
        self.set_status("代码拆分还原完成")
        if saved.persisted:
            safe_show_info("还原完成", message, self.root)
        if (
            cfg["open_after_done"] and cfg["target_mode"] == "directory"
            and result.target_folder
        ):
            open_path_with_default_app(result.target_folder, self.root)