# -*- coding: utf-8 -*-

import traceback
import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_ask_yes_no, safe_show_error, safe_show_info
from core.paths import open_path_with_default_app, validate_required_path
from core.text_io import get_text_value, set_text_value
from core.time_utils import current_timestamp_text
from panels.base_panel import BasePanel
from services.patch_service import (
    preview_patch,
    apply_patch,
    preview_backup_restore,
    apply_backup_restore,
)
from ui.dialogs import create_managed_text_box
from ui.theme import styled_frame, styled_button
from ui.widgets import (
    create_entry_row,
    browse_folder,
    bind_autosave,
    make_checkbutton,
    add_tooltip,
)


class PatchPanel(BasePanel):
    config_key = "patch"

    def build(self):
        body = self.make_body()

        self.cfg.pop("rollback_backup_dir", None)
        self.cfg.pop("force_rollback", None)

        self.project_root = tk.StringVar(value=self.cfg.get("project_root", ""))
        self.patch_mode = tk.StringVar(value=self.cfg.get("patch_mode", "apply"))
        self.allow_delete = tk.BooleanVar(value=self.cfg.get("allow_delete", False))
        self.allow_multi_replace_exact = tk.BooleanVar(
            value=self.cfg.get("allow_multi_replace_exact", False)
        )
        self.backup_enabled = tk.BooleanVar(value=self.cfg.get("backup_enabled", True))
        self.backup_dir = tk.StringVar(
            value=self.cfg.get("backup_dir", "99_归档/AI文件修改备份")
        )
        self.restore_source_dir = tk.StringVar(value=self.cfg.get("restore_source_dir", ""))
        self.force_restore = tk.BooleanVar(value=self.cfg.get("force_restore", False))
        self.keep_restore_source_path = tk.BooleanVar(
            value=self.cfg.get("keep_restore_source_path", False)
        )
        self.open_backup_after_done = tk.BooleanVar(value=self.cfg.get("open_backup_after_done", False))
        self.preview_has_errors = False
        self.preview_success_count = 0
        self.preview_failed_count = 0

        self.preview_payload = None
        self.preview_snapshot = None
        self.last_preview_text = ""
        self.last_backup_root = ""

        create_entry_row(
            body,
            "项目根目录",
            self.project_root,
            browse_command=lambda: browse_folder(self.project_root, title="选择项目根目录"),
            open_command=lambda: open_path_with_default_app(self.project_root.get(), self.root),
            config_data=self.config_data,
            history_key="patch.project_root",
            save_config=self.save_config,
        )

        mode_row = styled_frame(body, bg=THEME["bg_panel"])
        mode_row.pack(fill="x", pady=(10, 0))
        self.mode_row = mode_row

        tk.Label(
            mode_row,
            text="执行模式",
            width=10,
            anchor="w",
            bg=THEME["bg_panel"],
            fg=THEME["fg_label"],
            font=THEME["font_main"],
        ).pack(side="left", padx=(0, 8))

        tk.Radiobutton(
            mode_row,
            text="执行修改",
            variable=self.patch_mode,
            value="apply",
            command=self.refresh_mode_ui,
            bg=THEME["bg_panel"],
            fg=THEME["fg_label"],
            activebackground=THEME["bg_panel"],
            activeforeground=THEME["fg"],
            selectcolor=THEME["bg_input"],
            font=THEME["font_main"],
        )
        add_tooltip(
            mode_row.winfo_children()[-1],
            "执行 AI V2 修改包。建议先 Dry Run 预演，确认全部校验通过后再执行。",
        )
        mode_row.winfo_children()[-1].pack(side="left", padx=(0, 24))

        tk.Radiobutton(
            mode_row,
            text="备份还原",
            variable=self.patch_mode,
            value="restore",
            command=self.refresh_mode_ui,
            bg=THEME["bg_panel"],
            fg=THEME["danger"],
            activebackground=THEME["bg_panel"],
            activeforeground=THEME["danger"],
            selectcolor=THEME["danger_bg"],
            font=THEME["font_title"],
        ).pack(side="left")

        self.mode_hint = tk.Label(
            mode_row,
            text="",
            bg=THEME["bg_panel"],
            fg=THEME["danger"],
            font=THEME["font_title"],
        )
        self.mode_hint.pack(side="left", padx=(24, 0))

        self.backup_dir_entry = create_entry_row(
            body,
            "备份目录",
            self.backup_dir,
            browse_command=lambda: browse_folder(self.backup_dir, title="选择备份保存目录"),
            open_command=lambda: open_path_with_default_app(self.backup_dir.get(), self.root),
            config_data=self.config_data,
            history_key="patch.backup_dir",
            save_config=self.save_config,
        )

        self.restore_source_entry = create_entry_row(
            body,
            "备份来源",
            self.restore_source_dir,
            browse_command=lambda: browse_folder(self.restore_source_dir, title="选择用于还原的历史备份目录"),
            open_command=lambda: open_path_with_default_app(self.restore_source_dir.get(), self.root),
            tooltip_text="用于备份还原的历史备份目录。该目录必须包含 manifest.json。",
            config_data=self.config_data,
            history_key="patch.restore_source_dir",
            save_config=self.save_config,
        )

        option_row = styled_frame(body, bg=THEME["bg_panel"])
        option_row.pack(fill="x", pady=(10, 0))

        self.allow_delete_check = make_checkbutton(
            option_row,
            "允许删除文件（危险）",
            self.allow_delete,
            tooltip_text="仅执行修改模式使用。勾选后，修改包中的 delete_file 操作才允许执行；删除前会按自动备份设置先备份。",
        )
        self.allow_delete_check.pack(side="left", padx=(0, 24))

        self.allow_multi_replace_check = make_checkbutton(
            option_row,
            "允许多处精确替换",
            self.allow_multi_replace_exact,
            tooltip_text="仅执行修改模式使用。允许 replace_exact 按修改包声明一次替换多处完全相同的文本。",
        )
        self.allow_multi_replace_check.pack(side="left", padx=(0, 24))

        self.backup_enabled_check = make_checkbutton(
            option_row,
            "自动备份",
            self.backup_enabled,
            tooltip_text="勾选后，执行修改前会备份被修改/删除的文件；备份还原前会备份当前状态。实际备份文件是否生成和保留只由此项控制。",
        )
        self.backup_enabled_check.pack(side="left", padx=(0, 24))

        self.force_restore_check = make_checkbutton(
            option_row,
            "允许强制还原",
            self.force_restore,
            tooltip_text="仅备份还原模式使用。当前文件状态与备份记录不一致时，勾选后允许继续覆盖还原。",
        )
        self.force_restore_check.pack(side="left", padx=(0, 24))

        self.keep_restore_source_path_check = make_checkbutton(
            option_row,
            "保留备份来源路径",
            self.keep_restore_source_path,
            tooltip_text="仅备份还原模式使用。勾选后，还原完成后继续保留“备份来源”文本框中的路径；不勾选则还原成功后清空该路径，避免下次误用旧备份来源。该选项不会删除任何实际备份文件。",
        )
        self.keep_restore_source_path_check.pack(side="left", padx=(0, 24))

        self.open_backup_after_done_check = make_checkbutton(
            option_row,
            "执行完成后打开备份目录",
            self.open_backup_after_done,
            tooltip_text="勾选后，执行完成会自动打开本次新生成的备份目录。未启用自动备份时不会打开。",
        )
        self.open_backup_after_done_check.pack(side="left")

        bind_autosave(
            self.cfg,
            [
                (self.project_root, "project_root"),
                (self.patch_mode, "patch_mode"),
                (self.allow_delete, "allow_delete"),
                (self.allow_multi_replace_exact, "allow_multi_replace_exact"),
                (self.backup_enabled, "backup_enabled"),
                (self.backup_dir, "backup_dir"),
                (self.restore_source_dir, "restore_source_dir"),
                (self.force_restore, "force_restore"),
                (self.keep_restore_source_path, "keep_restore_source_path"),
                (self.open_backup_after_done, "open_backup_after_done"),
            ],
            self.save_config,
        )

        text_area = styled_frame(body, bg=THEME["bg_panel"])
        text_area.pack(fill="both", expand=True, pady=(10, 0))

        left = styled_frame(text_area, bg=THEME["bg_panel"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right = styled_frame(text_area, bg=THEME["bg_panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self.patch_text = create_managed_text_box(
            parent=left,
            label_text="粘贴 AI V2 修改包",
            initial_value=self.cfg.get("patch_text", ""),
            height=22,
            mono=True,
            config_data=self.config_data,
            favorite_key="patch_text",
            save_config_func=self.save_config,
            wrap="none",
            enable_import_text=True,
        )

        self.result_text = create_managed_text_box(
            parent=right,
            label_text="预演 / 执行结果",
            initial_value=self.cfg.get("last_result_text", ""),
            height=22,
            mono=True,
            wrap="none",
            readonly=True,
            enable_favorites=False,
            enable_clear=True,
            enable_wrap_toggle=True,
        )

        btn_row = styled_frame(body, bg=THEME["bg_panel"])
        btn_row.pack(fill="x", pady=(12, 0))

        styled_button(
            btn_row,
            "清空结果",
            self.clear_result,
            width=10,
        ).pack(side="left", padx=(0, 8))

        styled_button(
            btn_row,
            "打开备份目录",
            self.open_last_backup_dir,
            width=12,
        ).pack(side="left", padx=(0, 8))

        self.apply_button = styled_button(
            btn_row,
            "执行修改",
            self.apply,
            width=12,
            danger=True,
        )
        self.apply_button.pack(side="right", padx=(8, 0))

        self.preview_button = styled_button(
            btn_row,
            "Dry Run 预演",
            self.preview,
            width=14,
            accent=True,
        )
        self.preview_button.pack(side="right")

        self.refresh_mode_ui()

    def refresh_mode_ui(self):
        backup_dir_row = getattr(getattr(self, "backup_dir_entry", None), "_row_frame", None)
        restore_source_row = getattr(getattr(self, "restore_source_entry", None), "_row_frame", None)

        def show_entry_row(row):
            if row is not None and not row.winfo_ismapped():
                before_widget = getattr(self, "mode_row", None)

                if before_widget is not None:
                    row.pack(fill="x", pady=4, before=before_widget)
                else:
                    row.pack(fill="x", pady=4)

        def hide_entry_row(row):
            if row is not None:
                row.pack_forget()

        def show_widget(widget):
            if widget is not None and not widget.winfo_ismapped():
                widget.pack(side="left", padx=(0, 24))

        def hide_widget(widget):
            if widget is not None:
                widget.pack_forget()

        if self.patch_mode.get() == "restore":
            self.mode_hint.config(text="当前为备份还原模式，请谨慎操作")
            self.preview_button.config(text="预演还原")
            self.apply_button.config(text="执行还原")

            hide_entry_row(backup_dir_row)
            show_entry_row(restore_source_row)

            hide_widget(getattr(self, "allow_delete_check", None))
            hide_widget(getattr(self, "allow_multi_replace_check", None))
            show_widget(getattr(self, "backup_enabled_check", None))
            show_widget(getattr(self, "force_restore_check", None))
            show_widget(getattr(self, "keep_restore_source_path_check", None))
            show_widget(getattr(self, "open_backup_after_done_check", None))

        else:
            self.mode_hint.config(text="")
            self.preview_button.config(text="Dry Run 预演")
            self.apply_button.config(text="执行修改")

            hide_entry_row(restore_source_row)
            show_entry_row(backup_dir_row)

            show_widget(getattr(self, "allow_delete_check", None))
            show_widget(getattr(self, "allow_multi_replace_check", None))
            show_widget(getattr(self, "backup_enabled_check", None))
            hide_widget(getattr(self, "force_restore_check", None))
            hide_widget(getattr(self, "keep_restore_source_path_check", None))
            show_widget(getattr(self, "open_backup_after_done_check", None))

    def collect_config(self):
        self.cfg["project_root"] = self.project_root.get().strip()
        self.cfg["patch_mode"] = self.patch_mode.get()
        self.cfg["allow_delete"] = self.allow_delete.get()
        self.cfg["allow_multi_replace_exact"] = self.allow_multi_replace_exact.get()
        self.cfg["backup_enabled"] = self.backup_enabled.get()
        self.cfg["backup_dir"] = self.backup_dir.get().strip()
        self.cfg["restore_source_dir"] = self.restore_source_dir.get().strip()
        self.cfg["force_restore"] = self.force_restore.get()
        self.cfg["keep_restore_source_path"] = self.keep_restore_source_path.get()
        self.cfg.pop("rollback_backup_dir", None)
        self.cfg.pop("force_rollback", None)
        self.cfg["open_backup_after_done"] = self.open_backup_after_done.get()
        self.cfg["patch_text"] = get_text_value(self.patch_text)
        self.cfg["last_result_text"] = get_text_value(self.result_text)
        self.save_config()
        return self.cfg

    def make_current_snapshot(self, cfg):
        snapshot = {
            "project_root": cfg["project_root"],
            "patch_mode": cfg.get("patch_mode", "apply"),
            "backup_enabled": cfg.get("backup_enabled", True),
            "backup_dir": cfg.get("backup_dir", "99_归档/AI文件修改备份"),
        }

        if cfg.get("patch_mode", "apply") == "restore":
            snapshot.update({
                "restore_source_dir": cfg.get("restore_source_dir", ""),
                "force_restore": cfg.get("force_restore", False),
                "keep_restore_source_path": cfg.get("keep_restore_source_path", False),
            })
        else:
            snapshot.update({
                "allow_delete": cfg["allow_delete"],
                "allow_multi_replace_exact": cfg.get("allow_multi_replace_exact", False),
                "patch_text": cfg["patch_text"],
            })

        return snapshot

    def ensure_preview_snapshot_still_valid(self, cfg):
        if self.preview_payload is None or self.preview_snapshot is None:
            raise ValueError("请先执行 Dry Run，并确保校验通过")

        current_snapshot = self.make_current_snapshot(cfg)

        if current_snapshot != self.preview_snapshot:
            self.preview_payload = None
            self.preview_snapshot = None
            self.last_preview_text = ""
            raise ValueError(
                "当前项目根目录、允许删除选项或修改包内容已发生变化。\n\n"
                "为避免执行未经校验的内容，请重新点击【Dry Run 预演】，确认通过后再执行。"
            )

    def write_result(self, text):
        self.result_text.configure(state="normal")
        set_text_value(self.result_text, text)
        self.result_text.configure(state="disabled")
        self.cfg["last_result_text"] = text
        self.save_config()

    def mode_display_text(self):
        if self.patch_mode.get() == "restore":
            return "备份还原"
        return "执行修改"

    def append_result(self, title, text):
        old_text = get_text_value(self.result_text).rstrip()
        parts = []

        if old_text:
            parts.append(old_text)

        parts.extend([
            "",
            "=" * 60,
            f"【{current_timestamp_text()}｜{title}｜{self.mode_display_text()}】",
            "=" * 60,
            text or "",
        ])

        self.write_result("\n".join(parts).strip())

    def clear_result(self):
        self.write_result("")
        self.log("修改包执行器：已清空结果")

    def open_last_backup_dir(self):
        if self.last_backup_root:
            open_path_with_default_app(self.last_backup_root, self.root)
            return

        text = get_text_value(self.result_text)
        backup_line_prefix = "备份目录："

        for line in text.splitlines():
            if line.startswith(backup_line_prefix):
                path = line[len(backup_line_prefix):].strip()
                if path:
                    open_path_with_default_app(path, self.root)
                    return

        safe_show_error(
            "打开失败",
            "当前没有可打开的备份目录。请先执行一次修改包。",
            parent=self.root,
        )

    def preview(self):
        try:
            cfg = self.collect_config()

            project_root = validate_required_path(cfg["project_root"], "项目根目录")

            if cfg.get("patch_mode", "apply") == "restore":
                restore_source_dir = validate_required_path(
                    cfg.get("restore_source_dir", ""),
                    "备份来源",
                )

                self.set_status("正在预演备份还原...")
                self.log("用户启动：备份还原 Dry Run")

                result = preview_backup_restore(
                    project_root=project_root,
                    restore_source_dir=restore_source_dir,
                    backup_enabled=cfg.get("backup_enabled", True),
                    backup_dir=cfg.get("backup_dir", "99_归档/AI文件修改备份"),
                    force_restore=cfg.get("force_restore", False),
                )

                self.preview_payload = result.manifest
                self.preview_snapshot = self.make_current_snapshot(cfg)
                self.last_preview_text = result.preview_text
                self.preview_has_errors = result.has_mismatch
                self.preview_success_count = 0
                self.preview_failed_count = 0
                self.append_result("Dry Run 预演", result.preview_text)

                self.set_status("备份还原预演完成")
                self.log("备份还原 Dry Run 完成")
                safe_show_info("Dry Run 完成", "备份还原校验完成，请查看预演结果。", parent=self.root)
                return

            patch_text = validate_required_path(cfg["patch_text"], "修改包内容")

            self.set_status("正在执行 Dry Run 预演...")
            self.log("用户启动：修改包 Dry Run 预演")

            result = preview_patch(
                project_root=project_root,
                patch_text=patch_text,
                allow_delete=cfg["allow_delete"],
                allow_multi_replace_exact=cfg.get("allow_multi_replace_exact", False),
                backup_enabled=cfg.get("backup_enabled", True),
                backup_dir=cfg.get("backup_dir", "99_归档/AI文件修改备份"),
            )

            self.preview_payload = result.valid_patch
            self.preview_snapshot = self.make_current_snapshot(cfg)
            self.last_preview_text = result.preview_text
            self.preview_has_errors = result.has_errors
            self.preview_success_count = result.success_count
            self.preview_failed_count = result.failed_count
            self.append_result("Dry Run 预演", result.preview_text)

            self.set_status("Dry Run 预演完成")
            self.log("修改包 Dry Run 预演完成")

            if result.failed_count == 0:
                safe_show_info(
                    "Dry Run 完成",
                    "V2 修改包全部校验通过。请查看预演结果，确认无误后再执行。",
                    parent=self.root,
                )
            elif result.success_count > 0:
                safe_show_info(
                    "Dry Run 部分通过",
                    f"修改包存在失败项。\n\n校验成功：{result.success_count}\n校验失败：{result.failed_count}\n\n执行时将只执行校验成功的 OP。",
                    parent=self.root,
                )
            else:
                safe_show_error(
                    "Dry Run 全部失败",
                    "修改包没有任何可执行的成功 OP。请查看结果区中的失败详情。",
                    parent=self.root,
                )

        except Exception as e:
            self.preview_payload = None
            self.preview_snapshot = None
            self.last_preview_text = ""

            err = "【Dry Run 失败】\n\n" + str(e) + "\n\n" + traceback.format_exc()
            self.append_result("Dry Run 失败", err)

            self.set_status("Dry Run 失败")
            self.log(f"修改包 Dry Run 失败：{e}")
            safe_show_error("Dry Run 失败", str(e), parent=self.root)

    def apply(self):
        try:
            cfg = self.collect_config()

            project_root = validate_required_path(cfg["project_root"], "项目根目录")
            self.ensure_preview_snapshot_still_valid(cfg)

            if cfg.get("patch_mode", "apply") == "restore":
                if cfg.get("force_restore", False):
                    ok = safe_ask_yes_no(
                        "强制还原确认",
                        "你已勾选允许强制还原。\n\n"
                        "即使当前文件状态与备份记录不一致，也可能覆盖或删除当前文件。\n\n"
                        "确认继续？",
                        parent=self.root,
                    )

                    if not ok:
                        return

                ok = safe_ask_yes_no(
                    "执行还原确认",
                    "即将根据备份来源执行文件层级还原。\n\n"
                    "还原前程序会按“自动备份”设置备份当前状态。\n\n"
                    "如果未勾选“保留备份来源路径”，还原成功后会清空文本框中的备份来源路径，但不会删除任何实际备份文件。\n\n"
                    "确认执行？",
                    parent=self.root,
                )

                if not ok:
                    return

                self.set_status("正在执行备份还原...")
                self.log("用户启动：执行备份还原")

                result = apply_backup_restore(
                    project_root=project_root,
                    restore_source_dir=validate_required_path(
                        cfg.get("restore_source_dir", ""),
                        "备份来源",
                    ),
                    backup_enabled=cfg.get("backup_enabled", True),
                    backup_dir=cfg.get("backup_dir", "99_归档/AI文件修改备份"),
                    force_restore=cfg.get("force_restore", False),
                    preview_text=self.last_preview_text,
                )

                self.last_backup_root = result.backup_root
                self.append_result("执行结果", result.log_text)
                self.set_status("备份还原完成")
                self.log(f"备份还原完成，备份目录：{result.backup_root}")

                if not cfg.get("keep_restore_source_path", False):
                    self.restore_source_dir.set("")
                    self.cfg["restore_source_dir"] = ""
                    self.save_config()

                safe_show_info("执行完成", "备份还原已完成，请查看执行结果。", parent=self.root)

                if cfg.get("open_backup_after_done", False) and result.backup_root:
                    open_path_with_default_app(result.backup_root, self.root)

                return

            if self.preview_has_errors:
                if self.preview_success_count <= 0:
                    raise ValueError("当前 Dry Run 没有任何校验成功的 OP，不能执行。")

                ok = safe_ask_yes_no(
                    "部分执行确认",
                    f"当前修改包有部分 OP 校验失败。\n\n"
                    f"校验成功：{self.preview_success_count}\n"
                    f"校验失败：{self.preview_failed_count}\n\n"
                    f"将只执行校验成功的 OP，失败 OP 会被跳过。\n\n"
                    f"确认继续？",
                    parent=self.root,
                )

                if not ok:
                    return

            if cfg["allow_delete"]:
                ok = safe_ask_yes_no(
                    "删除确认",
                    "你已勾选允许删除文件。\n\n"
                    "如果修改包包含 delete_file，文件会先备份再删除。\n\n"
                    "确认继续？",
                    parent=self.root,
                )

                if not ok:
                    return

            ok = safe_ask_yes_no(
                "执行确认",
                "即将执行文件修改。\n\n"
                "程序会先备份被修改/删除的文件。\n\n"
                "确认执行？",
                parent=self.root,
            )

            if not ok:
                return

            self.set_status("正在执行修改包...")
            self.log("用户启动：执行修改包")

            result = apply_patch(
                project_root=project_root,
                patch=self.preview_payload,
                allow_delete=cfg["allow_delete"],
                allow_multi_replace_exact=cfg.get("allow_multi_replace_exact", False),
                backup_enabled=cfg.get("backup_enabled", True),
                backup_dir=cfg.get("backup_dir", "99_归档/AI文件修改备份"),
                patch_text=cfg.get("patch_text", ""),
                preview_text=self.last_preview_text,
            )

            self.last_backup_root = result.backup_root
            self.append_result("执行结果", result.log_text)

            self.set_status("修改包执行完成")
            self.log(f"修改包执行完成，备份目录：{result.backup_root}")

            safe_show_info(
                "执行完成",
                f"文件修改已完成。请查看执行结果。\n\n备份目录：\n{result.backup_root}",
                parent=self.root,
            )

            if cfg.get("open_backup_after_done", False):
                if result.backup_root:
                    open_path_with_default_app(result.backup_root, self.root)

        except Exception as e:
            err = "【执行失败】\n\n" + str(e) + "\n\n" + traceback.format_exc()
            self.append_result("执行失败", err)

            self.set_status("修改包执行失败")
            self.log(f"修改包执行失败：{e}")
            safe_show_error("执行失败", str(e), parent=self.root)
