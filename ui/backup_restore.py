# -*- coding: utf-8 -*-

"""两页面共享恢复交互协调；不实现文件恢复或推导工作范围。"""

import traceback
import tkinter as tk

from pathlib import Path

from core.path_validation import strip_outer_quotes
from core.recovery_paths import checked_chain
from panels.base_panel import publication_failure_title

from services.patch_service import (
    apply_backup_restore, invalidate_preview, preview_backup_restore,
)
from ui.dialogs import create_managed_text_box
from ui.message_dialogs import safe_show_error, safe_show_info
from ui.operation_lock import locked_page
from ui.recovery_selection import select_backup_restore
from ui.theme import styled_button, styled_frame
from ui.widgets import open_path_with_default_app
from ui.window_manager import (
    center_window, register_popup, take_modal_control, unregister_popup,
)


def resolve_restore_source(config):
    """两页使用明确绝对来源，不借执行模式的根或历史报告定位。"""
    source = strip_outer_quotes(config["restore_source_dir"])
    if not source:
        raise ValueError("请填写包含 recovery.json 的备份任务目录")
    path = Path(source)
    if not path.is_absolute():
        raise ValueError("备份来源必须是明确的绝对路径，不使用项目根目录")
    target, chain = checked_chain(path, include_observation=True)
    if chain[-1][1] != "directory":
        raise ValueError(f"备份来源不是既有普通任务目录：{target}")
    return target


def resolve_restore_backup_dir(config):
    """还原前保护的保存位置；准备与打开共用，不决定恢复目标。"""
    selected = strip_outer_quotes(config["backup_dir"])
    if not selected or not Path(selected).is_absolute():
        raise ValueError(
            "还原前保护的备份目录必须是明确的绝对路径；"
            "请填写完整路径或通过浏览选择，不使用项目根目录"
        )
    target, chain = checked_chain(selected, include_observation=True)
    if chain[-1][1] not in ("absent", "directory"):
        raise ValueError(f"还原前保护位置不是普通目录或可创建的缺失目录：{target}")
    return target


def open_restore_source(panel):
    try:
        source = resolve_restore_source(panel.cfg)
    except (OSError, ValueError) as error:
        safe_show_error("打开备份来源失败", str(error), panel.root)
        return
    open_path_with_default_app(source, panel.root)


def open_last_backup_folder(panel):
    """只消费本会话实际任务结果，不解析持久化报告文字。"""
    if not panel.last_backup_root:
        safe_show_error(
            "打开失败", "本会话尚未生成可打开的备份任务目录。", panel.root,
        )
        return
    open_path_with_default_app(panel.last_backup_root, panel.root)



def show_restore_preview(panel, preview):
    """独立预检只读展示；关闭即消费该任务，不保留可执行缓存。"""
    dialog = None

    def close():
        invalidate_preview(preview)
        unregister_popup(dialog)
        dialog.destroy()
        return True

    try:
        dialog = tk.Toplevel(panel.root)
        dialog.title("备份恢复独立预检")
        dialog.transient(panel.root)
        take_modal_control(dialog)
        center_window(dialog, 1000, 760)
        bottom = styled_frame(dialog)
        bottom.pack(side="bottom", fill="x", padx=14, pady=12)
        styled_button(bottom, "关闭预检", close, width=14).pack(side="right")
        body = styled_frame(dialog)
        body.pack(fill="both", expand=True, padx=14, pady=(12, 0))
        create_managed_text_box(
            body, "正式依据预检（关闭后任务作废）",
            preview.preview_text, height=28, mono=True, readonly=True,
            commits=panel.commits, enable_favorites=False, enable_clear=False,
            enable_wrap_toggle=False,
        )
        register_popup(dialog, close, exit_blocker=True)
        panel.root.wait_window(dialog)
    finally:
        invalidate_preview(preview)
        if dialog is not None and dialog.winfo_exists():
            unregister_popup(dialog)
            dialog.destroy()


def _prepare_preview(config):
    source = resolve_restore_source(config)
    backup_dir = (
        str(resolve_restore_backup_dir(config))
        if config["restore_backup_enabled"] else config["backup_dir"]
    )
    # 两页恢复均不依赖执行模式的项目根或拆分指定目录。
    # 正式服务继续检查清单绝对目标、文件系统根限制及来源隔离。
    return preview_backup_restore(
        project_root=None, restore_source_dir=str(source),
        backup_enabled=config["restore_backup_enabled"],
        backup_dir=backup_dir,
    )


def _remember_created_backup(panel, executor):
    protection = executor.protection
    if protection is not None and protection.directory_results:
        panel.last_backup_root = str(protection.task_root)


def run_backup_restore(panel, config, *, execute):
    """调用方已固定并保存配置、进入operation；两页面只走本正式链。

    页面提供record_backup_restore_result，分别处理已有长期报告或
    会话报告及配置更新。保存结果不提供再次执行恢复的入口。
    """
    preview = None
    with locked_page(panel):
        try:
            try:
                panel.set_status("正在预检正式备份...")
                panel.log("用户启动：备份恢复并执行" if execute else "用户启动：备份恢复独立预检")
                panel.commits.metrics["business_starts"] += 1
                preview = _prepare_preview(config)
                if not execute:
                    show_restore_preview(panel, preview)
                    panel.record_backup_restore_result(
                        preview.preview_text, {},
                        "恢复独立预检已结束，任务已作废",
                    )
                    panel.set_status("恢复独立预检已结束，任务已作废")
                    return

                accepted = select_backup_restore(
                    panel.root, preview,
                    keep_source_path=config["keep_restore_source_path"],
                )
                if not accepted:
                    invalidate_preview(preview)
                    panel.record_backup_restore_result(
                        preview.preview_text, {},
                        "恢复已取消，任务已作废，工作目标未改变",
                    )
                    panel.set_status("恢复已取消，任务已作废")
                    return

                panel.set_status("正在执行本次正式恢复方案...")
                panel.commits.metrics["business_starts"] += 1
                result = apply_backup_restore(preview)
            except (OSError, ValueError) as error:
                if preview is not None:
                    invalidate_preview(preview)
                    _remember_created_backup(panel, preview.execution)
                title = publication_failure_title(error) or "备份恢复未完整结束"
                report = (
                    (preview.preview_text + "\n\n" if preview is not None else "")
                    + str(error) + "\n\n" + traceback.format_exc()
                )
                panel.record_backup_restore_result(report, {}, title)
                panel.set_status(title)
                panel.log(f"{title}：{error}")
                safe_show_error(title, str(error), panel.root)
                return

            _remember_created_backup(panel, preview.execution)
            updates = {}
            if (
                result.complete_original_state
                and not config["keep_restore_source_path"]
            ):
                updates["restore_source_dir"] = ""
            outcome = (
                "全部对象原状态恢复完成"
                if result.complete_original_state else
                "按恢复方案完成，含用户保留，来源仍保留"
            )
            saved = panel.record_backup_restore_result(
                preview.preview_text + "\n\n" + result.log_text,
                updates, outcome,
            )
            panel.set_status(outcome)
            panel.log(outcome)
            if saved.persisted:
                safe_show_info(
                    "恢复完成" if result.complete_original_state else "按方案完成",
                    outcome + "\n备份材料没有删除。请查看运行日志或结果报告。",
                    panel.root,
                )
            if config["open_backup_after_done"] and result.backup_root:
                open_path_with_default_app(result.backup_root, panel.root)
        finally:
            if preview is not None:
                invalidate_preview(preview)