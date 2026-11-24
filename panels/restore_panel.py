# -*- coding: utf-8 -*-

"""持久化正文输入、固定预演确认及唯一RestoreTask消费。"""

import traceback
import tkinter as tk

from core.config import SaveStatus
from core.constants import THEME
from core.time_utils import current_timestamp_text
from ui.operation_lock import locked_page
from ui.page_settings import create_page_settings
from ui.text_editor import (
    get_text_value, replace_text_keep_undo, replace_text_preserve_view,
)
from core.path_validation import normalize_windows_display_path
from panels.base_panel import BasePanel
from services.restore_service import RestoreTask, prepare_restore
from ui.dialogs import create_managed_text_box
from ui.backup_restore import open_last_backup_folder
from ui.message_dialogs import (
    safe_ask_risk_confirm, safe_show_error, safe_show_info,
)
from panels.base_panel import publication_failure_title
from ui.newline_settings import edit_newline_settings
from ui.text_output_settings import show_output_help
from ui.theme import styled_button, styled_frame
from ui.widgets import (
    browse_folder, create_entry_row,
    make_radiobutton, open_path_with_default_app,
)



def split_target_label(mode):
    """只解释界面名称，不改变配置字段或路径定位政策。"""
    return "目标文件夹" if mode == "directory" else "相对路径基准"


def format_split_preview(plan):
    """只消费本次固定类型计划，不读取磁盘或推导新的授权范围。"""
    lines = [
        "【代码拆分完整预演】",
        f"来源：{plan.source_description}",
        "位置模式：" + (
            "指定文件夹映射" if plan.mode == "directory" else "原位还原"
        ),
        f"{split_target_label(plan.mode)}：{plan.base or '未使用'}",
        f"容器块：{plan.block_count} 个。",
        f"实际工作入口：{len(plan.entries)} 个；"
        f"普通文件：{plan.file_count} 个；"
        f"涉及既有链接路线：{plan.linked_access_count} 项。",
        f"普通文件夹要求：{len(plan.directories)} 个；"
        f"固定跳过：{len(plan.skipped)} 个。",

        "",
        "普通正文只解析、转换和编码一次；结构定义不进入普通正文。",
        "文件夹记录只要求普通文件夹存在，不清空未列出的成员。",
        "不创建、删除或改指向链接；授权后沿用根、父链及末端已有链接。",
        "文件夹链接结构按名义文件夹要求处理，缺失时只创建普通文件夹。",
        "只有文件链接定义而无正文的记录明确跳过，不补读目标或生成空文件。",
        "文件覆盖／跳过政策保持；现存硬链接正文仍写回原对象。",
        "经链接访问的实际范围已在读取旧正文前取得当次授权。",
        "访问依赖不成为备份或恢复对象；不因来源定义枚举或补读引用目标。",
        "预演不创建工作文件夹、发布成果或保存正式备份材料。",
        "关闭、取消或执行尝试后任务失效。",
    ]
    if plan.mode == "directory":
        lines.append(
            "完整盘符路径映射到目标文件夹下的大写盘符字母文件夹，"
            "保留其后层级；相对路径直接拼接目标文件夹。"
        )
    else:
        lines.append("绝对路径原位定位；相对路径使用明确绝对基准。")
    lines.extend((
        "",
        "【实际工作入口】",
    ))
    for member in plan.entries:
        lines.append(
            f"- 容器位置：{member.source_path}\n"
            f"  实际位置：{member.target}；容器类型={member.entry_kind}；"
            f"原现场类型={member.kind}"
        )
    lines.extend(("", "【普通文件夹要求】"))
    lines.extend(
        f"- {member.source_path} -> {member.target}；"
        f"原类型={member.access.kind}；不清空文件夹"
        for member in plan.directories
    )
    lines.extend((
        "",
        f"本次备份路径：{plan.backup_root or '无实际工作，不生成空任务'}",
        "全部必要原状态、严格version 4清单及完成凭据成功后才开始工作。",
        "备份失败清理本次唯一任务目录；清理失败提示人工处理。",
        "【必要保护对象】",
    ))
    lines.extend(
        f"- {member.target}；原类型={member.original_type}"
        for member in plan.projection.objects
    )
    lines.extend(("", "【固定必要文件夹创建计划】"))
    lines.extend("- " + str(path) for path in plan.missing_directories)
    if plan.skipped:
        lines.extend(("", "【固定跳过】"))
        lines.extend(f"- {path}：{reason}" for path, reason in plan.skipped)
    if plan.log_path is not None:
        lines.append(f"\n辅助跳过日志：{plan.log_path}；工作结束后单独发布。")
    lines.extend((
        "",
        "任一失败停止后续，不自动回滚、不自动重试；前序成果与备份保留。",
        "工作结束边界核对正式成果及文件夹要求，不从现场增加工作范围。",
        "不保证完整元数据、原硬链接拓扑、全部竞态或整批事务。",
        "确认及执行期间请避免外部修改。",
    ))
    return "\n".join(lines)


class RestorePanel(BasePanel):
    config_key = "restore"

    def build(self):
        body = self.make_body()
        self.last_backup_root = ""
        # 模式先初始化，使首次显示直接对应已保存的模式。
        # 只提前创建界面变量，原控件组块顺序保持不变。
        self.target_mode = tk.StringVar(master=self, value=self.cfg["target_mode"])
        for field, label in (
            ("target_folder", split_target_label(self.target_mode.get())),
            ("backup_dir", "备份保存位置"),
        ):
            variable = tk.StringVar(value=self.cfg[field])
            setattr(self, field, variable)
            entry = create_entry_row(
                body, label, variable, commits=self.commits, config=self.cfg,
                field=field, page=self.config_key,
                history_key=f"restore.{field}", label_width=12,
                value_normalizer=normalize_windows_display_path,
                browse_command=lambda current=variable, selected=field:
                browse_folder(
                    current, parent=self.root, commits=self.commits,
                    history_key=f"restore.{selected}",
                ),
                open_command=lambda current=variable:
                open_path_with_default_app(current.get(), self.root),
                tooltip_text=(
                    "指定文件夹模式：作为成果保存位置。原位还原模式：只作为相对路径基准。"
                    if field == "target_folder" else
                    "拆分强制备份；填写明确的绝对路径，执行时在此新建备份任务文件夹。"
                ),
            )
            if field == "target_folder":
                self.target_entry = entry
            else:
                self.backup_dir_entry = entry

        _, specific, variables = create_page_settings(
            body, self.commits, self.cfg,
            (
                ("open_after_done", "完成后打开成果", "指定文件夹模式完成后打开成果文件夹；原位还原模式不自动打开"),
                ("normalize_text_newlines", "标准化换行", "按换行设置转换拆分正文"),
            ),
            (

                ("newline", "换行设置", self.open_newline_settings),
            ),
        )
        for field, variable in variables.items():
            setattr(self, field, variable)
        self.existing_file_policy = tk.StringVar(value=self.cfg["existing_file_policy"])
        for label, variable, field, choices in (
            ("目标位置：", self.target_mode, "target_mode",
             (("指定文件夹", "directory"), ("原位还原", "in_place"))),
            ("已有文件：", self.existing_file_policy, "existing_file_policy",
             (("覆盖", "overwrite"), ("跳过", "skip"))),
        ):
            group = styled_frame(specific, bg=THEME["bg_panel"])
            group.pack(side="left", padx=(0, 8))
            tk.Label(
                group, text=label, bg=THEME["bg_panel"],
                fg=THEME["fg_label"], font=THEME["font_main"],
            ).pack(side="left")
            for text, value in choices:
                make_radiobutton(
                    group, text, variable, value,
                    commits=self.commits, config=self.cfg, field=field,
                    command=self.refresh_target_label
                    if field == "target_mode" else None,
                ).pack(side="left", padx=(0, 3))
        area = styled_frame(body, bg=THEME["bg_panel"])
        area.pack(fill="both", expand=True, pady=(10, 0))
        area.grid_columnconfigure(0, weight=1, uniform="split_text_columns")
        area.grid_columnconfigure(1, weight=1, uniform="split_text_columns")
        area.grid_rowconfigure(0, weight=1)
        left, right = styled_frame(area), styled_frame(area)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.source_text = create_managed_text_box(
            left, "待拆分内容", self.cfg["source_text"], height=22, mono=True,
            show_statistics=True, commits=self.commits, config=self.cfg,
            field="source_text", page=self.config_key,
            enable_favorites=False, enable_import_text=True, wrap="none",
            preserve_trailing_newlines=True, preserve_import_newlines=True,
            wrap_config_key="restore.source_text",
        )
        self.result_text = create_managed_text_box(
            right, "预演 / 执行结果", self.cfg["last_result_text"],
            height=22, mono=True, readonly=True, commits=self.commits,
            enable_favorites=False, enable_copy=True, wrap="none",
            wrap_config_key="restore.result_text",
            on_content_changed=self.accept_result_control,
        )
        buttons = styled_frame(body, bg=THEME["bg_panel"])
        buttons.pack(fill="x", pady=(12, 0))
        for label, command, width in (
            ("清空输入和结果", self.clear_all, 16),
            ("打开本次备份", lambda: open_last_backup_folder(self), 14),
        ):
            styled_button(buttons, label, command, width=width).pack(
                side="left", padx=(0, 8),
            )
        styled_button(
            buttons, "预演并执行", self.execute, width=14, danger=True,
        ).pack(side="right", padx=(8, 0))
        styled_button(
            buttons, "仅预演", self.preview, width=14, accent=True,
        ).pack(side="right")
        styled_button(
            buttons, "正文规则", lambda: show_output_help(self.root), width=10,
        ).pack(side="right", padx=(0, 8))

    def refresh_target_label(self):
        """模式按钮同步刷新名称，不清空路径、不另行保存配置。"""
        self.target_entry._row_label.configure(
            text=split_target_label(self.target_mode.get()),
        )

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
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            replace_text_keep_undo(self.source_text, "")
            self.source_text._config_binding.commit("")
            self.display_result("")
            self.commits.manager.accept(self.cfg, {"last_result_text": ""})
            self.commits.finish(self.root, include_session=True)
            self.log("代码拆分还原：已一键清空输入和结果")

    def result_section(self, title, content, cfg):
        return (
            "=" * 60 + "\n"
            f"【{current_timestamp_text()}｜{title}｜执行拆分】\n"
            + "=" * 60 + "\n" + content
        )

    def finish_result(self, text, updates=None, outcome="预演已完成"):
        history = self.cfg["last_result_text"]
        final = self.display_result(history + "\n\n" + text if history else text)
        self.commits.manager.accept(
            self.cfg, {"last_result_text": final, **(updates or {})},
        )
        saved = self.commits.manager.save(include_session=True)
        if saved.status is SaveStatus.FAILED:
            safe_show_error(
                "结果未持久化",
                outcome + "。\n\n" + saved.failure_description
                + "\n保存重试不会重新拆分。",
                self.root,
            )
        return saved

    def open_newline_settings(self):
        if edit_newline_settings(self.root, self.commits):
            self.log("换行设置已保存")

    def prepare_parameters(self):
        return self.prepare({
            "source_text", "target_folder", "target_mode",
            "existing_file_policy", "open_after_done",
            "normalize_text_newlines", "backup_dir",
        })

    def prepare_task(self, cfg):
        rules = (
            self.commits.prepare_rule(
                self.config_data["settings"], "crlf_file_list", "file",
            ) if cfg["normalize_text_newlines"] else None
        )
        from ui.restore_scope import make_split_selector

        return RestoreTask(prepare_restore(
            rules, cfg["target_folder"], cfg["existing_file_policy"],
            target_mode=cfg["target_mode"],
            normalize_text_newlines=cfg["normalize_text_newlines"],

            backup_dir=cfg["backup_dir"], source_text=cfg["source_text"],
            select_split_scope=make_split_selector(self.root, self.commits),
        ))

    def confirm_split_execution(self, plan):
        return safe_ask_risk_confirm(
            "执行确认",
            f"即将处理工作入口 {len(plan.entries)} 项："
            f"普通文件 {plan.file_count}，"
            f"涉及既有链接路线 {plan.linked_access_count} 项。\n"
            f"普通文件夹要求 {len(plan.directories)}；固定跳过 {len(plan.skipped)}。\n"
            f"强制备份：{plan.backup_root}\n\n"
            "文件夹不清空成员；只创建普通实体，已有链接保持并按授权路线工作。\n"
            "全部必要备份成功后开始工作；失败停止，前序成果与备份保留。\n"
            "完整范围已显示在结果中。确认执行？",
            self.root, danger=True,
        )

    def preview(self):
        return self.run_split(False)

    def execute(self):
        return self.run_split(True)

    def run(self, execute):
        return self.run_split(execute)

    def run_split(self, execute):
        if self.commits.operation_depth:
            return
        with self.commits.operation(), locked_page(self):
            task = None
            cfg = None
            report = ""
            try:
                cfg = self.prepare_parameters()
                if cfg is None:
                    return
                self.set_status("正在完整准备拆分预演...")
                self.commits.metrics["business_starts"] += 1
                task = self.prepare_task(cfg)
                report = self.result_section(
                    "拆分预演", format_split_preview(task.plan), cfg,
                )
                history = self.cfg["last_result_text"]
                self.display_result(history + "\n\n" + report if history else report)
                if not execute:
                    task.invalidate()
                    self.finish_result(report, outcome="独立预演完成，任务已作废")
                    self.set_status("拆分独立预演完成，任务已作废")
                    safe_show_info("预演完成", "请查看完整结果；本次未执行工作修改。", self.root)
                    return
                if not task.plan.entries:
                    task._check_directories(require_present=True)
                    task.invalidate()
                    self.finish_result(report, outcome="无实际工作，文件夹要求已满足")
                    self.set_status("无实际工作，任务已作废")
                    return
                if not self.confirm_split_execution(task.plan):
                    task.invalidate()
                    self.finish_result(report, outcome="拆分已取消，工作目标未改变")
                    self.set_status("拆分已取消，任务已作废")
                    return
                self.set_status("正在执行固定拆分任务...")
                self.commits.metrics["business_starts"] += 1
                result = task.execute(self.log)
                self.last_backup_root = result.backup_root
                self.present_result(result, cfg, report)
            except (OSError, ValueError) as error:
                title = publication_failure_title(error) or "拆分预演未完成"
                if cfg is not None:
                    failure = self.result_section(
                        title, str(error) + "\n\n" + traceback.format_exc(), cfg,
                    )
                    self.finish_result(
                        report + "\n\n" + failure if report else failure,
                        outcome="流程已停止",
                    )
                self.handle_error("代码拆分还原", error)
            finally:
                if task is not None:
                    task.invalidate()

    def present_result(self, result, cfg, preview_report):
        message = (
            "拆分任务完成。\n"
            f"成功正文文件：{len(result.completed_files)} 个；未创建或移除链接。\n"
            f"普通文件夹要求已满足：{result.required_directory_count} 个。\n"
            f"实际创建文件夹：{len(result.created_directories)} 个；"
            f"固定跳过：{result.skipped_count} 个。\n"

            f"本次备份：{result.backup_root or '未生成'}"
        )
        if result.log_path:
            message += f"\n跳过日志：{result.log_path}"
        saved = self.finish_result(
            preview_report + "\n\n" + self.result_section(
                "执行结果",
                message + "\n\n【完成入口】\n"
                + "\n".join(map(str, result.completed_entries)), cfg,
            ),
            outcome="拆分工作及最终核验完成",
        )
        self.set_status("代码拆分还原完成")
        if saved.persisted:
            safe_show_info("拆分完成", message, self.root)
        if (
            cfg["open_after_done"] and cfg["target_mode"] == "directory"
            and result.target_folder
        ):
            open_path_with_default_app(result.target_folder, self.root)