# -*- coding: utf-8 -*-

import traceback
import tkinter as tk

from core.constants import THEME
from core.message_utils import safe_ask_yes_no, safe_show_error, safe_show_info
from core.paths import open_path_with_default_app, validate_required_path
from core.text_io import get_text_value, set_text_value
from panels.base_panel import BasePanel
from services.patch_service import preview_patch, apply_patch
from ui.dialogs import create_managed_text_box
from ui.theme import styled_frame, styled_button
from ui.widgets import (
    create_entry_row,
    browse_folder,
    bind_autosave,
    make_checkbutton,
)


class PatchPanel(BasePanel):
    config_key = "patch"

    def build(self):
        body = self.make_body()

        self.project_root = tk.StringVar(value=self.cfg.get("project_root", ""))
        self.allow_delete = tk.BooleanVar(value=self.cfg.get("allow_delete", False))
        self.allow_multi_replace_exact = tk.BooleanVar(
            value=self.cfg.get("allow_multi_replace_exact", False)
        )
        self.open_backup_after_done = tk.BooleanVar(value=self.cfg.get("open_backup_after_done", False))

        self.current_patch = None
        self.preview_snapshot = None
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

        option_row = styled_frame(body, bg=THEME["bg_panel"])
        option_row.pack(fill="x", pady=(10, 0))

        make_checkbutton(
            option_row,
            "允许删除文件（危险）",
            self.allow_delete,
        ).pack(side="left", padx=(0, 24))

        make_checkbutton(
            option_row,
            "允许多处精确替换",
            self.allow_multi_replace_exact,
        ).pack(side="left", padx=(0, 24))

        make_checkbutton(
            option_row,
            "执行完成后打开备份目录",
            self.open_backup_after_done,
        ).pack(side="left")

        bind_autosave(
            self.cfg,
            [
                (self.project_root, "project_root"),
                (self.allow_delete, "allow_delete"),
                (self.allow_multi_replace_exact, "allow_multi_replace_exact"),
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

        styled_button(
            btn_row,
            "执行修改",
            self.apply,
            width=12,
            danger=True,
        ).pack(side="right", padx=(8, 0))

        styled_button(
            btn_row,
            "Dry Run 预演",
            self.preview,
            width=14,
            accent=True,
        ).pack(side="right")

    def collect_config(self):
        self.cfg["project_root"] = self.project_root.get().strip()
        self.cfg["allow_delete"] = self.allow_delete.get()
        self.cfg["allow_multi_replace_exact"] = self.allow_multi_replace_exact.get()
        self.cfg["open_backup_after_done"] = self.open_backup_after_done.get()
        self.cfg["patch_text"] = get_text_value(self.patch_text)
        self.cfg["last_result_text"] = get_text_value(self.result_text)
        self.save_config()
        return self.cfg

    def make_current_snapshot(self, cfg):
        return {
            "project_root": cfg["project_root"],
            "allow_delete": cfg["allow_delete"],
            "allow_multi_replace_exact": cfg.get("allow_multi_replace_exact", False),
            "patch_text": cfg["patch_text"],
        }

    def ensure_preview_snapshot_still_valid(self, cfg):
        if self.current_patch is None or self.preview_snapshot is None:
            raise ValueError("请先执行 Dry Run，并确保校验通过")

        current_snapshot = self.make_current_snapshot(cfg)

        if current_snapshot != self.preview_snapshot:
            self.current_patch = None
            self.preview_snapshot = None
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
            patch_text = validate_required_path(cfg["patch_text"], "修改包内容")

            self.set_status("正在执行 Dry Run 预演...")
            self.log("用户启动：修改包 Dry Run 预演")

            result = preview_patch(
                project_root=project_root,
                patch_text=patch_text,
                allow_delete=cfg["allow_delete"],
                allow_multi_replace_exact=cfg.get("allow_multi_replace_exact", False),
            )

            self.current_patch = result.patch
            self.preview_snapshot = self.make_current_snapshot(cfg)
            self.write_result(result.preview_text)

            self.set_status("Dry Run 预演完成")
            self.log("修改包 Dry Run 预演完成")

            safe_show_info(
                "Dry Run 完成",
                "V2 修改包校验通过。请查看预演结果，确认无误后再执行。",
                parent=self.root,
            )

        except Exception as e:
            self.current_patch = None
            self.preview_snapshot = None

            err = "【Dry Run 失败】\n\n" + str(e) + "\n\n" + traceback.format_exc()
            self.write_result(err)

            self.set_status("Dry Run 失败")
            self.log(f"修改包 Dry Run 失败：{e}")
            safe_show_error("Dry Run 失败", str(e), parent=self.root)

    def apply(self):
        try:
            cfg = self.collect_config()

            project_root = validate_required_path(cfg["project_root"], "项目根目录")
            self.ensure_preview_snapshot_still_valid(cfg)

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
                patch=self.current_patch,
                allow_delete=cfg["allow_delete"],
                allow_multi_replace_exact=cfg.get("allow_multi_replace_exact", False),
            )

            self.last_backup_root = result.backup_root
            self.write_result(result.log_text)

            self.set_status("修改包执行完成")
            self.log(f"修改包执行完成，备份目录：{result.backup_root}")

            safe_show_info(
                "执行完成",
                f"文件修改已完成。请查看执行结果。\n\n备份目录：\n{result.backup_root}",
                parent=self.root,
            )

            if cfg.get("open_backup_after_done", False):
                open_path_with_default_app(result.backup_root, self.root)

        except Exception as e:
            err = "【执行失败】\n\n" + str(e) + "\n\n" + traceback.format_exc()
            self.write_result(err)

            self.set_status("修改包执行失败")
            self.log(f"修改包执行失败：{e}")
            safe_show_error("执行失败", str(e), parent=self.root)
