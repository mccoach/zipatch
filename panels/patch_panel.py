# -*- coding: utf-8 -*-

import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from core.constants import THEME
from core.message_utils import (
    safe_ask_yes_no,
    safe_ask_risk_confirm,
    safe_show_error,
    safe_show_info,
)
from core.path_validation import normalize_windows_display_path
from core.paths import (
    center_window,
    get_app_dir,
    get_initial_dir_from_path,
    is_frozen_app,
    open_path_with_default_app,
    validate_required_path,
)
from core.text_io import (
    get_text_value,
    replace_text_preserve_view,
    set_text_value,
)
from core.time_utils import current_timestamp_text
from panels.base_panel import BasePanel
from services.patch_protocol_doc_service import get_patch_protocol_doc
from services.patch_protocol_doc_sync import (
    apply_protocol_doc_update,
    is_protocol_doc_update_available,
    preview_protocol_doc_update,
)
from services.patch_service import (
    preview_patch,
    apply_patch,
    preview_backup_restore,
    apply_backup_restore,
    resolve_backup_base_dir,
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
from ui.window_manager import register_popup, unregister_popup


class PatchPanel(BasePanel):
    config_key = "patch"

    def build(self):
        body = self.make_body()

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
        self.keep_restore_source_path = tk.BooleanVar(
            value=self.cfg.get("keep_restore_source_path", False)
        )
        self.open_backup_after_done = tk.BooleanVar(
            value=self.cfg.get("open_backup_after_done", False)
        )

        self.preview_has_errors = False
        self.preview_success_count = 0
        self.preview_failed_count = 0
        self.preview_has_global_errors = False

        self.preview_apply_patch = None
        self.preview_restore_manifest = None
        self.preview_snapshot = None
        self.last_preview_text = ""
        self.last_backup_root = ""
        self.restore_risk_summary = None

        create_entry_row(
            body,
            "项目根目录",
            self.project_root,
            browse_command=lambda: browse_folder(self.project_root, title="选择项目根目录"),
            open_command=lambda: open_path_with_default_app(self.project_root.get(), self.root),
            config_data=self.config_data,
            history_key="patch.project_root",
            save_config=self.save_config,
            value_normalizer=normalize_windows_display_path,
        )

        self.backup_dir_entry = create_entry_row(
            body,
            "备份目录",
            self.backup_dir,
            browse_command=self.browse_backup_dir,
            open_command=self.open_configured_backup_dir,
            config_data=self.config_data,
            history_key="patch.backup_dir",
            save_config=self.save_config,
            value_normalizer=normalize_windows_display_path,
        )

        self.restore_source_entry = create_entry_row(
            body,
            "备份来源",
            self.restore_source_dir,
            browse_command=self.browse_restore_source_dir,
            open_command=lambda: open_path_with_default_app(self.restore_source_dir.get(), self.root),
            tooltip_text="用于备份还原的历史备份目录。该目录必须包含 manifest.json。",
            config_data=self.config_data,
            history_key="patch.restore_source_dir",
            save_config=self.save_config,
            value_normalizer=normalize_windows_display_path,
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
            "执行 AI V2 修改包。必须先 Dry Run 预演，确认校验结果后再执行。",
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

        option_row = styled_frame(body, bg=THEME["bg_panel"])
        option_row.pack(fill="x", pady=(10, 0))
        self.option_row = option_row

        self.allow_delete_check = make_checkbutton(
            option_row,
            "允许删除文件/文件夹（危险）",
            self.allow_delete,
            tooltip_text="仅执行修改模式使用。勾选后，修改包中的 delete_file 和 delete_dir 操作才允许执行；删除前会按自动备份设置先备份。",
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
            tooltip_text="勾选后，执行修改前会备份被修改/删除的文件；备份还原前会备份当前状态。",
        )
        self.backup_enabled_check.pack(side="left", padx=(0, 24))

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
            wrap_config_key="patch.patch_text",
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
            config_data=self.config_data,
            save_config_func=self.save_config,
            wrap_config_key="patch.result_text",
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
            "预演并执行",
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

        styled_button(
            btn_row,
            "修改包协议规范",
            self.show_protocol_doc,
            width=16,
        ).pack(side="right", padx=(0, 8))

        self.refresh_mode_ui()

    def show_protocol_doc(self):
        """
        只读弹窗显示修改包协议规范文档。

        运行时唯一读取源是 services.patch_protocol_doc 中的内置文本：
        - 不读取外部 md；
        - 不依赖 assets；
        - 打包 exe 后稳定可显示。

        开发模式下额外显示“从 Markdown 更新”按钮，用于人工指定 md 源文档，
        比对后确认同步到内置 py 文档模块。
        """
        try:
            doc = get_patch_protocol_doc()

            dialog = tk.Toplevel(self.root)
            dialog.title("修改包协议规范")
            dialog.configure(bg=THEME["bg"])
            center_window(dialog, 980, 740)
            dialog.transient(self.root)
            dialog.grab_set()

            def close(event=None):
                unregister_popup(dialog)
                dialog.destroy()
                return "break"

            dialog.protocol("WM_DELETE_WINDOW", close)

            source_desc_var = tk.StringVar(
                value=self.format_protocol_doc_source_desc(doc)
            )

            tk.Label(
                dialog,
                textvariable=source_desc_var,
                bg=THEME["bg"],
                fg=THEME["fg_dim"],
                font=THEME["font_main"],
                anchor="w",
                justify="left",
                wraplength=930,
            ).pack(fill="x", padx=16, pady=(12, 4))

            body = styled_frame(dialog)
            body.pack(fill="both", expand=True, padx=16, pady=(0, 8))

            protocol_text = create_managed_text_box(
                parent=body,
                label_text="协议规范内容",
                initial_value=doc.content,
                height=30,
                mono=True,
                wrap="word",
                readonly=True,
                enable_favorites=False,
                enable_clear=False,
                enable_wrap_toggle=True,
                config_data=self.config_data,
                save_config_func=self.save_config,
                wrap_config_key="patch.protocol_doc_text",
            )

            bottom = styled_frame(dialog)
            bottom.pack(fill="x", padx=16, pady=(4, 14))

            if is_protocol_doc_update_available():
                styled_button(
                    bottom,
                    "从 Markdown 更新",
                    lambda: self.update_protocol_doc_from_markdown(
                        dialog,
                        protocol_text,
                        source_desc_var,
                    ),
                    width=16,
                ).pack(side="left")

            styled_button(
                bottom,
                "关闭",
                close,
                width=10,
            ).pack(side="right")

            register_popup(dialog, close)

        except Exception as e:
            safe_show_error(
                "协议规范打开失败",
                f"无法打开内置修改包协议规范：\n\n错误：{e}",
                parent=self.root,
            )

    def format_protocol_doc_source_desc(self, doc):
        source_desc = "当前显示：内置修改包协议规范"

        if doc.updated_at:
            source_desc += f"    更新时间：{doc.updated_at}"

        if doc.source_sha256:
            source_desc += f"    指纹：{doc.source_sha256[:12]}..."

        return source_desc

    def choose_protocol_doc_source_path(self, parent):
        """
        手动选择 Markdown 维护源文档。

        路径采用绝对路径，并持久化保存最后一次选择位置。
        程序运行时不会自动读取该 md，只在用户点击更新时使用。
        """
        current_path = self.cfg.get("protocol_doc_source_path", "")
        initial_dir = get_initial_dir_from_path(current_path) or str(get_app_dir())

        file_path = filedialog.askopenfilename(
            title="选择修改包协议规范 Markdown 源文档",
            initialdir=initial_dir,
            initialfile=Path(current_path).name if current_path else "Zipatch_V2_修改包协议规范.md",
            filetypes=[
                ("Markdown Documents", "*.md"),
                ("All Files", "*.*"),
            ],
            parent=parent,
        )

        if not file_path:
            return ""

        resolved = str(Path(file_path).expanduser().resolve())
        self.cfg["protocol_doc_source_path"] = resolved
        self.save_config()

        return resolved

    def update_protocol_doc_from_markdown(self, dialog, protocol_text, source_desc_var):
        """
        开发模式维护功能：
        选择 md -> 比对当前内置文档 -> 用户确认 -> 写入内置 py 文档。
        """
        try:
            if is_frozen_app():
                safe_show_error(
                    "当前环境不支持更新",
                    "当前程序为已封装版本，内置协议文档已写入 exe 内部，无法在运行时修改。\n\n"
                    "如需更新，请在源码开发环境中同步 Markdown 后重新封装。",
                    parent=dialog,
                )
                return

            source_path = self.choose_protocol_doc_source_path(dialog)

            if not source_path:
                return

            preview = preview_protocol_doc_update(source_path)

            if not preview.has_difference:
                safe_show_info(
                    "无需更新",
                    "所选 Markdown 文档与当前内置协议文档内容一致，无需更新。\n\n"
                    f"文档路径：\n{preview.source_path}\n\n"
                    f"编码：{preview.source_encoding}\n"
                    f"字符数：{preview.source_length}\n"
                    f"指纹：{preview.source_sha256}",
                    parent=dialog,
                )
                return

            message = (
                "所选 Markdown 文档与当前内置协议文档不一致。\n\n"
                "当前内置文档：\n"
                f"- 字符数：{preview.embedded_length}\n"
                f"- 指纹：{preview.embedded_sha256}\n\n"
                "所选 Markdown：\n"
                f"- 路径：{preview.source_path}\n"
                f"- 编码：{preview.source_encoding}\n"
                f"- 字符数：{preview.source_length}\n"
                f"- 指纹：{preview.source_sha256}\n\n"
                "是否使用所选 Markdown 更新内置协议文档？\n\n"
                "更新后当前窗口会立即显示新内容；如需发布 exe，请重新封装。"
            )

            if not safe_ask_yes_no("发现协议文档差异", message, parent=dialog):
                return

            target_module = apply_protocol_doc_update(preview)
            refreshed_doc = get_patch_protocol_doc()

            protocol_text.configure(state="normal")
            set_text_value(protocol_text, refreshed_doc.content)
            protocol_text.configure(state="disabled")
            source_desc_var.set(self.format_protocol_doc_source_desc(refreshed_doc))

            self.log(f"内置修改包协议规范已更新：{target_module}")
            self.set_status("内置协议文档已更新")

            safe_show_info(
                "更新完成",
                "内置协议文档已更新。\n\n"
                f"源 Markdown：\n{preview.source_path}\n\n"
                f"目标模块：\n{target_module}\n\n"
                "当前窗口已刷新为新内容。\n如需发布，请重新封装 exe。",
                parent=dialog,
            )

        except Exception as e:
            safe_show_error(
                "更新失败",
                f"无法从 Markdown 更新内置协议文档：\n\n错误：{e}",
                parent=dialog,
            )

    def refresh_mode_ui(self):
        backup_dir_row = getattr(getattr(self, "backup_dir_entry", None), "_row_frame", None)
        restore_source_row = getattr(getattr(self, "restore_source_entry", None), "_row_frame", None)
        is_restore = self.patch_mode.get() == "restore"

        def show_path_row(row):
            if row is None or row.winfo_ismapped():
                return

            row.pack(
                fill="x",
                pady=4,
                before=self.mode_row,
            )

        def hide_path_row(row):
            if row is not None:
                row.pack_forget()

        def show_widget(widget):
            if widget is not None and not widget.winfo_ismapped():
                widget.pack(side="left", padx=(0, 24))

        def hide_widget(widget):
            if widget is not None:
                widget.pack_forget()

        if is_restore:
            self.mode_hint.config(text="当前为备份还原模式，请谨慎操作")
            self.preview_button.config(text="预演还原")
            self.apply_button.config(text="预演并还原")

            hide_path_row(backup_dir_row)
            show_path_row(restore_source_row)

            hide_widget(getattr(self, "allow_delete_check", None))
            hide_widget(getattr(self, "allow_multi_replace_check", None))
            show_widget(getattr(self, "backup_enabled_check", None))
            show_widget(getattr(self, "keep_restore_source_path_check", None))
            show_widget(getattr(self, "open_backup_after_done_check", None))

        else:
            self.mode_hint.config(text="")
            self.preview_button.config(text="Dry Run 预演")
            self.apply_button.config(text="预演并执行")

            hide_path_row(restore_source_row)
            show_path_row(backup_dir_row)

            show_widget(getattr(self, "allow_delete_check", None))
            show_widget(getattr(self, "allow_multi_replace_check", None))
            show_widget(getattr(self, "backup_enabled_check", None))
            hide_widget(getattr(self, "keep_restore_source_path_check", None))
            show_widget(getattr(self, "open_backup_after_done_check", None))

        self.refresh_apply_button_style(is_restore)

    def refresh_apply_button_style(self, is_restore):
        """
        执行按钮颜色表达实际执行风险：
        - 执行修改：橙色；
        - 备份还原：红色。
        """
        if is_restore:
            bg = THEME["danger"]
        else:
            bg = THEME["warning"]

        self.apply_button.config(
            bg=bg,
            fg=THEME["fg_on_dark"],
            activebackground=bg,
            activeforeground=THEME["fg_on_dark"],
        )
        self.apply_button._normal_bg = bg
        self.apply_button._normal_fg = THEME["fg_on_dark"]

    def resolve_project_relative_initial_dir(self, value):
        """
        解析项目相对路径输入框的浏览初始目录。

        规则：
        - 项目根目录有效时，相对路径以项目根目录为基准；
        - 输入为空时，从项目根目录打开；
        - 输入为绝对路径时，直接使用该路径；
        - 目标目录存在时，从目标目录打开；
        - 目标目录不存在但父目录存在时，从父目录打开；
        - 路径非法或父目录无效时，回退项目根目录；
        - 项目根目录无效时，返回 None，交给系统默认位置。
        """
        project_root_text = self.project_root.get().strip()

        try:
            project_root = Path(project_root_text).expanduser().resolve()
        except Exception:
            return None

        if not project_root.is_dir():
            return None

        value = (value or "").strip()

        if not value:
            return str(project_root)

        try:
            target = Path(value).expanduser()

            if not target.is_absolute():
                target = project_root / target

            target = target.resolve()

            if target.is_dir():
                return str(target)

            if target.parent.is_dir():
                return str(target.parent)

        except Exception:
            pass

        return str(project_root)

    def browse_project_relative_folder(self, var, title):
        browse_folder(
            var,
            title=title,
            initial_dir=self.resolve_project_relative_initial_dir(var.get()),
        )

    def browse_backup_dir(self):
        self.browse_project_relative_folder(
            self.backup_dir,
            "选择备份保存目录",
        )

    def browse_restore_source_dir(self):
        self.browse_project_relative_folder(
            self.restore_source_dir,
            "选择用于还原的历史备份目录",
        )

    def resolve_configured_backup_dir(self):
        """
        解析“备份目录”输入框中的路径。

        规则：
        - 绝对路径：直接打开该绝对路径；
        - 相对路径：按当前修改包页面的“项目根目录”拼接；
        - 不使用 Python 进程工作目录解析相对路径，避免打开到其他项目。
        """
        return str(
            resolve_backup_base_dir(
                self.project_root.get().strip(),
                self.backup_dir.get().strip(),
            )
        )

    def open_configured_backup_dir(self):
        try:
            open_path_with_default_app(
                self.resolve_configured_backup_dir(),
                self.root,
            )
        except Exception as e:
            safe_show_error("打开失败", str(e), parent=self.root)

    def collect_config(self):
        self.cfg["project_root"] = self.project_root.get().strip()
        self.cfg["patch_mode"] = self.patch_mode.get()
        self.cfg["allow_delete"] = self.allow_delete.get()
        self.cfg["allow_multi_replace_exact"] = self.allow_multi_replace_exact.get()
        self.cfg["backup_enabled"] = self.backup_enabled.get()
        self.cfg["backup_dir"] = self.backup_dir.get().strip()
        self.cfg["restore_source_dir"] = self.restore_source_dir.get().strip()
        self.cfg["keep_restore_source_path"] = self.keep_restore_source_path.get()
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
        if self.preview_snapshot is None:
            raise ValueError("请先执行 Dry Run，并确保存在可执行内容")

        if cfg.get("patch_mode", "apply") == "restore":
            if self.preview_restore_manifest is None:
                raise ValueError("请先执行备份还原预演，并确保存在可还原内容。")
        elif self.preview_apply_patch is None:
            raise ValueError("请先执行 Dry Run，并确保存在可执行内容")

        current_snapshot = self.make_current_snapshot(cfg)

        if current_snapshot != self.preview_snapshot:
            self.preview_apply_patch = None
            self.preview_restore_manifest = None
            self.preview_snapshot = None
            self.last_preview_text = ""
            raise ValueError(
                "当前项目根目录、执行模式、选项或修改包内容已发生变化。\n\n"
                "为避免执行未经校验的内容，请重新点击【Dry Run 预演】，确认通过后再执行。"
            )

    def write_result(self, text):
        self.result_text.configure(state="normal")
        replace_text_preserve_view(self.result_text, text)
        self.result_text.configure(state="disabled")
        self.cfg["last_result_text"] = text
        self.save_config()

    def mode_display_text(self):
        if self.patch_mode.get() == "restore":
            return "备份还原"
        return "执行修改"

    def extract_preview_failure_summary(self, preview_text, max_items=3):
        """
        从 Dry Run 结果文本中提取失败原因摘要，用于弹窗快速提示。

        完整详情仍以右侧结果区为准；弹窗只承担“当前阶段关键错误不丢失”的提示职责。
        """
        reasons = []
        lines = (preview_text or "").splitlines()

        for index, line in enumerate(lines):
            if line.strip() != "失败原因：":
                continue

            collected = []

            for item in lines[index + 1:]:
                value = item.strip()

                if not value:
                    if collected:
                        break
                    continue

                if value in ("修改包定位：", "请在修改包中搜索：", "请在修改包中搜索以下 OP 头："):
                    break

                collected.append(value)

            if collected:
                reasons.append(" ".join(collected))

            if len(reasons) >= max_items:
                break

        if not reasons:
            return "请查看右侧结果区中的校验失败详情。"

        return "\n".join(
            f"{index}. {reason}"
            for index, reason in enumerate(reasons, 1)
        )

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

    def build_apply_operation_summary(self, cfg):
        """
        执行前确认摘要。

        规则：
        - 优先按处理修改类型分组；
        - 同一类别下同一文件只显示一次；
        - 同一文件跨类别出现时，允许分别显示；
        - 最高风险是“未开启自动备份 + 修改/删除已有文件”。
        """
        project_root = Path(cfg["project_root"]).resolve()
        groups = [
            ("create", "新增文件"),
            ("create_dir", "创建目录"),
            ("overwrite", "覆盖已有路径"),
            ("append_text", "追加文本"),
            ("replace_exact", "精确替换"),
            ("replace_between", "锚点区间替换"),
            ("rename_file", "文件改名"),
            ("move_file", "移动文件"),
            ("copy_file", "复制文件"),
            ("rename_dir", "目录改名"),
            ("move_dir", "移动目录"),
            ("copy_dir", "复制目录"),
            ("delete_file", "删除文件"),
            ("delete_dir", "删除文件夹"),
            ("skip", "跳过写入"),
        ]

        files_by_group = {key: [] for key, _ in groups}
        seen_by_group = {key: set() for key, _ in groups}
        operations_by_group = {key: 0 for key, _ in groups}
        replace_exact_total_count = 0
        existing_file_groups = {
            "overwrite",
            "append_text",
            "replace_exact",
            "replace_between",
            "rename_file",
            "move_file",
            "copy_file",
            "rename_dir",
            "move_dir",
            "copy_dir",
            "delete_file",
            "delete_dir",
        }
        
        for op in self.preview_apply_patch.get("operations", []):
            op_type = op.get("op", "")
            rel_path = str(op.get("path", "")).replace("\\", "/").strip()

            if not rel_path:
                continue

            display_path = rel_path
            target = project_root / rel_path

            if op_type == "write_file":
                if target.exists() and op.get("if_exists", "error") == "skip":
                    group_key = "skip"
                elif target.exists():
                    group_key = "overwrite"
                else:
                    group_key = "create"
            elif op_type in (
                "rename_file",
                "move_file",
                "copy_file",
                "rename_dir",
                "move_dir",
                "copy_dir",
            ) and op.get("new_path"):
                new_rel_path = str(op.get("new_path", "")).replace("\\", "/").strip()
                new_target = project_root / new_rel_path
                display_path = f"{rel_path} -> {new_rel_path}"

                if new_target.exists() and op.get("if_exists", "error") == "skip":
                    group_key = "skip"
                elif new_target.exists():
                    group_key = "overwrite"
                else:
                    group_key = op_type
            else:
                group_key = op_type

            if group_key not in files_by_group:
                continue

            operations_by_group[group_key] += 1

            if group_key == "replace_exact":
                replace_exact_total_count += int(str(op["count"]).strip())

            if display_path in seen_by_group[group_key]:
                continue

            seen_by_group[group_key].add(display_path)
            files_by_group[group_key].append(display_path)

        counts = {
            key: len(files_by_group[key])
            for key, _ in groups
        }

        affects_existing_files = any(
            counts[key] > 0
            for key in existing_file_groups
        )

        has_delete = counts["delete_file"] > 0 or counts["delete_dir"] > 0
        high_risk = not cfg.get("backup_enabled", True) and affects_existing_files

        return {
            "groups": groups,
            "files_by_group": files_by_group,
            "counts": counts,
            "operations_by_group": operations_by_group,
            "replace_exact_total_count": replace_exact_total_count,
            "affects_existing_files": affects_existing_files,
            "has_delete": has_delete,
            "high_risk": high_risk,
        }

    def format_grouped_file_list(self, summary, only_existing_risk=False, include_empty=False):
        existing_risk_groups = {
            "overwrite",
            "append_text",
            "replace_exact",
            "replace_between",
            "rename_file",
            "move_file",
            "copy_file",
            "rename_dir",
            "move_dir",
            "copy_dir",
            "delete_file",
            "delete_dir",
        }

        lines = []

        for key, title in summary["groups"]:
            if only_existing_risk and key not in existing_risk_groups:
                continue

            files = summary["files_by_group"].get(key, [])

            if not files and not include_empty:
                continue

            op_count = summary.get("operations_by_group", {}).get(key, len(files))

            if key == "replace_exact":
                replace_count = summary.get("replace_exact_total_count", op_count)
                lines.append(f"{title}：OP {op_count} 个，文本替换 {replace_count} 处，涉及 {len(files)} 个文件")
            else:
                lines.append(f"{title}：OP {op_count} 个，涉及 {len(files)} 个文件")

            for path in files:
                lines.append(f"- {path}")

            lines.append("")

        return "\n".join(lines).rstrip()

    def format_restore_risk_summary(self):
        summary = self.restore_risk_summary or {}
        lines = []

        groups = [
            ("mismatch_paths", "状态漂移项"),
            ("delete_paths", "将删除当前存在路径"),
            ("overwrite_paths", "将覆盖当前路径"),
            ("restore_dirs", "涉及目录树还原"),
        ]

        for key, title in groups:
            paths = summary.get(key, [])

            if not paths:
                continue

            lines.append(f"{title}：{len(paths)} 项")

            for path in paths:
                lines.append(f"- {path}")

            lines.append("")

        return "\n".join(lines).rstrip() or "未检测到额外高风险项。"

    def confirm_restore_execution(self, cfg):
        risk_text = self.format_restore_risk_summary()
        has_risk = bool((self.restore_risk_summary or {}).get("has_risk", False))

        lines = [
            "即将根据备份来源执行文件层级还原。",
            "",
            "还原前程序会按“自动备份”设置备份当前状态。",
            "",
            "风险清单：",
            "",
            risk_text,
            "",
        ]

        if has_risk:
            lines.extend([
                "检测到高风险项。若继续执行，当前路径可能会被覆盖或删除。",
                "本次确认只对当前这一次还原生效，不会保存为长期强制开关。",
                "",
            ])

        if not cfg.get("keep_restore_source_path", False):
            lines.extend([
                "还原成功后会清空文本框中的备份来源路径，但不会删除任何实际备份文件。",
                "",
            ])

        lines.append("确认执行还原？")

        return safe_ask_risk_confirm(
            "执行还原确认" if not has_risk else "高风险还原确认",
            "\n".join(lines),
            parent=self.root,
            danger=has_risk,
        )

    def confirm_apply_execution(self, cfg):
        summary = self.build_apply_operation_summary(cfg)

        if summary["high_risk"]:
            message = (
                "高风险：当前未开启自动备份。\n\n"
                "本次将修改或删除已有文件。执行后，工具无法帮你自动还原这些文件到执行前状态。\n\n"
                "受影响文件清单：\n\n"
                f"{self.format_grouped_file_list(summary, only_existing_risk=True)}\n\n"
                "确认继续执行？"
            )

            return safe_ask_risk_confirm(
                "高风险确认：未启用自动备份",
                message,
                parent=self.root,
                danger=True,
            )

        lines = [
            "即将执行文件修改。",
            "",
        ]

        if cfg.get("backup_enabled", True):
            lines.extend([
                "自动备份已开启。执行前会备份被修改/删除的已有文件，可通过备份还原回退。",
                "",
            ])
        else:
            lines.extend([
                "当前未开启自动备份。",
                "本次未检测到会被修改/删除的已有文件，主要风险较低。",
                "",
            ])

        lines.extend([
            "本次操作清单：",
            "",
            self.format_grouped_file_list(summary),
            "",
            "确认执行？",
        ])

        return safe_ask_risk_confirm(
            "执行确认",
            "\n".join(lines),
            parent=self.root,
            danger=False,
        )

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

    def preview(self, for_execution=False):
        """
        执行当前模式的 Dry Run。

        for_execution=False：
        - 作为独立预演按钮使用；
        - 预演成功后显示原有成功提示；
        - 不继续执行。

        for_execution=True：
        - 作为“预演并执行/还原”的前置安全校验；
        - 预演失败时显示与独立预演完全相同的错误提示并停止；
        - 预演完全通过时不显示额外成功提示，直接进入原执行确认流程。

        返回值：
        - True：预演完成且允许进入执行确认；
        - False：预演失败或存在校验错误，必须停止。
        """
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
                )

                self.preview_apply_patch = None
                self.preview_restore_manifest = result.manifest
                self.preview_snapshot = self.make_current_snapshot(cfg)
                self.last_preview_text = result.preview_text
                self.restore_risk_summary = result.risk_summary
                self.preview_has_errors = result.has_mismatch
                self.preview_success_count = 0
                self.preview_failed_count = 0
                self.preview_has_global_errors = False
                self.append_result("Dry Run 预演", result.preview_text)

                self.set_status("备份还原预演完成")
                self.log("备份还原 Dry Run 完成")

                if not for_execution:
                    safe_show_info(
                        "Dry Run 完成",
                        "备份还原校验完成，请查看预演结果。",
                        parent=self.root,
                    )

                return True

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

            self.preview_apply_patch = result.valid_patch
            self.preview_restore_manifest = None
            self.preview_snapshot = self.make_current_snapshot(cfg)
            self.last_preview_text = result.preview_text
            self.preview_has_errors = result.has_errors
            self.preview_success_count = result.success_count
            self.preview_failed_count = result.failed_count
            self.preview_has_global_errors = result.has_global_errors
            self.append_result("Dry Run 预演", result.preview_text)

            self.set_status("Dry Run 预演完成")
            self.log("修改包 Dry Run 预演完成")

            if result.failed_count == 0:
                if not for_execution:
                    safe_show_info(
                        "Dry Run 完成",
                        "V2 修改包全部校验通过。请查看预演结果，确认无误后再执行。",
                        parent=self.root,
                    )

                return True

            safe_show_error(
                "Dry Run 存在失败项",
                "修改包存在校验失败项，请先查看并修正。\n\n"
                f"校验成功：{result.success_count}\n"
                f"校验失败：{result.failed_count}\n"
                f"全局冲突：{'有' if result.has_global_errors else '无'}\n\n"
                "失败原因摘要：\n"
                f"{self.extract_preview_failure_summary(result.preview_text)}\n\n"
                "完整详情请查看右侧结果区。",
                parent=self.root,
            )
            return False

        except Exception as e:
            self.preview_apply_patch = None
            self.preview_restore_manifest = None
            self.preview_snapshot = None
            self.last_preview_text = ""
            self.restore_risk_summary = None
            self.preview_has_global_errors = False

            err = "【Dry Run 失败】\n\n" + str(e) + "\n\n" + traceback.format_exc()
            self.append_result("Dry Run 失败", err)

            self.set_status("Dry Run 失败")
            self.log(f"修改包 Dry Run 失败：{e}")
            safe_show_error("Dry Run 失败", str(e), parent=self.root)
            return False

    def apply(self):
        if not self.preview(for_execution=True):
            return

        try:
            cfg = self.collect_config()

            project_root = validate_required_path(cfg["project_root"], "项目根目录")
            self.ensure_preview_snapshot_still_valid(cfg)

            if cfg.get("patch_mode", "apply") == "restore":
                if not self.confirm_restore_execution(cfg):
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
                    risk_confirmed=True,
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

            if self.preview_has_global_errors:
                raise ValueError(
                    "当前 Dry Run 存在路径互斥或内容互斥等全局冲突，不能执行任何 OP。\n\n"
                    "请先修正修改包并重新 Dry Run。"
                )

            if self.preview_has_errors:
                raise ValueError(
                    "当前 Dry Run 存在校验失败项，不能执行任何 OP。\n\n"
                    "请先修正修改包，再重新点击【预演并执行】。"
                )

            if not self.confirm_apply_execution(cfg):
                return

            self.set_status("正在执行修改包...")
            self.log("用户启动：执行修改包")

            result = apply_patch(
                project_root=project_root,
                patch=self.preview_apply_patch,
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
