# -*- coding: utf-8 -*-

"""唯一主动备份还原页面；恢复、保护和回滚继续由共用服务负责。"""

import tkinter as tk
from pathlib import Path

from core.config import SaveStatus
from core.constants import THEME
from core.path_validation import normalize_windows_display_path, strip_outer_quotes
from core.recovery_paths import checked_chain
from core.time_utils import current_timestamp_text
from panels.base_panel import BasePanel
from ui.backup_restore import (
    open_last_backup_folder, open_restore_source, run_backup_restore,
)
from ui.dialogs import create_managed_text_box
from ui.message_dialogs import safe_show_error
from ui.operation_lock import locked_page
from ui.text_editor import get_text_value, replace_text_preserve_view
from ui.theme import styled_button, styled_frame
from ui.widgets import (
    browse_folder, create_entry_row, make_checkbutton,
    open_path_with_default_app,
)


def source_parent_for_protection(value):
    """仅有效绝对任务目录触发跟随；不读取清单或执行恢复。"""
    selected = strip_outer_quotes(value)
    if not selected:
        return None
    path = Path(selected)
    if not path.is_absolute():
        return None
    target, chain = checked_chain(path, include_observation=True)
    if chain[-1][1] != "directory":
        return None
    return str(target.parent)


class BackupRestorePanel(BasePanel):
    config_key = "backup_restore"

    def build(self):
        body = self.make_body()
        self.last_backup_root = ""
        self._followed_source = self.cfg["restore_source_dir"]
        self.restore_source_dir = tk.StringVar(value=self.cfg["restore_source_dir"])
        self.backup_dir = tk.StringVar(value=self.cfg["backup_dir"])
        self.restore_source_entry = create_entry_row(
            body, "备份来源", self.restore_source_dir,
            commits=self.commits, config=self.cfg,
            field="restore_source_dir", page=self.config_key,
            history_key="backup_restore.restore_source_dir",
            value_normalizer=normalize_windows_display_path,
            browse_command=lambda: browse_folder(
                self.restore_source_dir,
                "选择包含 recovery.json 和 backup.complete 的任务目录",
                parent=self.root,
            ),
            open_command=lambda: open_restore_source(self),
            tooltip_text="选择完整任务目录，不是files材料目录。恢复位置由清单决定。",
            on_accepted=self.follow_source,
        )
        self.backup_dir_entry = create_entry_row(
            body, "还原保护目录", self.backup_dir,
            commits=self.commits, config=self.cfg,
            field="backup_dir", page=self.config_key,
            history_key="backup_restore.backup_dir",
            value_normalizer=normalize_windows_display_path,
            browse_command=lambda: browse_folder(
                self.backup_dir, "选择还原前保护任务的保存目录",
                parent=self.root,
            ),
            open_command=lambda: open_path_with_default_app(
                self.backup_dir.get(), self.root,
            ),
            tooltip_text="还原前在这里新建独立保护任务，不覆盖原备份。",
        )
        # 浏览、完成输入和历史回填均经过字段接受回调。
        # 连续输入不触发联动；窗口隐藏也不影响已经接受的配置事实。

        tk.Label(
            body,
            text="选择新的有效备份来源后，还原保护目录自动设为其上一级；"
                 "可以手动修改，再次改变来源时重新跟随。\n"
                 "仅恢复本备份记录的文件、链接和目录树，不回退整个项目。"
                 "范围内的后续更改可能被覆盖或移除，还原前将先行备份。",
            bg=THEME["bg_panel"], fg=THEME["fg_label"],
            font=THEME["font_main"], anchor="w", justify="left",
            wraplength=1000,
        ).pack(fill="x", pady=(8, 6))

        options = styled_frame(body, bg=THEME["bg_panel"])
        options.pack(fill="x", pady=(4, 6))
        for field, label in (
            ("keep_restore_source_path", "还原成功后保留来源路径"),
            ("open_backup_after_done", "完成后打开本次保护备份"),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            make_checkbutton(
                options, label, variable, commits=self.commits,
                config=self.cfg, field=field,
            ).pack(side="left", padx=(0, 16))

        self.result_text = create_managed_text_box(
            body, "预演 / 还原结果", self.cfg["last_result_text"],
            height=22, mono=True, readonly=True, commits=self.commits,
            enable_favorites=False, enable_copy=True,
            wrap_config_key="backup_restore.result_text",
            on_content_changed=self.accept_result_control,
        )
        actions = styled_frame(body, bg=THEME["bg_panel"])
        actions.pack(fill="x", pady=(12, 0))
        styled_button(
            actions, "打开本次保护", lambda: open_last_backup_folder(self),
            width=14,
        ).pack(side="left")
        styled_button(
            actions, "预演并还原", lambda: self.run(True),
            width=14, danger=True,
        ).pack(side="right", padx=(8, 0))
        styled_button(
            actions, "仅预演", lambda: self.run(False),
            width=12, accent=True,
        ).pack(side="right")

    def follow_source(self):
        source = self.cfg["restore_source_dir"]
        if source == self._followed_source:
            return
        try:
            directory = source_parent_for_protection(source)
        except (OSError, ValueError) as error:
            # 来源故障不改保护目录；正式预演仍会精确拒绝。
            self.log(f"备份来源尚不能用于保护目录跟随：{error}")
            return
        if directory is None:
            return
        self.backup_dir.set(directory)
        binding = self.backup_dir_entry._config_binding
        # 跟随先统一接受，外层来源提交会完成同一配置保存。
        self.commits.accept_editors((binding,))
        self._followed_source = source

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

    def record_backup_restore_result(self, text, updates, outcome):
        section = (
            "=" * 60 + "\n"
            f"【{current_timestamp_text()}｜{outcome}】\n"
            + "=" * 60 + "\n" + text
        )
        history = self.cfg["last_result_text"]
        final = self.display_result(history + "\n\n" + section if history else section)
        if "restore_source_dir" in updates:
            self.restore_source_dir.set(updates["restore_source_dir"])
            self.restore_source_entry._config_binding.dirty = False
            self._followed_source = updates["restore_source_dir"]
        self.commits.manager.accept(
            self.cfg, {"last_result_text": final, **updates},
        )
        saved = self.commits.manager.save(include_session=True)
        if saved.status is SaveStatus.FAILED:
            safe_show_error(
                "结果未持久化",
                outcome + "。\n\n" + saved.failure_description
                + "\n保存重试不会重新还原。",
                self.root,
            )
        return saved

    def run(self, execute):
        if self.commits.operation_depth:
            return
        invalid_entry = None
        try:
            with self.commits.operation(), locked_page(self):
                # 来源先接受，使直接联动先于保护目录最终快照。
                source_binding = self.restore_source_entry._config_binding
                self.commits.accept_editors((source_binding,))
                if source_binding.dirty:
                    return
                cfg = self.prepare({
                    "restore_source_dir", "backup_dir",
                    "keep_restore_source_path", "open_backup_after_done",
                })
                if cfg is None:
                    return
                for field, label, entry in (
                    ("restore_source_dir", "备份来源", self.restore_source_entry),
                    ("backup_dir", "还原保护目录", self.backup_dir_entry),
                ):
                    value = strip_outer_quotes(cfg[field])
                    if not value or not Path(value).is_absolute():
                        invalid_entry = entry
                        self.set_status(f"请填写明确绝对路径：{label}")
                        safe_show_error(
                            "参数未填写完整",
                            f"{label}必须是明确的绝对路径。",
                            self.root,
                        )
                        return
                return run_backup_restore(self, cfg, execute=execute)
        finally:
            # 同步锁已退出，控件已恢复；无需将焦点动作留到未来事件循环。
            if (
                invalid_entry is not None
                and not getattr(self.root, "_zipatch_closed", False)
                and invalid_entry.winfo_exists()
                and str(invalid_entry.cget("state")) == "normal"
            ):
                invalid_entry.focus_set()