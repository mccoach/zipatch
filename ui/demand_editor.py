# -*- coding: utf-8 -*-

"""按名单合并草稿、后台检查调度和最终操作的界面协调。"""

import tkinter as tk
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from dataclasses import dataclass
from threading import Event
from time import monotonic
from tkinter import messagebox

from core.constants import THEME
from ui.message_dialogs import safe_show_error, safe_show_info
from ui.window_manager import center_window
from ui.text_editor import replace_text_keep_undo
from services.demand_rules import (
    DemandCheckCancelled,
    DemandMatch,
    build_demand_selection,

    demand_physical_lines,
    inspect_demand_rules,
    rule_is_relative,
)
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup, take_modal_control
from ui.list_text_editing import ListTextEditing
from core.rule_lists import clean_rule_quotes, is_exclusion_layout_line


DEBOUNCE_SECONDS = 0.2
RESULT_POLL_MILLISECONDS = 40

# 正文高亮与顶部色块图例共用颜色，避免展示含义发生偏离。
DEMAND_HIGHLIGHT_STYLES = (
    ("demand_error", THEME["danger"], THEME["danger_bg"], "红：选择错误"),
    ("demand_warning", THEME["fg"], "#fff2a8", "黄：存在重复"),
    ("demand_success", "#175c25", "#cde8cf", "绿：选择成功且无重复"),
    ("demand_disabled", "#555555", "#c6c6c6", "灰：主动停用"),
)

DEMAND_HELP = r"""合并列表填写规则

一、每行选择一个文件或一组文件
────────────────────────────────────────
每行填写一条文件选择规则。
可以填写具体文件路径，也可以使用通配符选择一组文件。
这里是“选择要合并的文件”，不是排除名单；
扫描和按目录合并的排除名单不适用于这里。

例：
README.md
docs\a.md
docs\*.md

不要用逗号或分号分隔多条规则。
名称里的空格、逗号、分号和方括号都是普通字符。


二、引号、空行和空格
────────────────────────────────────────
• 半角双引号 " 在输入、粘贴和回填时实时删除，包括路径内部的双引号。
• 删除双引号后完全为空、或仅含半角空格的行属于排版。
• 排版行原样保留位置、数量和半角空格数量；不选择文件、不高亮、不提醒。
• 开头、中间、末尾及连续排版行都保留，保存和收藏不压缩。
• 实际规则不自动删除首尾空格；空格参与文件名匹配，不增加专门警告。
• 全角空格、NBSP 等 Unicode 空白不是排版，保留并按实际名称检查。
  存在对应文件时可以选择；不存在时显示正常的文件选择错误。
• Tab、孤立回车和禁用控制字符不是排版；启用行含这些字符时报红。
  行首 | 停用的正文不接受路径检查。
• 整份名单只有排版或停用行时，没有可合并文件，不能执行。
• 不展开环境变量或 ~，请填写实际路径。
• 不支持行尾注释；说明文字不要写在启用规则后面。
• 换行请使用普通回车换行，不用 Tab 分隔规则。

例：
"docs\a.md"       → docs\a.md
""                → 不选择文件
;README.md        → 名为 ;README.md 的文件，不表示停用


三、停用某一行：行首 |
────────────────────────────────────────
删除双引号后，第一个字符为半角竖线 |，表示停用。
去掉行首 | 即可重新启用。
Ctrl+` 逐行切换行首一个 |；无选区处理光标所在物理行。
有选区时各行独立切换；结束在下一行行首时不处理该下一行。
屏幕折行不算新行。空行变为 |，||abc 变为 |abc。
一次切换可一次撤销，不自动保存。

例：
|docs\a.md
|docs\*.md

停用行不选择文件，显示灰色。
| 必须位于行首；“ |docs\a.md”不是停用规则。
停用行可以保留暂时不用的路径或备注。


四、绝对路径和相对路径
────────────────────────────────────────
【绝对路径】
填写完整盘符路径，或完整网络共享路径。

例：
C:\docs\a.md
D:/project/README.md
\\server\share\docs\a.md

网络共享路径必须包含服务器名和共享名。
服务器名及共享名不能使用通配符。
可以选择不同盘符下的文件。

【相对路径】
以主面板设置的源文件夹为起点。

例：
README.md
只选择源文件夹根下的 README.md，不自动搜索子目录。

docs\a.md
选择源文件夹下 docs 目录中的 a.md。

使用相对路径时，必须设置有效的源文件夹。
全部启用规则都是绝对路径时，可以不设置源文件夹。
相对路径不得使用 .. 返回上级目录。

以下形式不支持：
C:docs\a.md
C:
\docs\a.md
\\?\C:\docs\a.md
\\.\C:\docs\a.md

可以使用 / 或 \ 作为路径分隔符。
不要在文件名后加斜杠；docs\a.md\ 表示目录要求，
不能用来选择文件。


五、文件夹不能直接合并
────────────────────────────────────────
填写文件夹路径不会自动选择里面的文件。
如需选择目录内文件，请明确使用通配符。

例：
docs
这是文件夹，不是可合并文件。

docs\*
选择 docs 当前层级的文件。

docs\**\*
选择 docs 及其全部子目录中的文件。


六、通配符
────────────────────────────────────────
【*：当前路径段内零个或多个字符】
不跨目录。

docs\*.md
选择 docs 当前层级的 Markdown 文件。

test_*.py
选择源文件夹根下的 test_.py、test_a.py 等文件。

【?：当前路径段内一个字符】
不跨目录。

part?.txt
可以选择 part1.txt，不选择 part.txt 或 part10.txt。

【**：零层或多层目录】
必须独占一个路径段。

docs\**\*.md
选择 docs 及其子目录中的 Markdown 文件。

**\README.md
选择源文件夹各层级的 README.md。

docs\**\*
选择 docs 下所有层级的文件。

以下写法无效：
a**b
**.md
***

不支持正则表达式、花括号扩展或取反。
[ab].txt 表示字面名称 [ab].txt，不表示 a.txt 或 b.txt。


七、顺序和重复选择
────────────────────────────────────────
• 合并顺序按名单从上到下排列。
• 一条通配符规则选中的文件，按完整路径排序，不区分大小写。
• 多条规则选中同一路径时，只合并一次，保留首次选中的位置。
• 重复规则不必手动删除；也可以调整顺序改变文件出现位置。


八、颜色和诊断
────────────────────────────────────────
红：规则无效、文件不存在、没有选中文件或无法访问；
    查看对应行的原因并修正，红项不能执行。

黄：选中了文件，但与前面的规则有重复；可以执行。

绿：成功选中文件，且没有与前面的规则重复。

灰：该行已主动停用。

无高亮：尚未得到确定结果；空行也不高亮。

结果区显示对应行的命中、重复和新增数量，
以及当前选中的文件清单，便于核对选择范围。
颜色是最近一次检查的结果，不代表文件以后不会变化。


九、保存与主页面合并
────────────────────────────────────────
本窗口只编辑合并列表，不执行文件合并，也不设置前言、后语。
顶部说明、颜色图例、规则检查和检查记录仍保留。

保存保留规则表达，不把通配符改写为具体文件清单。
重复行、排版行和末尾空行都保留。
有红项可以保存，留待以后修改；主页面合并时仍须完整检查通过。

主页面只有一个“合并”按钮，通过单选模式确定来源：
• 仅目录：只读取源文件夹，使用排除名单，不使用合并列表。
• 仅列表：只选择合并列表文件，不使用排除名单。
• 目录+列表：先选择目录文件，再补充列表文件。

仅目录和目录+列表必须填写有效源文件夹；
不会把空源文件夹当成程序当前工作目录。
仅列表全部使用绝对路径时，可以不填写源文件夹；
相对路径仍需要有效源文件夹作为基准。

目录+列表按目录排序在前、列表顺序在后。
同一路径只出现一次，保留首次位置。
列表明确选中的目录排除文件提供真实正文；
排除目录中的文件也可以通过列表明确补入。
这不改变排除名单本身。

所有模式的前言和后语统一来自主页面“附加文本”。
本窗口保存不会开始合并、创建成果目录或发布成果文件。
主页面执行时重新检查本次所需列表，不直接使用旧颜色作为执行资格。

执行前请确认：
• 所需列表没有红项。
• 仅列表模式至少选中一个文件。
• 文件清单及顺序符合预期。
• 没有选中本次输出文件。
• 相对路径所需的源文件夹正确。
• 目录+列表可以使用空列表，表示没有额外补充文件。
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

    dialog = tk.Toplevel(parent)
    dialog.title("合并列表 - 填写规则")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
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
        self.list_editing = ListTextEditing(
            self.rule_text, self.commits, self.on_rule_text,
        )
        self.baseline["demand_file_list_text"] = self.list_editing.text
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
            field: (
                self.list_editing.complete()
                if field == "demand_file_list_text"
                else self.commits.read_text(widget)
            )
            for field, widget in self.widgets.items()
        }

    def prepare_favorite(self, content, display=True):
        prepared = clean_rule_quotes(content)
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
                expression = clean_rule_quotes(raw)
                if is_exclusion_layout_line(expression):
                    rule.match = DemandMatch(completed=True, empty=True)
                elif expression.startswith("|"):
                    rule.match = DemandMatch(completed=True, disabled=True)
            rules.append(rule)
        self.rules = rules
        self.version += 1
        self.render()
        return True

    def on_rule_text(self, content):
        if self._destroyed or self._writing_text:
            return
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
                                "自动检查完成；保存及主页面合并会重新检查所需列表"
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
        # 高亮表达当前事实；结果正文保留按时间追加的检查反馈。
        # 历史记录不参与选择、去重或执行资格判断。
        follow_end = self.result_text.yview()[1] >= 0.999
        self.result_text.configure(state="normal")
        separator = "" if self.result_text.index("end-1c") == "1.0" else "\n\n"
        self.result_text.insert(
            "end-1c",
            separator + "【当前名单检查记录】\n" + "\n".join(lines),
        )
        self.result_text.configure(state="disabled")
        if follow_end:
            self.result_text.see("end")
        return selection

    def clear_check_history(self):
        """仅主动清空显示历史，不改变规则、匹配和业务配置。"""
        if self._destroyed or self.busy or self.commits.operation_depth:
            return False
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.configure(state="disabled")
        return True

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
        if action not in ("save", "close"):
            raise ValueError(f"未知合并列表保存操作：{action}")
        if self._destroyed or self.busy or self.commits.operation_depth:
            return False
        editors = self.commits.select("merge", {"source_folder"})
        self.commits.accept_editors(editors)
        if any(editor.dirty for editor in editors):
            self.status.set("源文件夹尚未有效接受，请修正后再继续")
            return False
        self.on_source_accepted()
        values = self.values()
        self.reconcile(values["demand_file_list_text"])
        self._final_values = values
        self._final_check_finished = False
        self._operation_scope = self.commits.operation()
        self._operation_scope.__enter__()
        prepared = False
        try:
            self.lock_inputs()
            self.cancel_running()
            for rule in self.rules:
                expression = clean_rule_quotes(rule.raw)
                rule.match = (
                    DemandMatch(completed=True, empty=True)
                    if is_exclusion_layout_line(expression) else
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
                "等待旧任务取消，随后完整检查并保存列表"
                if self.running is not None else "等待完整检查并保存列表"
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
            values["demand_file_list_text"] = clean_rule_quotes(
                values["demand_file_list_text"],
            )
            if not selection.complete:
                self.status.set("最终检查未完整完成，列表未保存")
                return
            # 与排除名单一样，本窗口只接受配置。
            # 文件选择错误允许保存草稿，执行时由唯一合并入口重新检查。
            if not super().accept(values):
                self.status.set("保存失败，列表草稿仍保留在窗口")
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
                    rule.raw = clean_rule_quotes(rule.raw)
                self.render()
            finally:
                self._writing_text = False

            self.status.set(
                "列表已保存；仍有选择错误，使用列表合并前需要修正"
                if selection.has_errors else
                "合并列表已保存；请在主页面选择模式并点击合并"
            )
            if selection.has_errors:
                safe_show_info(
                    "合并列表已保存",
                    "规则表达已保存，但仍有红色选择错误。\n"
                    "使用列表的合并模式执行前需要修正。",
                    self.dialog,
                )
            if action == "close":
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
                    "内容已修改", "是否完整检查后保存当前合并列表？",
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
    dialog.title("合并列表")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(panel.root)
    take_modal_control(dialog)
    center_window(dialog, 1180, 840)

    header = styled_frame(dialog)
    header.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(
        header,
        text="每行一条文件选择规则；| 停用；相对路径以主面板源文件夹为基准。"
             "Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做，Ctrl+` 逐行停用／启用。",
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
    body.grid_columnconfigure(0, weight=1, uniform="demand_text_columns")
    body.grid_columnconfigure(1, weight=1, uniform="demand_text_columns")
    body.grid_rowconfigure(0, weight=1)
    left = styled_frame(body)
    right = styled_frame(body)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    widgets = {
        "demand_file_list_text": create_managed_text_box(
            left, "合并列表", panel.cfg["demand_file_list_text"],
            height=30, mono=True, commits=panel.commits,
            favorite_key="demand_file_list",
            wrap_config_key="merge.demand_file_list",
            preserve_trailing_newlines=True,
        ),
    }
    tk.Label(
        right, text="名单检查记录", anchor="w",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
    ).pack(fill="x", pady=(8, 2))
    frame, result_text = styled_text_with_scrollbars(
        right, height=30, mono=True, wrap="none", readonly=True,
    )
    frame.pack(fill="both", expand=True)
    status = tk.StringVar(master=dialog, value="待检查")
    session = DemandDraftSession(panel, dialog, widgets, result_text, status)
    history_buttons = styled_frame(right)
    history_buttons.pack(fill="x", pady=(4, 0))
    styled_button(
        history_buttons, "清空检查记录",
        session.clear_check_history, width=12,
    ).pack(side="right")

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
        bottom, "保存", session.save_and_close, width=8,
    ).pack(side="right")
    session.cancel_button = styled_button(
        bottom, "取消本次检查", session.cancel_final, width=12,
    )
    session.cancel_button.configure(state="disabled")
    session.cancel_button.pack(side="right", padx=(0, 8))
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    return session