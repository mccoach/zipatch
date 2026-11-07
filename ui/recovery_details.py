# -*- coding: utf-8 -*-

"""只读恢复详情窗口；查看快照和派生视图只存活于本窗口。"""

import tkinter as tk
from tkinter import ttk

from core.constants import THEME
from ui.message_dialogs import safe_show_error
from ui.widgets import open_path_with_default_app
from ui.window_manager import center_window
from services.recovery_comparison import RESTORE_STATUS_LABELS
from services.recovery_details import (
    build_restore_detail_scope, check_detail_task, format_file_detail,
    format_text_difference, read_restore_file_detail,
)
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, take_modal_control, unregister_popup


class RestoreDetailsSession:
    """只展示固定依据，不接受决定、不准备或确认工作任务。"""

    def __init__(self, parent, preview, identifier):
        self.preview = preview
        self.scope = build_restore_detail_scope(preview, identifier)
        self.entries = {
            f"d{number}": entry
            for number, entry in enumerate(self.scope.entries)
        }
        self.loaded_files = {}
        self.views = {}
        self.destroyed = False
        self.dialog = tk.Toplevel(parent)
        self.dialog.withdraw()
        self.dialog.title("备份还原：文件详情与差异查看")
        self.dialog.configure(bg=THEME["bg"])
        self.dialog.transient(parent)
        self.dialog._restore_details_session = self
        self.dialog.bind("<Destroy>", self.on_destroy, add="+")

        header = styled_frame(self.dialog)
        header.pack(fill="x", padx=14, pady=(12, 6))
        introduction = tk.Label(
            header,
            text=(
                f"对象：{self.scope.object_id}；{self.scope.target}\n"
                f"固定分类：{RESTORE_STATUS_LABELS[self.scope.classification]}\n"
                "结构来自本次预演；文件按需读取并核验。查看不接受决定或执行恢复。\n"
                "文本差异比较原材料与固定当前文件；预期仅有指纹，没有预期正文。\n"
                "已读取内容是本窗口查看快照，切换视图不刷新现场，关闭后释放。"
            ),
            bg=THEME["bg"], anchor="w", justify="left",
            width=1, wraplength=1,
        )
        introduction.pack(fill="x")
        introduction.bind(
            "<Configure>",
            lambda event: introduction.configure(wraplength=max(1, event.width)),
        )

        controls = styled_frame(self.dialog)
        controls.pack(side="bottom", fill="x", padx=14, pady=12)
        styled_button(
            controls, "关闭详情", self.close, width=12,
        ).pack(side="right")
        self.bytes_button = styled_button(
            controls, "文件大小与指纹",
            lambda: self.show_file(False), width=18,
        )
        self.bytes_button.pack(side="left")
        self.text_button = styled_button(
            controls, "尝试严格文本差异",
            lambda: self.show_file(True), width=20,
        )
        self.text_button.pack(side="left", padx=6)
        styled_button(
            controls, "复制实际路径", self.copy_path, width=16,
        ).pack(side="left")
        styled_button(
            controls, "打开所在目录", self.open_parent, width=16,
        ).pack(side="left", padx=6)

        body = styled_frame(self.dialog)
        body.pack(fill="both", expand=True, padx=14)
        table_frame = styled_frame(body)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(
            table_frame,
            columns=("position", "original", "expected", "current"),
            show="headings", selectmode="browse", height=8,
        )
        for column, title, width in (
            ("position", "相对位置（完整固定范围）", 480),
            ("original", "原状态", 140),
            ("expected", "操作后预期", 140),
            ("current", "固定当前", 140),
        ):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=100)
        vertical = tk.Scrollbar(table_frame, command=self.table.yview)
        horizontal = tk.Scrollbar(
            table_frame, orient="horizontal", command=self.table.xview,
        )
        self.table.configure(
            yscrollcommand=vertical.set, xscrollcommand=horizontal.set,
        )
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        for row, entry in self.entries.items():
            self.table.insert("", "end", iid=row, values=(
                entry.relative or "（对象根自身）",
                entry.original_kind, entry.expected_kind, entry.current_kind,
            ))
        self.table.bind("<<TreeviewSelect>>", self.show_structure)

        frame, self.content = styled_text_with_scrollbars(
            body, height=20, mono=True, wrap="none", readonly=True,
        )
        frame.pack(fill="both", expand=True, pady=(6, 0))
        self.bytes_button.configure(state="disabled")
        self.text_button.configure(state="disabled")
        self.set_text(
            "请选择位置查看结构；文件大小和文本差异在明确点击后读取。\n"
            "关闭详情只返回原选择窗口，不取消或确认恢复任务。"
        )
        center_window(self.dialog, 1080, 780)
        take_modal_control(self.dialog)
        register_popup(self.dialog, self.close, focus_on_register=False)
        self.dialog.deiconify()

    def selected_entry(self):
        selected = self.table.selection()
        return self.entries[selected[0]] if selected else None

    def set_text(self, content):
        self.content.configure(state="normal")
        self.content.delete("1.0", "end")
        self.content.insert("1.0", content)
        self.content.configure(state="disabled")

    def show_structure(self, event=None):
        if self.destroyed:
            return
        entry = self.selected_entry()
        if entry is None:
            return
        enabled = "normal" if entry.has_file_content else "disabled"
        self.bytes_button.configure(state=enabled)
        self.text_button.configure(state=enabled)
        lines = [
            f"实际位置：{entry.target}",
            f"原状态：{entry.original_kind}",
            f"操作后预期：{entry.expected_kind}",
            f"固定当前：{entry.current_kind}",
            f"原SHA-256：{entry.original_digest or '不适用'}",
            f"预期SHA-256：{entry.expected_digest or '不适用'}",
            f"当前SHA-256：{entry.current_digest or '不适用'}",
            f"原正式mtime_ns：{entry.original_mtime_ns}",
            f"当前固定mtime_ns：{entry.current_mtime_ns}",
            f"源备份：{self.scope.source}",
            "",
            "本对象相关的原任务正式执行事实：",
        ]
        from services.recovery_comparison import format_execution_fact
        for identifier, members, state, changes in self.scope.execution_facts:
            lines.append(f"共同成员：{', '.join(members)}")
            lines.extend(format_execution_fact(identifier, state, changes))
        lines.extend((
            "",
            "目录和缺失位置不冒充空文件。",
            "预期只有正式指纹，不提供虚构的预期正文或大小。",
            "文件大小由本窗口按需取得的已核验字节计算。",
        ))
        self.set_text("\n".join(lines))

    def show_file(self, text_difference):
        if self.destroyed:
            return
        entry = self.selected_entry()
        if entry is None:
            return
        try:
            check_detail_task(self.preview)
            key = (entry.relative, text_difference)
            content = self.views.get(key)
            if content is None:
                detail = self.loaded_files.get(entry.relative)
                if detail is None:
                    detail = read_restore_file_detail(
                        self.preview, self.scope, entry.relative,
                    )
                    self.loaded_files[entry.relative] = detail
                content = (
                    format_text_difference(detail)
                    if text_difference else format_file_detail(detail)
                )
                self.views[key] = content
        except (OSError, ValueError) as error:
            self.set_text(
                "本次查看未完成，不能据此认定无差异。\n\n" + str(error)
            )
            safe_show_error("文件详情查看未完成", str(error), self.dialog)
            return
        self.set_text(content)

    def copy_path(self):
        entry = self.selected_entry()
        if entry is not None:
            self.dialog.clipboard_clear()
            self.dialog.clipboard_append(str(entry.target))

    def open_parent(self):
        entry = self.selected_entry()
        if entry is not None:
            open_path_with_default_app(entry.target.parent, self.dialog)

    def close(self):
        if not self.destroyed:
            unregister_popup(self.dialog)
            self.dialog.destroy()
        return True

    def on_destroy(self, event):
        if event.widget is self.dialog:
            self.destroyed = True
            self.loaded_files.clear()
            self.views.clear()


def show_restore_details(parent, preview, identifier):
    """只读模态查看归还控制；不作废、准备或确认恢复任务。"""
    session = None
    try:
        session = RestoreDetailsSession(parent, preview, identifier)
        parent.wait_window(session.dialog)
    finally:
        if session is not None and not session.destroyed:
            session.close()
