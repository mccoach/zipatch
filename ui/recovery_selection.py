# -*- coding: utf-8 -*-

"""恢复对象及固定子项决定、完整影响与一次最终确认，不执行工作恢复。"""

import tkinter as tk
from tkinter import ttk

from core.constants import THEME
from ui.message_dialogs import safe_show_error
from ui.widgets import open_path_with_default_app
from ui.window_manager import center_window
from services.patch_service import (
    choose_backup_restore, choose_backup_restore_subitems,
    confirm_backup_restore, invalidate_preview,
    prepare_backup_restore, return_backup_restore_to_selection,
)
from services.recovery_comparison import (
    RESTORE_STATUS_LABELS, format_restore_comparisons,
)
from services.recovery_selection import RESTORE_DECISION_LABELS
from services.recovery_subitems import (
    SUBITEM_DECISION_LABELS, SUBITEM_STATUS_LABELS,
    change_subitem_decisions, initial_subitem_decisions,
    selected_cleanup_plan,
)
from ui.recovery_details import show_restore_details
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, take_modal_control, unregister_popup


KIND_LABELS = {
    "absent": "不存在", "file": "普通文件", "directory": "普通目录",
}


def restore_action_label(action):
    """解释固定原状态及当前类型，不读取目标。"""
    if not action.comparison.requires_restore:
        return "无需操作"
    original = action.payload["original"]["kind"]
    current = action.current.kind
    if original == "absent":
        return "删除新增目录及全部内容" if current == "directory" else "删除新增对象"
    if original == "directory":
        return "恢复重建原目录树" if current == "absent" else "整体替换为原目录树"
    if current == "absent":
        return "恢复重建旧文件"
    if current == "directory":
        return "移除当前目录并恢复旧文件"
    return "恢复旧文件及正式时间"


def restore_object_detail(action):
    """展示同次固定依据；整体替换与选择性清理的后果明确区分。"""
    current = action.current
    original = action.payload["original"]
    comparison = action.comparison
    lines = [
        f"对象编号：{action.payload['id']}",
        f"实际绝对位置：{current.path}",
        f"原状态：{KIND_LABELS[original['kind']]}",
        f"原任务预期：{KIND_LABELS[comparison.expected_kind]}",
        f"当前状态：{KIND_LABELS[current.kind]}",
        f"分类：{RESTORE_STATUS_LABELS[comparison.status]}",
        f"选择整体恢复时的动作：{restore_action_label(action)}",
        "选择整体保留不改变本对象，仍检查必要当前状态。",
        "",
    ]
    lines.extend(format_restore_comparisons(((str(current.path), comparison),)))
    lines.extend(("", "当前固定内容明细（文件显示SHA-256）："))
    for relative, kind, digest in current.content_records:
        location = current.path / relative if relative else current.path
        lines.append(
            f"- [{KIND_LABELS[kind]}] {location}"
            + (f"；SHA-256={digest}" if kind == "file" else "")
        )
    if not current.content_records:
        lines.append("当前对象不存在。")
    if current.kind == "directory" and comparison.requires_restore:
        if action.subitem_scope is not None:
            lines.extend((
                "",
                "本对象支持固定子项清理／保留，也可明确选择整体恢复或整体保留。",
                "整体恢复将删除全部当前内容，包括隐藏名称和空目录。",
                "子项清理不叠加整体删除；目录只在为空且满足固定方案时移除。",
                "保留子项的必要祖先目录自动留下；不自动合并或搬迁保留内容。",
                "保留目录自身不等于保留其全部后代，后代需要分别决定。",
            ))
        else:
            lines.extend((
                "",
                "目录只能整体恢复或整体保留。",
                "恢复将移除上面完整当前目录范围，包括隐藏名称和空目录；"
                "不提供子项选择，不自动合并或搬迁保留内容。",
                "关联约束：整体替换不能同时保留该范围内的后代。",
            ))
    lines.extend(("", "正式原内容明细："))
    for relative, kind, digest in original["records"]:
        location = current.path / relative if relative else current.path
        lines.append(
            f"- [{KIND_LABELS[kind]}] {location}"
            + (f"；SHA-256={digest}" if kind == "file" else "")
        )
    if original["kind"] == "absent":
        lines.append("原状态不存在；只有全部必要清理完成且无保留结果时才满足原状态。")
    lines.extend(("", "正式原文件时间："))
    lines.extend(
        f"- {current.path / relative if relative else current.path}；mtime_ns={modified}"
        for relative, modified in action.payload["file_times"]
    )
    return "\n".join(lines)


def subitem_decision_detail(scope, previous, candidate):
    """草稿完整展示仅消费固定范围；不发布材料或取得执行资格。"""
    resets = sum(
        previous[relative] in ("restore", "keep")
        and previous[relative] != decision
        for relative, decision in candidate.items()
    )
    lines = [
        "【待接受的子项决定，不是工作删除执行】",
        f"对象编号：{scope.object_id}",
        f"固定目录：{scope.target}",
        f"将重设已有相反子项决定：{resets}",
        "接受后仍可调整；全部决定完成后另行核对一次最终方案。",
        "清理目录只表示移除满足条件的空目录，不授权递归删除后代。",
        "保留目录自身不等于保留全部后代；保留后代会使必要祖先留下。",
        "",
        "【全部固定子项及候选决定】",
    ]
    for item in scope.items:
        lines.append(
            f"- {item.path}；当前={KIND_LABELS[item.current_kind]}；"
            f"预期={KIND_LABELS[item.expected_kind]}；"
            f"{SUBITEM_STATUS_LABELS[item.status]}；"
            f"原决定={SUBITEM_DECISION_LABELS[previous[item.relative]]}；"
            f"候选决定={SUBITEM_DECISION_LABELS[candidate[item.relative]]}"
        )
        if item.current_kind == "file":
            lines.append(f"  SHA-256={item.current_digest}")
        from services.recovery_comparison import format_execution_fact
        for identifier, state, changes in item.uncertain_units:
            lines.append("  原任务事实不足：")
            lines.extend(
                "  " + line
                for line in format_execution_fact(identifier, state, changes)
            )
    lines.extend(("", "【固定当前文件时间】"))
    lines.extend(
        f"- {scope.target / relative if relative else scope.target}；mtime_ns={modified}"
        for relative, modified in scope.current_file_times
    )
    if any(value == "undecided" for value in candidate.values()):
        lines.extend((
            "", "仍有子项未决定，不能形成最终清理方案或取得工作执行资格。",
            "目录是否实际移除，须待全部决定收齐后按保留祖先规则计算。",
        ))
    else:
        cleanup = selected_cleanup_plan(scope, candidate)
        lines.extend(("", "【候选实际删除范围】"))
        lines.extend(
            f"- {'删除文件' if step.removal == 'file' else '仅移除空目录'}："
            f"{step.item.path}"
            for step in cleanup.steps
        )
        if not cleanup.steps:
            lines.append("无工作删除。")
        lines.extend(("", "【候选保留结果，包含必要祖先】"))
        lines.extend(
            f"- {scope.target / relative if relative else scope.target}"
            for relative, _, _ in cleanup.retained_records
        )
        if not cleanup.retained_records:
            lines.append("无。全部步骤完成后可满足原不存在状态。")
        else:
            lines.append("按此方案完成仍未完整恢复本对象原不存在状态。")
    lines.append(
        "\n还原前保护在最终方案收口后规划，只覆盖实际删除文件或完整被清理子树；"
        "不扩大到包含保留内容的父树和无关兄弟。"
    )
    return "\n".join(lines)


def batch_decision_detail(actions, decisions, decision):
    """完整批量影响来自同次固定动作，不重新观察或形成执行方案。"""
    changed = tuple(
        action for action in actions
        if decisions[action.payload["id"]] in ("restore", "keep", "subitems")
        and decisions[action.payload["id"]] != decision
    )
    lines = [
        "【待接受的对象决定，不是工作恢复执行】",
        f"对象数量：{len(actions)}",
        f"将设置为：{RESTORE_DECISION_LABELS[decision]}",
        f"将重设已有相反决定：{len(changed)}",
        "接受后仍可调整；全部决定完成后另行核对一次最终恢复方案。",
        "",
        "【本批实际对象与恢复后果】",
    ]
    for action in actions:
        identifier = action.payload["id"]
        lines.append(
            f"- {identifier}；{action.current.path}；"
            f"当前决定={RESTORE_DECISION_LABELS[decisions[identifier]]}；"
            f"本批决定={RESTORE_DECISION_LABELS[decision]}；"
            f"选择恢复的后果={restore_action_label(action)}"
        )
    lines.extend((
        "", "【全部固定详情与目录影响】",
        "\n\n".join(restore_object_detail(action) for action in actions),
    ))
    return "\n".join(lines)


class RestoreSelectionSession:
    """执行器拥有正式决定；窗口只拥有未接受草稿和交互状态。"""

    def __init__(self, parent, preview, *, keep_source_path):
        self.preview = preview
        self.keep_source_path = keep_source_path
        self.accepted = False
        self.destroyed = False
        self.busy = False
        self.pending_decision = None
        self.pending_subitem_decision = None
        self.subitem_identifier = None
        self.subitem_rows = {}
        self.actions = {
            action.payload["id"]: action
            for action in preview.execution.selection_plan.actions
        }
        self.dialog = tk.Toplevel(parent)
        self.dialog.withdraw()
        self.dialog.title("备份还原：对象选择与最终确认")
        self.dialog.configure(bg=THEME["bg"])
        self.dialog.transient(parent)
        self.dialog._restore_selection_session = self
        self.dialog.bind("<Destroy>", self.on_destroy, add="+")
        self.status = tk.StringVar(master=self.dialog)
        self.loss_acknowledged = tk.BooleanVar(master=self.dialog, value=False)

        header = styled_frame(self.dialog)
        header.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(
            header,
            text="先决定恢复或保留，再核对一次最终汇总。预演尚未修改工作目标。\n"
                 "原不存在目录可按固定子项清理／保留；其他对象整体处理。\n"
                 "整体与子项方案互斥，切换须明确接受重设；取消、Esc或标题关闭作废任务。",
            justify="left", anchor="w", bg=THEME["bg"],
        ).pack(fill="x")
        status_label = tk.Label(
            header, textvariable=self.status, justify="left", anchor="w",
            width=1, wraplength=1, bg=THEME["bg"], fg=THEME["fg_label"],
        )
        status_label.pack(fill="x", pady=(5, 0))
        status_label.bind(
            "<Configure>",
            lambda event: status_label.configure(wraplength=max(1, event.width)),
        )

        self.bottom = styled_frame(self.dialog)
        self.bottom.pack(side="bottom", fill="x", padx=14, pady=12)
        self.selection_page = styled_frame(self.dialog)
        self.selection_page.pack(fill="both", expand=True, padx=14)
        table_frame = styled_frame(self.selection_page)
        table_frame.pack(fill="both", expand=True)
        columns = ("identifier", "path", "classification", "action", "decision")
        self.table = ttk.Treeview(
            table_frame, columns=columns, show="headings",
            selectmode="extended", height=7,
        )
        for column, title, width in (
            ("identifier", "对象编号", 100),
            ("path", "实际绝对位置", 330),
            ("classification", "固定分类", 230),
            ("action", "选择恢复时的动作", 220),
            ("decision", "当前决定", 140),
        ):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=90, stretch=True)
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
        self.table.bind("<<TreeviewSelect>>", self.show_detail)

        controls = styled_frame(self.selection_page)
        controls.pack(fill="x", pady=5)
        self.single_restore = styled_button(
            controls, "查看此对象恢复决定",
            lambda: self.choose_single("restore"), width=28,
        )
        self.single_restore.pack(side="left")
        styled_button(
            controls, "查看此对象保留决定",
            lambda: self.choose_single("keep"), width=20,
        ).pack(side="left", padx=5)
        styled_button(
            controls, "复制路径", self.copy_path, width=10,
        ).pack(side="left", padx=5)
        styled_button(
            controls, "打开所在目录", self.open_parent, width=14,
        ).pack(side="left")
        self.subitem_button = styled_button(
            controls, "查看子项清理／保留", self.open_subitems, width=22,
        )
        self.subitem_button.pack(side="left", padx=(5, 0))
        self.subitem_button.configure(state="disabled")

        detail_controls = styled_frame(self.selection_page)
        detail_controls.pack(fill="x", pady=(0, 4))
        styled_button(
            detail_controls, "文件详情／差异",
            self.open_file_details, width=20,
        ).pack(side="left")
        tk.Label(
            detail_controls,
            text="只读查看原材料与固定当前状态，不接受决定或执行恢复。",
            bg=THEME["bg"], anchor="w",
        ).pack(side="left", padx=(8, 0))

        batches = styled_frame(self.selection_page)
        batches.pack(fill="x", pady=(0, 4))
        for scope, name in (
            ("selected", "选中"), ("remaining", "剩余未决定"), ("all", "全部"),
        ):
            group = styled_frame(batches)
            group.pack(side="left", padx=(0, 8))
            for decision, label in (("restore", "恢复"), ("keep", "保留")):
                styled_button(
                    group, name + label + "范围",
                    lambda selected=scope, value=decision:
                    self.choose_batch(selected, value),
                    width=16,
                ).pack(side="left", padx=(0, 3))

        self.build_subitem_controls()
        frame, self.detail = styled_text_with_scrollbars(
            self.selection_page, height=12, mono=True,
            wrap="none", readonly=True,
        )
        self.detail_frame = frame
        frame.pack(fill="both", expand=True, pady=(3, 5))
        self.decision_row = styled_frame(self.selection_page)
        self.accept_decision_button = styled_button(
            self.decision_row, "接受上面这批对象决定",
            self.accept_pending_decision, width=26,
        )
        self.accept_decision_button.pack(side="right")
        styled_button(
            self.decision_row, "放弃这批决定，返回查看",
            self.discard_pending_decision, width=26,
        ).pack(side="right", padx=(0, 8))

        self.final_page = styled_frame(self.dialog)
        frame, self.final_text = styled_text_with_scrollbars(
            self.final_page, height=25, mono=True,
            wrap="word", readonly=True,
        )
        frame.pack(fill="both", expand=True)
        self.risk_check = tk.Checkbutton(
            self.final_page,
            text="我明确确认：未启用还原前保护，上述当前内容可能永久丢失，无法保证找回。",
            variable=self.loss_acknowledged, command=self.refresh_final_button,
            bg=THEME["danger_bg"], fg=THEME["danger"], anchor="w",
            wraplength=900, justify="left",
        )
        self.cancel_button = styled_button(
            self.bottom, "取消本次还原", self.cancel, width=16,
        )
        self.cancel_button.pack(side="right", padx=(8, 0))
        self.next_button = styled_button(
            self.bottom, "核对最终方案", self.prepare_final, width=16,
        )
        self.next_button.pack(side="right")
        self.back_button = styled_button(
            self.bottom, "返回检查", self.return_to_selection, width=12,
        )
        self.confirm_button = styled_button(
            self.bottom, "确认按方案还原", self.confirm, width=18, danger=True,
        )
        self.refresh_selection()
        center_window(self.dialog, 1120, 800)
        take_modal_control(self.dialog)
        register_popup(
            self.dialog, self.cancel, exit_blocker=True,
            focus_on_register=False,
        )
        self.dialog.deiconify()
        self.cancel_button.focus_set()

    def set_text(self, widget, content):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def refresh_selection(self):
        for identifier, action in self.actions.items():
            values = (
                identifier, str(action.current.path),
                RESTORE_STATUS_LABELS[action.comparison.status],
                restore_action_label(action),
                RESTORE_DECISION_LABELS[self.preview.execution.decisions[identifier]],
            )
            if self.table.exists(identifier):
                self.table.item(identifier, values=values)
            else:
                self.table.insert("", "end", iid=identifier, values=values)
        summary = self.preview.risk_summary
        self.status.set(
            f"未决定 {summary['undecided_count']}；"
            f"已选恢复 {summary['selected_restore_count']}；"
            f"已选保留 {summary['selected_keep_count']}；"
            f"子项模式对象 {summary['selected_subitem_count']}；"
            f"其中未决定子项 {summary['subitem_undecided_count']}；"
            f"无需操作 {summary['no_operation_count']}。"
            f"还原前保护：{'开启' if summary['backup_enabled'] else '关闭'}；"
            f"目录设置：{summary['backup_directory_setting']}"
        )
        self.next_button.configure(
            state="disabled"
            if (
                summary["undecided_count"] or self.pending_decision is not None
                or self.pending_subitem_decision is not None
            )
            else "normal",
        )
        self.show_detail()

    def focused_id(self):
        identifier = self.table.focus()
        return identifier if identifier in self.table.selection() else ""

    def show_detail(self, event=None):
        if (
            self.pending_decision is not None
            or self.pending_subitem_decision is not None
        ):
            return
        identifier = self.focused_id()
        if self.subitem_identifier is not None and identifier != self.subitem_identifier:
            self.subitem_identifier = None
            self.subitem_frame.pack_forget()
        if not identifier:
            self.set_text(self.detail, "请选择对象查看固定差异和完整目录影响。")
            self.single_restore.configure(state="disabled")
            self.subitem_button.configure(state="disabled")
            return
        action = self.actions[identifier]
        self.set_text(self.detail, restore_object_detail(action))
        self.single_restore.configure(
            text=restore_action_label(action) + "：查看决定",
            state="normal" if action.comparison.requires_restore else "disabled",
        )
        self.subitem_button.configure(
            state="normal" if action.subitem_scope is not None else "disabled",
        )
        if self.subitem_identifier == identifier:
            self.refresh_subitems()

    def choose_single(self, decision):
        identifier = self.focused_id()
        if not identifier:
            safe_show_error("未选择对象", "请选择一个对象。", self.dialog)
            return
        self.stage_decision((identifier,), decision)

    def choose_batch(self, scope, decision):
        if scope not in ("selected", "remaining", "all"):
            raise ValueError("未知对象决定范围")
        decisions = self.preview.execution.decisions
        identifiers = tuple(
            identifier for identifier, action in self.actions.items()
            if action.comparison.requires_restore and (
                scope == "all"
                or scope == "remaining" and decisions[identifier] == "undecided"
                or scope == "selected" and identifier in self.table.selection()
            )
        )
        self.stage_decision(identifiers, decision)

    def stage_decision(self, identifiers, decision):
        if self.busy or self.destroyed or self.preview.execution.finalized:
            return
        if self.pending_subitem_decision is not None:
            safe_show_error(
                "子项草稿尚未处理", "请先接受或放弃当前子项草稿。", self.dialog,
            )
            return
        if decision not in ("restore", "keep"):
            raise ValueError("未知对象决定")
        if not identifiers:
            safe_show_error(
                "没有待设置对象", "本范围没有可设置恢复或保留的对象。", self.dialog,
            )
            return
        actions = tuple(self.actions[identifier] for identifier in identifiers)
        if any(not action.comparison.requires_restore for action in actions):
            safe_show_error("无需操作", "无需操作对象不能设置恢复或保留。", self.dialog)
            return
        self.pending_decision = (tuple(identifiers), decision)
        text = batch_decision_detail(
            actions, self.preview.execution.decisions, decision,
        )
        replaces_subitems = any(
            self.preview.execution.decisions[identifier] == "subitems"
            for identifier in identifiers
        )
        if replaces_subitems:
            text += (
                "\n\n【整体／子项关联约束】\n"
                "明确接受后，上述对象原子项方案将被删除，改为本批整体决定。\n"
                "整体恢复不保留其中后代；不会叠加执行两套方案。"
            )
        self.set_text(self.detail, text)
        self.decision_row.pack(fill="x", pady=(0, 5))
        self.accept_decision_button.configure(
            text="明确接受重设及这批决定"
            if any(
                self.preview.execution.decisions[identifier] in (
                    "restore", "keep", "subitems",
                )
                and self.preview.execution.decisions[identifier] != decision
                for identifier in identifiers
            ) else "接受上面这批对象决定",
        )
        self.next_button.configure(state="disabled")

    def accept_pending_decision(self):
        if self.busy or self.destroyed or self.pending_decision is None:
            return
        identifiers, decision = self.pending_decision
        replaces_subitems = any(
            self.preview.execution.decisions[identifier] == "subitems"
            for identifier in identifiers
        )
        choose_backup_restore(
            self.preview, identifiers, decision,
            replace_decisions=replaces_subitems,
        )
        self.pending_decision = None
        self.decision_row.pack_forget()
        self.refresh_selection()

    def discard_pending_decision(self):
        self.pending_decision = None
        self.decision_row.pack_forget()
        self.refresh_selection()

    def copy_path(self):
        identifier = self.focused_id()
        if identifier:
            self.dialog.clipboard_clear()
            self.dialog.clipboard_append(str(self.actions[identifier].current.path))

    def open_parent(self):
        identifier = self.focused_id()
        if identifier:
            open_path_with_default_app(
                str(self.actions[identifier].current.path.parent), self.dialog,
            )

    def open_file_details(self):
        """查看不处理决定草稿，不取得最终方案或工作资格。"""
        if self.busy or self.destroyed or self.preview.execution.finalized:
            return
        identifier = self.focused_id()
        if not identifier:
            safe_show_error("未选择对象", "请选择对象查看文件详情。", self.dialog)
            return
        try:
            show_restore_details(self.dialog, self.preview, identifier)
        except (OSError, ValueError) as error:
            safe_show_error("详情窗口未完成", str(error), self.dialog)

    def build_subitem_controls(self):
        """子项表只关联固定相对位置，不维护另一份正式决定。"""
        self.subitem_frame = styled_frame(self.selection_page)
        self.subitem_status = tk.StringVar(master=self.dialog)
        tk.Label(
            self.subitem_frame, textvariable=self.subitem_status,
            anchor="w", justify="left", bg=THEME["bg"], wraplength=1000,
        ).pack(fill="x")
        table_frame = styled_frame(self.subitem_frame)
        table_frame.pack(fill="both", expand=True)
        self.subitem_table = ttk.Treeview(
            table_frame,
            columns=("position", "kind", "classification", "decision"),
            show="headings", selectmode="extended", height=5,
        )
        for column, title, width in (
            ("position", "固定相对位置（含目录根）", 300),
            ("kind", "当前类型", 100),
            ("classification", "固定分类", 350),
            ("decision", "已接受决定", 160),
        ):
            self.subitem_table.heading(column, text=title)
            self.subitem_table.column(column, width=width, minwidth=80)
        vertical = tk.Scrollbar(table_frame, command=self.subitem_table.yview)
        horizontal = tk.Scrollbar(
            table_frame, orient="horizontal", command=self.subitem_table.xview,
        )
        self.subitem_table.configure(
            yscrollcommand=vertical.set, xscrollcommand=horizontal.set,
        )
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)
        self.subitem_table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        controls = styled_frame(self.subitem_frame)
        controls.pack(fill="x", pady=4)
        for selection, title in (
            ("selected", "选中子项"),
            ("remaining", "剩余未决定"),
            ("all", "全部子项"),
        ):
            for decision, verb in (("restore", "清理"), ("keep", "保留")):
                styled_button(
                    controls, title + verb,
                    lambda selected=selection, value=decision:
                    self.choose_subitem_batch(selected, value),
                    width=16,
                ).pack(side="left", padx=(0, 3))
        self.subitem_decision_row = styled_frame(self.subitem_frame)
        self.accept_subitem_button = styled_button(
            self.subitem_decision_row, "接受这批子项决定",
            self.accept_pending_subitems, width=28,
        )
        self.accept_subitem_button.pack(side="right")
        styled_button(
            self.subitem_decision_row, "放弃子项草稿",
            self.discard_pending_subitems, width=18,
        ).pack(side="right", padx=(0, 6))

    def open_subitems(self):
        if self.busy or self.destroyed or self.preview.execution.finalized:
            return
        if (
            self.pending_decision is not None
            or self.pending_subitem_decision is not None
        ):
            safe_show_error(
                "草稿尚未处理", "请先接受或放弃当前决定草稿。", self.dialog,
            )
            return
        identifier = self.focused_id()
        if not identifier:
            safe_show_error("未选择对象", "请选择一个目录对象。", self.dialog)
            return
        if self.actions[identifier].subitem_scope is None:
            safe_show_error(
                "关联范围必须整体处理",
                "此对象必须整体恢复或保留；不能整体替换同时保留其后代。",
                self.dialog,
            )
            return
        self.subitem_identifier = identifier
        self.subitem_frame.pack(
            fill="both", expand=True, pady=(3, 0), before=self.detail_frame,
        )
        self.refresh_subitems()

    def current_subitem_decisions(self):
        identifier = self.subitem_identifier
        scope = self.actions[identifier].subitem_scope
        values = self.preview.execution.subitem_decisions.get(identifier)
        return initial_subitem_decisions(scope) if values is None else values

    def refresh_subitems(self):
        identifier = self.subitem_identifier
        if identifier is None:
            return
        scope = self.actions[identifier].subitem_scope
        decisions = self.current_subitem_decisions()
        selected = {
            self.subitem_rows[row]
            for row in self.subitem_table.selection()
            if row in self.subitem_rows
        }
        for row in self.subitem_table.get_children():
            self.subitem_table.delete(row)
        self.subitem_rows = {}
        for number, item in enumerate(scope.items):
            row = f"s{number}"
            self.subitem_rows[row] = item.relative
            self.subitem_table.insert("", "end", iid=row, values=(
                item.relative if item.relative else "（目录根自身）",
                KIND_LABELS[item.current_kind],
                SUBITEM_STATUS_LABELS[item.status],
                SUBITEM_DECISION_LABELS[decisions[item.relative]],
            ))
            if item.relative in selected:
                self.subitem_table.selection_add(row)
        mode = RESTORE_DECISION_LABELS[
            self.preview.execution.decisions[identifier]
        ]
        self.subitem_status.set(
            f"{identifier}；{scope.target}；正式模式：{mode}。\n"
            "查看不切换模式；接受子项决定才生效。目录保留只保留自身，"
            "后代分别决定；保留后代的必要祖先自动留下。"
        )

    def choose_subitem_batch(self, selection, decision):
        if (
            self.busy or self.destroyed or self.preview.execution.finalized
            or self.subitem_identifier is None
        ):
            return
        if self.pending_decision is not None:
            safe_show_error(
                "对象草稿尚未处理", "请先接受或放弃对象决定草稿。", self.dialog,
            )
            return
        if selection not in ("selected", "remaining", "all"):
            raise ValueError("未知子项决定范围")
        identifier = self.subitem_identifier
        scope = self.actions[identifier].subitem_scope
        previous = self.current_subitem_decisions()
        relatives = (
            tuple(
                self.subitem_rows[row] for row in self.subitem_table.selection()
            ) if selection == "selected" else ()
        )
        replaces = selection == "all" and any(
            value in ("restore", "keep") and value != decision
            for value in previous.values()
        )
        try:
            candidate = change_subitem_decisions(
                scope, previous, relatives, decision,
                selection=selection, replace_decisions=replaces,
            )
        except ValueError as error:
            safe_show_error("子项决定未准备", str(error), self.dialog)
            return
        replaces_object = self.preview.execution.decisions[identifier] in (
            "restore", "keep",
        )
        self.pending_subitem_decision = (
            identifier, relatives, decision, selection, replaces, replaces_object,
        )
        text = subitem_decision_detail(scope, previous, candidate)
        if replaces_object:
            text += (
                "\n\n【整体／子项模式重设】\n"
                "明确接受后将替换本对象已有整体恢复／保留决定。\n"
                "子项方案与整体决定互斥，不会同时执行。"
            )
        self.set_text(self.detail, text)
        self.accept_subitem_button.configure(
            text="明确接受模式／子项重设"
            if replaces or replaces_object else "接受这批子项决定",
        )
        self.subitem_decision_row.pack(fill="x", pady=(0, 4))
        self.next_button.configure(state="disabled")

    def accept_pending_subitems(self):
        if (
            self.busy or self.destroyed
            or self.pending_subitem_decision is None
        ):
            return
        (
            identifier, relatives, decision, selection,
            replaces, replaces_object,
        ) = self.pending_subitem_decision
        choose_backup_restore_subitems(
            self.preview, identifier, relatives, decision,
            selection=selection, replace_decisions=replaces,
            replace_object_decision=replaces_object,
        )
        self.pending_subitem_decision = None
        self.subitem_decision_row.pack_forget()
        # 对象表在草稿期间可能改变选中项；接受仍绑定草稿原对象编号。
        self.table.selection_set(identifier)
        self.table.focus(identifier)
        self.subitem_identifier = identifier
        self.refresh_selection()

    def discard_pending_subitems(self):
        self.pending_subitem_decision = None
        self.subitem_decision_row.pack_forget()
        self.refresh_selection()

    def prepare_final(self):
        if (
            self.busy or self.destroyed or self.preview.execution.finalized
            or self.pending_decision is not None
            or self.pending_subitem_decision is not None
        ):
            return
        self.busy = True
        try:
            summary = prepare_backup_restore(self.preview)
            text = self.preview.preview_text
            text += (
                "\n\n来源路径设置：完整原状态恢复成功后清空；"
                "有用户保留时仍保留来源，不删除备份文件。"
                if not self.keep_source_path else
                "\n\n来源路径设置：完成后保留来源，不删除备份文件。"
            )
            self.set_text(self.final_text, text)
            self.selection_page.pack_forget()
            self.final_page.pack(fill="both", expand=True, padx=14)
            self.next_button.pack_forget()
            self.back_button.pack(side="right", padx=(8, 0))
            self.confirm_button.pack(side="right")
            self.loss_acknowledged.set(False)
            if not summary["backup_enabled"] and summary["content_loss_paths"]:
                self.risk_check.pack(fill="x", pady=(8, 0))
            else:
                self.risk_check.pack_forget()
            self.status.set(
                f"最终方案：恢复 {summary['restore_count']}；"
                f"保留 {summary['keep_count']}；"
                f"无需操作 {summary['no_operation_count']}。请核对完整范围。"
            )
            self.refresh_final_button()
            self.back_button.focus_set()
        except (OSError, ValueError) as error:
            safe_show_error("最终方案未形成", str(error), self.dialog)
            if self.preview.execution.consumed:
                self.close_window()
        finally:
            self.busy = False

    def refresh_final_button(self):
        summary = self.preview.risk_summary
        requires_ack = (
            not summary["backup_enabled"] and bool(summary["content_loss_paths"])
        )
        self.confirm_button.configure(
            state="disabled"
            if requires_ack and not self.loss_acknowledged.get() else "normal",
            text="确认按方案还原"
            if summary["restore_count"] else "确认全部保留／无需操作",
        )

    def return_to_selection(self):
        if self.busy or self.destroyed:
            return
        return_backup_restore_to_selection(self.preview)
        self.loss_acknowledged.set(False)
        self.final_page.pack_forget()
        self.confirm_button.pack_forget()
        self.back_button.pack_forget()
        self.selection_page.pack(fill="both", expand=True, padx=14)
        self.next_button.pack(side="right")
        self.refresh_selection()
        self.next_button.focus_set()

    def confirm(self):
        if self.busy or self.destroyed:
            return
        self.busy = True
        try:
            confirm_backup_restore(
                self.preview, loss_acknowledged=self.loss_acknowledged.get(),
            )
            self.accepted = True
            self.close_window()
        except ValueError as error:
            safe_show_error("最终确认未接受", str(error), self.dialog)
        finally:
            self.busy = False

    def cancel(self):
        if self.busy:
            return False
        if not self.destroyed:
            invalidate_preview(self.preview)
            self.close_window()
        return True

    def close_window(self):
        if not self.destroyed:
            unregister_popup(self.dialog)
            self.dialog.destroy()

    def on_destroy(self, event):
        if event.widget is self.dialog:
            self.destroyed = True
            if not self.accepted:
                invalidate_preview(self.preview)


def select_backup_restore(parent, preview, *, keep_source_path):
    """选择只取得本次最终资格，工作恢复仍由正式服务执行。"""
    session = None
    completed = False
    try:
        session = RestoreSelectionSession(
            parent, preview, keep_source_path=keep_source_path,
        )
        parent.wait_window(session.dialog)
        completed = session.accepted
        return completed
    finally:
        if not completed:
            invalidate_preview(preview)
        if session is not None and not session.destroyed:
            session.close_window()