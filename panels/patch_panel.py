# -*- coding: utf-8 -*-

import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from core.config import SaveStatus
from core.constants import THEME
from core.message_utils import safe_ask_risk_confirm, safe_ask_yes_no, safe_show_error, safe_show_info
from core.path_validation import normalize_windows_display_path
from core.paths import (
    center_window, get_app_dir, get_initial_dir_from_path,
    is_frozen_app, open_path_with_default_app, validate_required_path,
)
from core.text_io import get_text_value, replace_text_preserve_view, set_text_value
from core.time_utils import current_timestamp_text
from panels.base_panel import BasePanel
from services.patch_backup import resolve_backup_base_dir
from services.patch_protocol_doc_service import get_patch_protocol_doc
from services.patch_protocol_doc_sync import (
    apply_protocol_doc_update, is_protocol_doc_update_available, preview_protocol_doc_update,
)
from services.patch_service import apply_backup_restore, apply_patch, preview_backup_restore, preview_patch
from ui.dialogs import create_managed_text_box
from ui.theme import styled_button, styled_frame
from ui.widgets import browse_folder, create_entry_row, make_checkbutton, make_radiobutton
from ui.window_manager import register_popup, unregister_popup


class PatchPanel(BasePanel):
    config_key = "patch"

    def build(self):
        body = self.make_body()
        for field in ("project_root", "backup_dir", "restore_source_dir"):
            setattr(self, field, tk.StringVar(value=self.cfg[field]))
        self.patch_mode = tk.StringVar(value=self.cfg["patch_mode"])
        self.last_backup_root = ""

        create_entry_row(
            body, "项目根目录", self.project_root,
            commits=self.commits, config=self.cfg, field="project_root", page="patch",
            history_key="patch.project_root", value_normalizer=normalize_windows_display_path,
            browse_command=lambda: browse_folder(self.project_root, "选择项目根目录", parent=self.root),
            open_command=lambda: open_path_with_default_app(self.project_root.get(), self.root),
        )
        self.backup_dir_entry = create_entry_row(
            body, "备份目录", self.backup_dir,
            commits=self.commits, config=self.cfg, field="backup_dir", page="patch",
            history_key="patch.backup_dir", value_normalizer=normalize_windows_display_path,
            browse_command=lambda: self.browse_project_relative_folder(self.backup_dir, "选择备份保存目录"),
            open_fields={"project_root", "backup_dir"}, open_command=self.open_configured_backup_dir,
        )
        self.restore_source_entry = create_entry_row(
            body, "备份来源", self.restore_source_dir,
            commits=self.commits, config=self.cfg, field="restore_source_dir", page="patch",
            history_key="patch.restore_source_dir", value_normalizer=normalize_windows_display_path,
            browse_command=lambda: self.browse_project_relative_folder(self.restore_source_dir, "选择用于还原的历史备份目录"),
            open_command=lambda: open_path_with_default_app(self.restore_source_dir.get(), self.root),
            tooltip_text="备份来源目录必须包含 manifest.json。",
        )

        self.mode_row = styled_frame(body, bg=THEME["bg_panel"])
        self.mode_row.pack(fill="x", pady=(10, 0))
        tk.Label(
            self.mode_row, text="执行模式", width=10, anchor="w",
            bg=THEME["bg_panel"], fg=THEME["fg_label"], font=THEME["font_main"],
        ).pack(side="left", padx=(0, 8))
        for label, value, danger in (
            ("执行修改", "apply", False), ("备份还原", "restore", True),
        ):
            make_radiobutton(
                self.mode_row, label, self.patch_mode, value, commits=self.commits,
                config=self.cfg, field="patch_mode", command=self.refresh_mode_ui, danger=danger,
            ).pack(side="left", padx=(0, 24))
        self.mode_hint = tk.Label(
            self.mode_row, text="", bg=THEME["bg_panel"],
            fg=THEME["danger"], font=THEME["font_title"],
        )
        self.mode_hint.pack(side="left", padx=(24, 0))

        option_row = styled_frame(body, bg=THEME["bg_panel"])
        option_row.pack(fill="x", pady=(10, 0))
        for field, label, attribute, tip in (
            ("allow_delete", "允许删除文件/文件夹（危险）", "allow_delete_check", "允许修改包中的删除操作；仍保留路径校验和执行确认。"),
            ("allow_multi_replace_exact", "允许多处精确替换", "allow_multi_replace_check", "允许按修改包声明替换多处完全相同文本。"),
            ("backup_enabled", "自动备份", "backup_enabled_check", "执行修改或还原前，按设置备份原状态。"),
            ("keep_restore_source_path", "保留备份来源路径", "keep_restore_source_path_check", "不勾选时，仅在还原成功后清空来源，不删除备份文件。"),
            ("open_backup_after_done", "执行完成后打开备份目录", "open_backup_after_done_check", "仅在本次生成了备份目录时打开。"),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            widget = make_checkbutton(
                option_row, label, variable, commits=self.commits,
                config=self.cfg, field=field, tooltip_text=tip,
            )
            setattr(self, attribute, widget)
            widget.pack(side="left", padx=(0, 24))

        area = styled_frame(body, bg=THEME["bg_panel"])
        area.pack(fill="both", expand=True, pady=(10, 0))
        left, right = styled_frame(area), styled_frame(area)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.patch_text = create_managed_text_box(
            left, "粘贴 Zipatch V3 修改包", self.cfg["patch_text"],
            height=22, mono=True, commits=self.commits, config=self.cfg,
            field="patch_text", page="patch", favorite_key="patch_text",
            wrap="none", enable_import_text=True, wrap_config_key="patch.patch_text",
        )
        self.result_text = create_managed_text_box(
            right, "预演 / 执行结果", self.cfg["last_result_text"],
            height=22, mono=True, readonly=True, commits=self.commits,
            enable_favorites=False, wrap="none", wrap_config_key="patch.result_text",
            on_content_changed=self.accept_result_control,
        )

        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(12, 0))
        styled_button(row, "清空结果", self.clear_result, width=10).pack(side="left", padx=(0, 8))
        styled_button(row, "打开备份目录", self.open_last_backup_dir, width=12).pack(side="left")
        self.apply_button = styled_button(row, "预演并执行", self.apply, width=12, danger=True)
        self.apply_button.pack(side="right", padx=(8, 0))
        self.preview_button = styled_button(row, "Dry Run 预演", self.preview, width=14, accent=True)
        self.preview_button.pack(side="right")
        styled_button(row, "修改包协议规范", self.show_protocol_doc, width=16).pack(
            side="right", padx=(0, 8),
        )
        self.refresh_mode_ui()

    def refresh_mode_ui(self):
        restore = self.patch_mode.get() == "restore"
        for row, visible in (
            (self.backup_dir_entry._row_frame, not restore),
            (self.restore_source_entry._row_frame, restore),
        ):
            if visible:
                if not row.winfo_manager():
                    row.pack(fill="x", pady=4, before=self.mode_row)
            else:
                row.pack_forget()
        option_widgets = (
            (self.allow_delete_check, not restore),
            (self.allow_multi_replace_check, not restore),
            (self.backup_enabled_check, True),
            (self.keep_restore_source_path_check, restore),
            (self.open_backup_after_done_check, True),
        )
        for widget, _ in option_widgets:
            widget.pack_forget()
        for widget, visible in option_widgets:
            if visible:
                widget.pack(side="left", padx=(0, 24))
        self.mode_hint.configure(text="当前为备份还原模式，请谨慎操作" if restore else "")
        self.preview_button.configure(text="预演还原" if restore else "Dry Run 预演")
        color = THEME["danger"] if restore else THEME["warning"]
        self.apply_button.configure(
            text="预演并还原" if restore else "预演并执行",
            bg=color, fg=THEME["fg_on_dark"], activebackground=color,
        )
        self.apply_button._normal_bg = color
        self.apply_button._normal_fg = THEME["fg_on_dark"]

    def resolve_project_relative_initial_dir(self, value):
        root_text = normalize_windows_display_path(self.project_root.get())
        if not root_text:
            return None
        root = Path(root_text).expanduser().resolve()
        if not root.is_dir():
            return None
        value = normalize_windows_display_path(value)
        target = Path(value).expanduser() if value else root
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if target.is_dir():
            return str(target)
        if target.parent.is_dir():
            return str(target.parent)
        return str(root)

    def browse_project_relative_folder(self, variable, title):
        return browse_folder(
            variable, title,
            initial_dir=self.resolve_project_relative_initial_dir(variable.get()),
            parent=self.root,
        )

    def open_configured_backup_dir(self):
        try:
            path = resolve_backup_base_dir(self.cfg["project_root"], self.cfg["backup_dir"])
        except (OSError, ValueError) as error:
            safe_show_error("打开失败", str(error), self.root)
            return
        open_path_with_default_app(str(path), self.root)

    def prepare_parameters(self):
        fields = {
            "project_root", "patch_mode", "backup_enabled", "backup_dir",
            "open_backup_after_done",
        }
        if self.cfg["patch_mode"] == "restore":
            fields |= {"restore_source_dir", "keep_restore_source_path"}
        else:
            fields |= {"allow_delete", "allow_multi_replace_exact", "patch_text"}
        return self.prepare(fields)

    def display_result(self, text):
        self.result_text.configure(state="normal")
        replace_text_preserve_view(self.result_text, text)
        self.result_text.configure(state="disabled")
        return text

    def accept_result_control(self):
        if self.commits.operation_depth:
            return False
        text = get_text_value(self.result_text)
        return self.commits.submit_values(
            self.cfg, {"last_result_text": text}, self.root,
        ).request_satisfied

    def clear_result(self):
        if self.commits.operation_depth:
            return
        self.display_result("")
        self.commits.submit_values(self.cfg, {"last_result_text": ""}, self.root)
        self.log("修改包执行器：已清空结果")

    def result_section(self, title, content, cfg):
        mode = "备份还原" if cfg["patch_mode"] == "restore" else "执行修改"
        return (
            "=" * 60 + "\n"
            f"【{current_timestamp_text()}｜{title}｜{mode}】\n"
            + "=" * 60 + "\n" + content
        )

    def finish_result(self, text, updates=None, outcome="预演已完成"):
        final = self.display_result(text)
        values = {"last_result_text": final}
        if updates:
            values.update(updates)
        self.commits.manager.accept(self.cfg, values)
        result = self.commits.manager.save(include_session=True)
        if result.status is SaveStatus.FAILED:
            safe_show_error(
                "结果未持久化",
                f"{outcome}。\n\n"
                "最新结果或关联设置未能写入配置文件，已接受内容仍保留在内存。\n"
                "保存重试不会自动再次执行业务。\n\n"
                f"{result.error}",
                self.root,
            )
        return result

    def run_preview(self, cfg):
        """消费本次固定参数；计算结果不写入面板或配置状态。"""
        project = validate_required_path(cfg["project_root"], "项目根目录")
        if cfg["patch_mode"] == "restore":
            source = validate_required_path(cfg["restore_source_dir"], "备份来源")
            return preview_backup_restore(
                project_root=project, restore_source_dir=source,
                backup_enabled=cfg["backup_enabled"], backup_dir=cfg["backup_dir"],
            )
        patch = validate_required_path(cfg["patch_text"], "修改包内容")
        return preview_patch(
            project_root=project, patch_text=patch,
            allow_delete=cfg["allow_delete"],
            allow_multi_replace_exact=cfg["allow_multi_replace_exact"],
            backup_enabled=cfg["backup_enabled"], backup_dir=cfg["backup_dir"],
        )

    def preview_failure(self, error, cfg):
        content = "【Dry Run 失败】\n\n" + str(error) + "\n\n" + traceback.format_exc()
        self.finish_result(
            self.result_section("Dry Run 失败", content, cfg),
            outcome="预演未能完成，以下保存失败仅针对失败报告",
        )
        self.set_status("Dry Run 失败")
        safe_show_error("Dry Run 失败", "预演未能完成，请查看结果中的错误信息。", self.root)

    def failure_message(self, result):
        if result.has_global_errors:
            return "Dry Run 未通过。\n\n全局校验：不通过\n最终可执行 OP：0"
        return (
            "Dry Run 存在单项失败。\n\n"
            f"单项校验通过：{result.success_count}\n"
            f"单项校验失败：{result.failed_count}\n"
            f"最终可执行 OP：{result.success_count}"
        )

    def preview(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._preview()

    def _preview(self):
        cfg = self.prepare_parameters()
        if cfg is None:
            return
        self.display_result("")
        self.set_status(
            "正在预演备份还原..." if cfg["patch_mode"] == "restore"
            else "正在执行 Dry Run 预演..."
        )
        try:
            self.commits.metrics["business_starts"] += 1
            result = self.run_preview(cfg)
        except (OSError, ValueError) as error:
            self.preview_failure(error, cfg)
            return
        self.set_status("Dry Run 预演完成")
        self.log("Dry Run 预演完成")
        saved = self.finish_result(
            self.result_section("Dry Run 预演", result.preview_text, cfg),
        )
        if cfg["patch_mode"] == "apply" and result.failed_count:
            safe_show_error("Dry Run 存在失败项", self.failure_message(result), self.root)
        elif saved.persisted:
            safe_show_info("Dry Run 完成", "校验完成，请查看预演结果。", self.root)


    def build_apply_operation_summary(self, cfg, patch):
        project = Path(cfg["project_root"]).resolve()
        groups = [
            ("create", "新增文件"), ("create_dir", "创建目录"),
            ("overwrite", "覆盖已有路径"), ("append_text", "追加文本"),
            ("replace_exact", "精确替换"), ("replace_between", "锚点区间替换"),
            ("rename_file", "文件改名"), ("move_file", "移动文件"),
            ("copy_file", "复制文件"), ("rename_dir", "目录改名"),
            ("move_dir", "移动目录"), ("copy_dir", "复制目录"),
            ("delete_file", "删除文件"), ("delete_dir", "删除文件夹"),
            ("skip", "跳过写入"),
        ]
        paths = {key: [] for key, _ in groups}
        counts = {key: 0 for key, _ in groups}
        replacements = 0

        for operation in patch["operations"]:
            kind = operation["op"]
            relative = operation["path"].replace("\\", "/").strip()
            target = project / relative
            display = relative
            if kind == "write_file":
                if target.exists():
                    group = "skip" if operation.get("if_exists", "error") == "skip" else "overwrite"
                else:
                    group = "create"
            elif kind in (
                "rename_file", "move_file", "copy_file",
                "rename_dir", "move_dir", "copy_dir",
            ):
                new_path = operation["new_path"].replace("\\", "/").strip()
                display = f"{relative} -> {new_path}"
                if (project / new_path).exists():
                    group = "skip" if operation.get("if_exists", "error") == "skip" else "overwrite"
                else:
                    group = kind
            else:
                group = kind
            counts[group] += 1
            if group == "replace_exact":
                replacements += int(operation["count"])
            if display not in paths[group]:
                paths[group].append(display)

        existing = {
            "overwrite", "append_text", "replace_exact", "replace_between",
            "rename_file", "move_file", "copy_file", "rename_dir",
            "move_dir", "copy_dir", "delete_file", "delete_dir",
        }
        return {
            "groups": groups, "paths": paths, "counts": counts,
            "replacements": replacements, "existing": existing,
            "high_risk": not cfg["backup_enabled"] and any(paths[key] for key in existing),
        }

    def format_grouped_file_list(self, summary, only_existing=False):
        lines = []
        for key, title in summary["groups"]:
            if only_existing and key not in summary["existing"]:
                continue
            paths = summary["paths"][key]
            if not paths:
                continue
            extra = f"，文本替换 {summary['replacements']} 处" if key == "replace_exact" else ""
            lines.append(
                f"{title}：OP {summary['counts'][key]} 个{extra}，涉及 {len(paths)} 个文件"
            )
            lines.extend(f"- {path}" for path in paths)
            lines.append("")
        return "\n".join(lines).rstrip()

    def confirm_apply_execution(self, cfg, patch):
        summary = self.build_apply_operation_summary(cfg, patch)
        if summary["high_risk"]:
            message = (
                "高风险：当前未开启自动备份。\n\n"
                "本次将修改或删除已有文件，工具无法自动还原到执行前状态。\n\n"
                + self.format_grouped_file_list(summary, only_existing=True)
                + "\n\n确认继续执行？"
            )
            return safe_ask_risk_confirm(
                "高风险确认：未启用自动备份", message, self.root, danger=True,
            )
        backup = "自动备份已开启。" if cfg["backup_enabled"] else "当前未开启自动备份。"
        return safe_ask_risk_confirm(
            "执行确认",
            "即将执行文件修改。\n\n" + backup + "\n\n本次操作清单：\n\n"
            + self.format_grouped_file_list(summary) + "\n\n确认执行？",
            self.root,
        )

    def confirm_restore_execution(self, cfg, summary):
        lines = [
            "即将根据备份来源执行文件层级还原。",
            "还原前程序会按“自动备份”设置备份当前状态。", "", "风险清单：",
        ]
        for key, title in (
            ("mismatch_paths", "状态漂移项"), ("delete_paths", "将删除当前存在路径"),
            ("overwrite_paths", "将覆盖当前路径"), ("restore_dirs", "涉及目录树还原"),
        ):
            paths = summary.get(key, [])
            if paths:
                lines.append(f"\n{title}：{len(paths)} 项")
                lines.extend(f"- {path}" for path in paths)
        if summary.get("has_risk"):
            lines.append("\n若继续执行，当前路径可能被覆盖或删除。本次确认不是长期强制开关。")
        if not cfg["keep_restore_source_path"]:
            lines.append("\n还原成功后清空备份来源路径，但不删除实际备份文件。")
        lines.append("\n确认执行还原？")
        return safe_ask_risk_confirm(
            "执行还原确认", "\n".join(lines), self.root,
            danger=bool(summary.get("has_risk")),
        )

    def apply(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._apply()

    def _apply(self):
        cfg = self.prepare_parameters()
        if cfg is None:
            return
        self.display_result("")
        self.set_status(
            "正在预演备份还原..." if cfg["patch_mode"] == "restore"
            else "正在执行 Dry Run 预演..."
        )
        try:
            self.commits.metrics["business_starts"] += 1
            preview = self.run_preview(cfg)
        except (OSError, ValueError) as error:
            self.preview_failure(error, cfg)
            return

        self.set_status("Dry Run 预演完成")
        self.log("Dry Run 预演完成")
        report = self.result_section("Dry Run 预演", preview.preview_text, cfg)
        self.display_result(report)

        if cfg["patch_mode"] == "apply" and preview.failed_count:
            message = self.failure_message(preview)
            if preview.has_global_errors or preview.success_count == 0:
                self.finish_result(report)
                safe_show_error("Dry Run 存在失败项", message, self.root)
                return
            if not safe_ask_yes_no(
                "部分执行确认",
                message + "\n\n是否跳过失败的 OP，只执行通过单项校验的 OP？",
                self.root, icon="warning",
            ):
                self.finish_result(report)
                return

        confirmed = (
            self.confirm_restore_execution(cfg, preview.risk_summary)
            if cfg["patch_mode"] == "restore"
            else self.confirm_apply_execution(cfg, preview.valid_patch)
        )
        if not confirmed:
            self.finish_result(report)
            return

        try:
            self.set_status(
                "正在执行备份还原..." if cfg["patch_mode"] == "restore"
                else "正在执行修改包..."
            )
            self.commits.metrics["business_starts"] += 1
            if cfg["patch_mode"] == "restore":
                result = apply_backup_restore(
                    project_root=cfg["project_root"],
                    restore_source_dir=cfg["restore_source_dir"],
                    backup_enabled=cfg["backup_enabled"], backup_dir=cfg["backup_dir"],
                    risk_confirmed=True, preview_text=preview.preview_text,
                )
            else:
                result = apply_patch(
                    project_root=cfg["project_root"], patch=preview.valid_patch,
                    allow_delete=cfg["allow_delete"],
                    allow_multi_replace_exact=cfg["allow_multi_replace_exact"],
                    backup_enabled=cfg["backup_enabled"], backup_dir=cfg["backup_dir"],
                    patch_text=cfg["patch_text"], preview_text=preview.preview_text,
                )
        except (OSError, ValueError) as error:
            content = str(error) + "\n\n" + traceback.format_exc()
            self.finish_result(
                report + "\n\n" + self.result_section("执行失败", content, cfg),
                outcome="业务未能完成，以下保存失败仅针对失败报告",
            )
            self.set_status("执行失败")
            safe_show_error("执行失败", "业务未能完成，请查看执行结果。", self.root)
            return

        self.last_backup_root = result.backup_root
        updates = {}
        if cfg["patch_mode"] == "restore" and not cfg["keep_restore_source_path"]:
            self.restore_source_dir.set("")
            self.restore_source_entry._config_binding.dirty = False
            updates["restore_source_dir"] = ""
        saved = self.finish_result(
            report + "\n\n" + self.result_section("执行结果", result.log_text, cfg),
            updates,
            outcome="备份还原已经完成" if cfg["patch_mode"] == "restore" else "文件修改已经完成",
        )
        self.set_status("备份还原完成" if cfg["patch_mode"] == "restore" else "修改包执行完成")
        self.log(f"执行完成，备份目录：{result.backup_root}")
        if saved.persisted:
            safe_show_info("执行完成", "业务已完成，请查看执行结果。", self.root)
        if cfg["open_backup_after_done"] and result.backup_root:
            open_path_with_default_app(result.backup_root, self.root)

    def open_last_backup_dir(self):
        if self.last_backup_root:
            open_path_with_default_app(self.last_backup_root, self.root)
            return
        for line in self.cfg["last_result_text"].splitlines():
            if line.startswith("备份目录："):
                path = line[len("备份目录："):].strip()
                if path:
                    open_path_with_default_app(path, self.root)
                    return
        safe_show_error("打开失败", "当前没有可打开的备份目录。", self.root)

    def format_protocol_doc_source_desc(self, doc):
        text = "当前显示：内置修改包协议规范"
        if doc.updated_at:
            text += f"    更新时间：{doc.updated_at}"
        if doc.source_sha256:
            text += f"    指纹：{doc.source_sha256[:12]}..."
        return text

    def show_protocol_doc(self):
        doc = get_patch_protocol_doc()
        dialog = tk.Toplevel(self.root)
        dialog.title("修改包协议规范")
        dialog.configure(bg=THEME["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        center_window(dialog, 980, 740)
        description = tk.StringVar(value=self.format_protocol_doc_source_desc(doc))
        tk.Label(
            dialog, textvariable=description, bg=THEME["bg"], fg=THEME["fg_dim"],
            font=THEME["font_main"], anchor="w", wraplength=930,
        ).pack(fill="x", padx=16, pady=(12, 4))
        body = styled_frame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        text = create_managed_text_box(
            body, "协议规范内容", doc.content, height=30, mono=True,
            readonly=True, commits=self.commits, enable_favorites=False,
            enable_clear=False, wrap_config_key="patch.protocol_doc_text",
        )
        bottom = styled_frame(dialog)
        bottom.pack(fill="x", padx=16, pady=(4, 14))

        def close():
            if self.commits.operation_depth:
                return False
            unregister_popup(dialog)
            dialog.destroy()
            return True

        if is_protocol_doc_update_available():
            styled_button(
                bottom, "从 Markdown 更新",
                lambda: self.update_protocol_doc_from_markdown(dialog, text, description),
                width=16,
            ).pack(side="left")
        styled_button(bottom, "关闭", close, width=10).pack(side="right")
        register_popup(dialog, close)

    def update_protocol_doc_from_markdown(self, dialog, text, description):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._update_protocol_doc_from_markdown(dialog, text, description)

    def _update_protocol_doc_from_markdown(self, dialog, text, description):
        if is_frozen_app():
            safe_show_error("当前环境不支持更新", "请在源码环境更新后重新封装。", dialog)
            return
        current = self.cfg["protocol_doc_source_path"]
        selected = filedialog.askopenfilename(
            title="选择修改包协议规范 Markdown 源文档",
            initialdir=get_initial_dir_from_path(current) or str(get_app_dir()),
            initialfile=Path(current).name if current else "Zipatch_V3_修改包协议规范.md",
            filetypes=[("Markdown Documents", "*.md"), ("All Files", "*.*")],
            parent=dialog,
        )
        if not selected:
            return
        selected = str(Path(selected).expanduser().resolve())
        self.commits.manager.accept(
            self.cfg, {"protocol_doc_source_path": selected},
        )
        if not self.commits.finish(dialog, include_session=True).persisted:
            return

        try:
            preview = preview_protocol_doc_update(selected)
            if not preview.has_difference:
                safe_show_info("无需更新", "所选文档与当前内置文档内容一致。", dialog)
                return
            message = (
                f"当前内置字符数：{preview.embedded_length}\n"
                f"当前指纹：{preview.embedded_sha256}\n\n"
                f"源文档：{preview.source_path}\n"
                f"编码：{preview.source_encoding}\n"
                f"字符数：{preview.source_length}\n"
                f"指纹：{preview.source_sha256}\n\n是否更新内置协议文档？"
            )
            if not safe_ask_yes_no("发现协议文档差异", message, dialog):
                return
            target = apply_protocol_doc_update(preview)
            refreshed = get_patch_protocol_doc()
            text.configure(state="normal")
            set_text_value(text, refreshed.content)
            text.configure(state="disabled")
            description.set(self.format_protocol_doc_source_desc(refreshed))
        except (OSError, ValueError, RuntimeError) as error:
            safe_show_error("更新失败", str(error), dialog)
            return
        self.log(f"内置修改包协议规范已更新：{target}")
        self.set_status("内置协议文档已更新")
        safe_show_info("更新完成", "内置协议文档已更新；发布 exe 时请重新封装。", dialog)