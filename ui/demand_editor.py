# -*- coding: utf-8 -*-

"""按需合并草稿、后台检查调度和最终操作的界面协调。"""

import tkinter as tk
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import Event
from time import monotonic
from tkinter import messagebox

from core.constants import THEME
from core.message_utils import safe_show_error, safe_show_info
from core.paths import center_window
from core.text_io import replace_text_keep_undo
from services.demand_rules import (
    DemandCheckCancelled,
    DemandMatch,
    build_demand_selection,
    clean_demand_expression,
    demand_physical_lines,
    inspect_demand_rules,
    rule_is_relative,
)
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup


DEBOUNCE_SECONDS = 0.2
RESULT_POLL_MILLISECONDS = 40

# 正文高亮与顶部色块图例共用颜色，避免展示含义发生偏离。
DEMAND_HIGHLIGHT_STYLES = (
    ("demand_error", THEME["danger"], THEME["danger_bg"], "红：选择错误"),
    ("demand_warning", THEME["fg"], "#fff2a8", "黄：存在重复"),
    ("demand_success", "#175c25", "#cde8cf", "绿：选择成功且无重复"),
    ("demand_disabled", "#555555", "#c6c6c6", "灰：主动停用"),
)

DEMAND_HELP = r"""按需合并名单填写规则

每个物理文本行是一条文件选择规则，不是排除规则。
常规扫描、常规合并的排除名单不应用于这里。

文本处理
1. 删除每行全部半角双引号。
2. 删除后完全为空的行忽略；仅空白行不是空行。
3. 删除后第一个字符为 |，该行停用。
4. 其他行启用。不自动去掉首尾空白。
5. ; 是普通名称字符，不再表示停用；不支持行尾注释。
6. 不删除重复规则，不改变规则顺序。
7. 只识别 LF、CRLF 为换行，不展开环境变量或 ~。

路径
C:\docs\a.md 或 C:/docs/a.md：完整盘符绝对路径。
\\server\share\docs\a.md：完整 UNC 路径。
docs\a.md：以主面板已接受的源文件夹为唯一基准。
README.md：只选择源文件夹根下文件，不到任意层级搜索。
绝对路径可以使用不同于源文件夹的盘符。
C:docs\a.md、C:、单个开头斜杠、设备路径均不支持。
UNC 必须包含服务器名和共享名；两者不能使用通配符。
相对路径不得使用 ..，并要求源文件夹存在且为目录。
全部启用规则均为绝对路径时，不要求设置源文件夹。
普通目录路径不能直接合并，必须填写文件或显式通配符。

通配符
*：当前路径段内零个或多个字符，不跨目录。
?：当前路径段内恰好一个字符。
**：独立路径段，零层或多层目录。
a**、**a、*** 等均无效。
不支持正则、花括号扩展或取反规则。

示例
docs\*.md：docs 当前层级的 Markdown 文件。
docs\**\*.md：docs 及其子目录的 Markdown 文件。
**\README.md：源文件夹各层级的同名文件。
docs\**\*：递归选择 docs 下的文件。
|docs\*.md：主动停用，不检查、不执行。
;docs\a.md：启用的普通路径，不是停用。

颜色
红：语法、文件选择或访问检查失败，不能执行。
黄：选择成功，至少一个文件与前序规则重复，可以执行。
绿：选择成功，重复关系确定，没有与前序规则重复。
灰：用户主动停用。
无颜色：待检查、检查中、旧结果失效，或重复关系尚未确定。
空行无颜色。
颜色仅代表最近一次有效检查，不自动感知所有外部文件变化。

检查与结果
正文变化后立即撤销变化规则的旧结果。
200 毫秒防抖，只合并尚未开始的请求。
始终只有一个运行扫描任务和一个最新待检查请求。
修改正文或源基准立即通知旧任务取消；不保证立即中断系统调用。
检查在后台执行，后台不访问 Tk。
结果展示当前行号、命中/重复/新增数量和有序去重文件清单。
单条规则的文件按完整路径大小写不敏感排序。
同一路径仅合并一次，保留首次选中的位置。

保存
点击保存后固定正文和源基准，重新检查全部启用规则。
允许保存红项，但会明确提示仍不能执行。
保存的是规则表达，不是展开文件清单。
收藏同样保存规则表达；收藏回填触发自动检查。
收藏添加/改名的文字清洗不代替业务保存或执行的完整最终检查。

执行
点击执行后固定正文、附加文本和必要主面板参数。
等待旧任务退出后立即进行完整最终检查，不等待防抖。
任一红项、全部为空/停用、没有文件、实际选中输出文件，
或最终检查被取消，均不执行。
黄色不阻止执行。
执行消费本次固定的有序去重清单，不重新读取另一份名单。
源文件读取失败明确报告失败，不静默跳过。
本轮仍直接写成果；原子写入及旧成果保护留到下一课题。
"""


@dataclass
class RuleState:
    identifier: int
    raw: str
    match: DemandMatch | None = None


@dataclass(frozen=True)
class CheckRequest:
    version: int
    source: str
    jobs: tuple
    due: float
    final_action: str = ""


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def show_demand_help(parent):
    root = parent._root()
    previous_grab = parent.grab_current()
    dialog = tk.Toplevel(parent)
    dialog.title("按需合并 - 填写规则")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    dialog.grab_set()
    center_window(dialog, 860, 720)
    frame, text = styled_text_with_scrollbars(
        dialog, height=30, wrap="word", readonly=True,
    )
    frame.pack(fill="both", expand=True, padx=16, pady=(16, 8))
    text.configure(state="normal")
    text.insert("1.0", DEMAND_HELP)
    text.configure(state="disabled")
    row = styled_frame(dialog)
    row.pack(fill="x", padx=16, pady=(0, 14))

    def close():
        unregister_popup(dialog)
        dialog.destroy()
        return True

    styled_button(row, "关闭", close, width=10).pack(side="right")
    register_popup(dialog, close)
    try:
        parent.wait_window(dialog)
    finally:
        unregister_popup(dialog)
        if (
            not getattr(root, "_zipatch_closed", False)
            and previous_grab is not None
            and previous_grab.winfo_exists()
        ):
            previous_grab.grab_set()


class DemandDraftSession(TextDraftSession):
    """控件是草稿源；任务和匹配结果是有明确失效边界的派生快照。"""

    def __init__(self, panel, dialog, widgets, result_text, status):
        super().__init__(dialog, panel.commits, panel.cfg, widgets)
        self.panel = panel
        self.result_text = result_text
        self.status = status
        self.rule_text = widgets["demand_file_list_text"]
        self.baseline["demand_file_list_text"] = self.rule_text.get("1.0", "end-1c")
        self.source = panel.cfg["source_folder"]
        self.version = 0
        self.rules = []
        self.next_identifier = 0
        self.pending = None
        self.running = None
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ZipatchDemandCheck",
        )
        self._poll_id = None
        self._writing_text = False
        self._final_values = None
        self._final_parameters = None
        self._operation_scope = None
        self._final_check_finished = False
        self._locked_widgets = []
        self._source_bindings = []
        self._disposed = False
        self.cancel_button = None

        for tag, foreground, background, _ in DEMAND_HIGHLIGHT_STYLES:
            self.rule_text.tag_configure(
                tag, foreground=foreground, background=background,
            )
            self.rule_text.tag_lower(tag)
        self.rule_text.tag_raise("search_match")
        self.rule_text.tag_raise("search_current")
        self.rule_text.tag_raise("sel")
        self.rule_text.edit_modified(False)
        self.rule_text.bind("<<Modified>>", self.on_modified, add="+")
        self.rule_text._prepare_favorite_content = self.prepare_favorite
        for binding in self.commits.select("merge", {"source_folder"}):
            callback_id = binding.widget.bind(
                "<<ConfigAccepted>>", self.on_source_accepted, add="+",
            )
            self._source_bindings.append((binding.widget, callback_id))

        self.reconcile(self.baseline["demand_file_list_text"])
        self.request_auto()
        self._poll_id = dialog.after(RESULT_POLL_MILLISECONDS, self.poll)
        dialog.bind("<Destroy>", self.on_dialog_destroyed, add="+")

    @property
    def busy(self):
        return self._operation_scope is not None

    def values(self):
        return {
            field: self.commits.read_text(
                widget,
                preserve_trailing_newlines=field == "demand_file_list_text",
            )
            for field, widget in self.widgets.items()
        }

    def prepare_favorite(self, content, display=True):
        prepared = clean_demand_expression(content)
        if display and prepared != content:
            replace_text_keep_undo(self.rule_text, prepared)
        return prepared

    def reconcile(self, content):
        new_lines = demand_physical_lines(content)
        if new_lines == [rule.raw for rule in self.rules]:
            return False
        reusable = defaultdict(deque)
        for rule in self.rules:
            reusable[rule.raw].append(rule)
        rules = []
        for raw in new_lines:
            if reusable[raw]:
                rule = reusable[raw].popleft()
            else:
                self.next_identifier += 1
                rule = RuleState(self.next_identifier, raw)
                expression = clean_demand_expression(raw)
                if expression == "":
                    rule.match = DemandMatch(completed=True, empty=True)
                elif expression.startswith("|"):
                    rule.match = DemandMatch(completed=True, disabled=True)
            rules.append(rule)
        self.rules = rules
        self.version += 1
        self.render()
        return True

    def on_modified(self, event=None):
        if self._destroyed or self._writing_text:
            return
        if not self.rule_text.edit_modified():
            return
        self.rule_text.edit_modified(False)
        content = self.commits.read_text(
            self.rule_text, preserve_trailing_newlines=True,
        )
        if demand_physical_lines(content) == [rule.raw for rule in self.rules]:
            return
        self.cancel_running()
        if self.busy:
            self.pending = None
            self.status.set("固定正文发生变化，最终操作已取消")
        self.reconcile(content)
        if not self.busy:
            self.request_auto()

    def on_source_accepted(self, event=None):
        if self._destroyed:
            return
        source = self.panel.cfg["source_folder"]
        if source == self.source:
            return
        self.source = source
        self.version += 1
        self.cancel_running()
        for rule in self.rules:
            if rule_is_relative(rule.raw):
                rule.match = None
        self.render()
        if self.busy:
            self.pending = None
            self.status.set("固定源基准发生变化，最终操作已取消")
        else:
            self.request_auto()

    def cancel_running(self):
        if self.running is not None:
            self.running[1].set()

    def request_auto(self):
        if self._destroyed or self.busy:
            return
        jobs = tuple(
            (rule.identifier, rule.raw)
            for rule in self.rules if rule.match is None
        )
        self.pending = (
            CheckRequest(
                self.version, self.source, jobs,
                monotonic() + DEBOUNCE_SECONDS,
            )
            if jobs else None
        )
        self.status.set(
            "取消中，等待旧任务退出" if self.running is not None
            else "待检查" if jobs else "检查结果已更新"
        )

    def poll(self):
        # 正式定时调用与显式调用共用一个入口。
        # 显式调用不能遗失仍在 Tcl 队列中的旧回调；
        # 定时调用自身已经消费时，after_cancel 同样可以安全收口。
        if self._poll_id is not None:
            self.dialog.after_cancel(self._poll_id)
            self._poll_id = None
        if self._destroyed or self._disposed:
            return
        try:
            if self.running is not None and self.running[2].done():
                request, cancel, future = self.running
                self.running = None
                delivered = False
                try:
                    try:
                        results = future.result()
                    except DemandCheckCancelled:
                        results = None
                    if (
                        results is not None and not cancel.is_set()
                        and request.version == self.version
                        and request.source == self.source
                    ):
                        for rule in self.rules:
                            if rule.identifier in results:
                                rule.match = results[rule.identifier]
                        selection = self.render()
                        delivered = True
                        if request.final_action:
                            self.finish_final(request.final_action, selection)
                        else:
                            self.status.set(
                                "自动检查完成；保存和执行仍会完整重新检查"
                            )
                finally:
                    if request.final_action and self.busy and not delivered:
                        self.release_final()
                        self.status.set("最终检查未完成，未保存、未执行")
            if self._destroyed or self._disposed:
                return

            # 取消可能发生在旧自动任务退出之前，此时运行请求不是 final。
            if self.busy and self.running is None and self.pending is None:
                self.release_final()
                self.status.set("最终检查已取消，未保存、未执行")

            if (
                self.running is None and self.pending is not None
                and monotonic() >= self.pending.due
            ):
                request, self.pending = self.pending, None
                cancel = Event()
                submitted = False
                try:
                    future = self.executor.submit(
                        inspect_demand_rules, request.jobs, request.source, cancel,
                    )
                    self.running = (request, cancel, future)
                    submitted = True
                    self.status.set(
                        "正在完整最终检查" if request.final_action else "检查中"
                    )
                finally:
                    if not submitted and self.busy:
                        self.release_final()
                        self.status.set("任务未启动，未保存、未执行")
        finally:
            if not self._destroyed and not self._disposed:
                self._poll_id = self.dialog.after(
                    RESULT_POLL_MILLISECONDS, self.poll,
                )

    def render(self):
        selection = build_demand_selection([rule.match for rule in self.rules])
        for tag in (
            "demand_error", "demand_warning",
            "demand_success", "demand_disabled",
        ):
            self.rule_text.tag_remove(tag, "1.0", "end")
        lines = []
        for number, (rule, duplicate) in enumerate(
            zip(self.rules, selection.duplicates), 1,
        ):
            match = rule.match
            tag = None
            if match is None:
                lines.append(f"第 {number} 行：待检查；命中／重复／新增待确定")
            elif match.empty:
                continue
            elif match.disabled:
                tag = "demand_disabled"
                lines.append(f"第 {number} 行：停用，不检查、不去重")
            elif match.issue:
                tag = "demand_error"
                lines.append(
                    f"第 {number} 行：{match.issue}\n"
                    f"  规则转义展示：{rule.raw!r}\n"
                    "  命中／重复／新增无有效结论"
                )
            elif duplicate.determined:
                tag = "demand_warning" if duplicate.duplicates else "demand_success"
                lines.append(
                    f"第 {number} 行：命中 {duplicate.hits}，"
                    f"重复 {duplicate.duplicates}，新增 {duplicate.additions}"
                )
            else:
                lines.append(
                    f"第 {number} 行：命中 {duplicate.hits}；"
                    "前序结果未确定，重复／新增待确定"
                )
            if tag:
                self.rule_text.tag_add(tag, f"{number}.0", f"{number}.end")
        lines.extend((
            "",
            "最终有序去重文件清单"
            if selection.complete and not selection.has_errors
            else "当前有效匹配文件清单（非可执行结论）",
            "-" * 48,
        ))
        lines.extend(
            f"{number}. {path}"
            for number, path in enumerate(selection.files, 1)
        )
        if not selection.files:
            lines.append("无")
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", "\n".join(lines))
        self.result_text.configure(state="disabled")
        return selection

    def lock_inputs(self):
        candidates = list(descendants(self.dialog)) + list(descendants(self.panel))
        for widget in candidates:
            if widget is self.cancel_button:
                continue
            if isinstance(
                widget, (tk.Text, tk.Entry, tk.Button, tk.Checkbutton, tk.Radiobutton),
            ):
                self._locked_widgets.append((widget, str(widget.cget("state"))))
                widget.configure(state="disabled")
        if self.cancel_button is not None:
            self.cancel_button.configure(state="normal")

    def release_final(self):
        try:
            for widget, previous in self._locked_widgets:
                if widget.winfo_exists():
                    widget.configure(state=previous)
            if self.cancel_button is not None and self.cancel_button.winfo_exists():
                self.cancel_button.configure(state="disabled")
        finally:
            self._locked_widgets.clear()
            if self._operation_scope is not None:
                scope, self._operation_scope = self._operation_scope, None
                scope.__exit__(None, None, None)
            self._final_values = None
            self._final_parameters = None
            self._final_check_finished = False

    def cancel_final(self):
        # 取消只终止检查，不能在保存或成果执行阶段提前解除生命周期保护。
        if not self.busy or self._final_check_finished:
            return False
        self.pending = None
        self.cancel_running()
        if self.running is None:
            self.release_final()
            self.status.set("最终检查已取消，未保存、未执行")
        else:
            self.status.set("取消中；等待任务退出，未保存、未执行")
        return True

    def start_final(self, action):
        if action not in ("save", "execute", "close"):
            raise ValueError(f"未知按需名单操作：{action}")
        if self._destroyed or self.busy or self.commits.operation_depth:
            return False
        fields = (
            {
                "source_folder", "output_folder", "output_filename",
                "code_header_line", "code_footer_line",
            }
            if action == "execute" else {"source_folder"}
        )
        self.commits.accept_editors(self.commits.select("merge", fields))
        self.on_source_accepted()
        values = self.values()
        self.reconcile(values["demand_file_list_text"])
        self._final_values = values
        self._final_parameters = {
            field: deepcopy(self.panel.cfg[field])
            for field in (
                "source_folder", "output_folder", "output_filename",
                "code_header_line", "code_footer_line",
                "force_overwrite", "open_after_done",
            )
        }
        self._final_check_finished = False
        self._operation_scope = self.commits.operation()
        self._operation_scope.__enter__()
        prepared = False
        try:
            self.lock_inputs()
            self.cancel_running()
            for rule in self.rules:
                expression = clean_demand_expression(rule.raw)
                rule.match = (
                    DemandMatch(completed=True, empty=True)
                    if expression == "" else
                    DemandMatch(completed=True, disabled=True)
                    if expression.startswith("|") else None
                )
            self.render()
            self.pending = CheckRequest(
                self.version, self.source,
                tuple((rule.identifier, rule.raw) for rule in self.rules),
                monotonic(), action,
            )
            self.status.set(
                "等待旧任务取消，随后进行完整最终检查"
                if self.running is not None else "等待完整最终检查"
            )
            prepared = True
            return True
        finally:
            if not prepared:
                self.release_final()

    def finish_final(self, action, selection):
        try:
            self._final_check_finished = True
            if self.cancel_button is not None:
                self.cancel_button.configure(state="disabled")
            values = dict(self._final_values)
            parameters = dict(self._final_parameters)
            values["demand_file_list_text"] = clean_demand_expression(
                values["demand_file_list_text"]
            )
            if not selection.complete:
                self.status.set("最终检查未完整完成，未保存、未执行")
                return
            if action == "execute":
                if selection.has_errors:
                    self.status.set("存在红色规则，未执行；可以修改或保存草稿")
                    safe_show_error(
                        "名单需要修正",
                        "存在选择错误，请查看当前行号诊断。整份名单未执行。",
                        self.dialog,
                    )
                    return
                if not selection.files:
                    self.status.set("没有可合并文件，未执行")
                    safe_show_error(
                        "名单为空", "请至少启用一条实际选中文件的规则。",
                        self.dialog,
                    )
                    return

            if not super().accept(values):
                self.status.set("保存失败，草稿仍保留在窗口")
                return
            self._writing_text = True
            try:
                original = "\n".join(rule.raw for rule in self.rules)
                if original != values["demand_file_list_text"]:
                    self.rule_text.configure(state="normal")
                    replace_text_keep_undo(
                        self.rule_text, values["demand_file_list_text"],
                    )
                    self.rule_text.configure(state="disabled")
                self.rule_text.edit_modified(False)
                for rule in self.rules:
                    rule.raw = clean_demand_expression(rule.raw)
                # 正文整体替换会移除标签；清洗不改变选择语义，
                # 使用本次已完成的匹配结果重建展示，不重新访问文件系统。
                self.render()
            finally:
                self._writing_text = False

            self.status.set(
                "已保存；仍存在选择错误，暂不能执行"
                if selection.has_errors else "已保存规则表达"
            )
            if selection.has_errors:
                safe_show_info(
                    "名单已保存",
                    "规则表达已保存，但仍有红色选择错误，暂不能执行。",
                    self.dialog,
                )
            if action == "execute":
                completed = self.panel.execute_demand_merge(
                    parameters, values, selection.files, self.dialog,
                )
                if completed:
                    self.release_final()
                    self.destroy()
                else:
                    self.status.set("按需合并未完成；规则已保存，未宣称执行成功")
            elif action == "close":
                self.release_final()
                self.destroy()
        finally:
            if self.busy:
                self.release_final()

    def save_and_close(self):
        return self.start_final("save")

    def prepare_close(self):
        if self._destroyed:
            return True
        if self._close_in_progress or self.busy or self.commits.operation_depth:
            return False
        self._close_in_progress = True
        try:
            values = self.values()
            if values == self.baseline:
                return True
            with self.commits.operation():
                choice = messagebox.askyesnocancel(
                    "内容已修改", "是否完整检查后保存当前规则和附加文本？",
                    parent=self.dialog,
                )
            if choice is None:
                return False
            if choice:
                action = (
                    "save" if getattr(self.dialog._root(), "_zipatch_closing", False)
                    else "close"
                )
                self.start_final(action)
                return False
            return True
        finally:
            self._close_in_progress = False

    def close(self):
        if not self.prepare_close():
            return False
        self.destroy()
        return True

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.pending = None
        self.cancel_running()
        try:
            if self._poll_id is not None:
                self.dialog.after_cancel(self._poll_id)
                self._poll_id = None
            for widget, callback_id in self._source_bindings:
                if widget.winfo_exists():
                    widget.unbind("<<ConfigAccepted>>", callback_id)
            self._source_bindings.clear()
            if self.busy:
                self.release_final()
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)

    def on_dialog_destroyed(self, event):
        if event.widget is self.dialog:
            self.dispose()

    def destroy(self):
        self.dispose()
        super().destroy()


def open_demand_editor(panel):
    dialog = tk.Toplevel(panel.root)
    dialog.title("按需合并")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(panel.root)
    dialog.grab_set()
    center_window(dialog, 1180, 840)

    header = styled_frame(dialog)
    header.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(
        header,
        text="每行一条文件选择规则；| 停用；相对路径以主面板源文件夹为基准。"
             "Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
        anchor="w", justify="left", wraplength=990,
    ).pack(side="left", fill="x", expand=True)
    styled_button(
        header, "填写规则", lambda: show_demand_help(dialog), width=10,
    ).pack(side="right")
    legend = styled_frame(dialog)
    legend.pack(fill="x", padx=16, pady=(4, 4))
    tk.Label(
        legend, text="色块图例：", bg=THEME["bg"], fg=THEME["fg_label"],
        font=THEME["font_main"],
    ).pack(side="left", padx=(0, 8))
    legend_styles = [
        (background, description)
        for _, _, background, description in DEMAND_HIGHLIGHT_STYLES
    ]
    legend_styles.append((
        THEME["bg_input"], "无高亮：待检查或重复关系未确定",
    ))
    for background, description in legend_styles:
        tk.Label(
            legend, text="  ", bg=background, relief="solid", bd=1,
        ).pack(side="left", padx=(0, 5))
        tk.Label(
            legend, text=description, bg=THEME["bg"], fg=THEME["fg_label"],
            font=THEME["font_main"],
        ).pack(side="left", padx=(0, 16))

    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16, pady=4)
    left = styled_frame(body)
    right = styled_frame(body)
    left.pack(side="left", fill="both", expand=True, padx=(0, 8))
    right.pack(side="left", fill="both", expand=True, padx=(8, 0))
    widgets = {}
    for field, label, parent, favorite, wrap_key, height in (
        ("demand_file_list_text", "文件选择规则", left, "demand_file_list", "merge.demand_file_list", 21),
        ("demand_preamble_text", "前言文本", right, "demand_preamble", "merge.demand_preamble", 8),
        ("demand_ending_text", "后语文本", right, "demand_ending", "merge.demand_ending", 8),
    ):
        widgets[field] = create_managed_text_box(
            parent, label, panel.cfg[field], height=height,
            mono=parent is left, commits=panel.commits,
            favorite_key=favorite, wrap_config_key=wrap_key,
            preserve_trailing_newlines=field == "demand_file_list_text",
        )
    frame, result_text = styled_text_with_scrollbars(
        left, height=11, mono=True, wrap="none", readonly=True,
    )
    frame.pack(fill="both", expand=True, pady=(8, 0))
    status = tk.StringVar(master=dialog, value="待检查")
    session = DemandDraftSession(panel, dialog, widgets, result_text, status)

    bottom = styled_frame(dialog)
    bottom.pack(fill="x", padx=16, pady=(6, 14))
    tk.Label(
        bottom, textvariable=status, bg=THEME["bg"], fg=THEME["fg_dim"],
        font=THEME["font_main"], anchor="w", wraplength=750,
    ).pack(side="left", fill="x", expand=True)
    styled_button(bottom, "关闭", session.close, width=8).pack(
        side="right", padx=(8, 0),
    )
    styled_button(
        bottom, "执行按需合并", lambda: session.start_final("execute"),
        width=14, accent=True,
    ).pack(side="right", padx=(8, 0))
    styled_button(
        bottom, "保存", session.save_and_close, width=8,
    ).pack(side="right")
    session.cancel_button = styled_button(
        bottom, "取消本次检查", session.cancel_final, width=12,
    )
    session.cancel_button.configure(state="disabled")
    session.cancel_button.pack(side="right", padx=(0, 8))
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    return session