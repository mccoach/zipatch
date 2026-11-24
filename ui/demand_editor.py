# -*- coding: utf-8 -*-

"""常驻目录／文件列表的后台检查、高亮和行悬浮摘要。

控件拥有正文，配置绑定负责接受；检查结果仅是本次派生视图。
后台不接触Tk、不授权链接、不读取普通文件正文或执行合并。
"""

import tkinter as tk

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from core.constants import THEME
from core.path_validation import normalize_windows_display_path
from core.rule_lists import clean_rule_quotes, is_exclusion_layout_line
from services.demand_rules import (
    DemandCheckCancelled, DemandMatch, build_demand_selection,
    demand_physical_lines, inspect_demand_rules,
)
from ui.dialogs import create_managed_text_box
from ui.list_text_editing import ListTextEditing
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.widgets import add_tooltip
from ui.window_manager import (
    center_window, register_popup, take_modal_control, unregister_popup,
)


CHECK_DELAY_MS = 200
POLL_MS = 50
HIGHLIGHTS = {
    "error": (THEME["danger"], THEME["danger_bg"]),
    "warning": (THEME["fg"], "#fff2a8"),
    "success": ("#175c25", "#cde8cf"),
    "disabled": ("#555555", "#c6c6c6"),
}

SOURCE_LIST_HELP = r"""文件夹列表与文件列表

共同填写规则
• 每行一条；半角双引号实时删除。
• 空行及纯半角空格行保留，不参与选择。
• 行首 | 停用；Ctrl+反引号逐物理行切换。
• 实际规则首尾空格参与匹配，不自动删掉。
• 支持完整盘符、UNC绝对路径和相对路径。
• 相对规则以页面“相对路径基准”为起点。
• 基准不自动加入合并；全部使用绝对规则时可以留空。
• 不展开环境变量或 ~；相对路径不允许 ..。
• 不用逗号或分号分隔规则；不支持行尾注释。
• * 匹配当前路径段零个或多个字符，? 匹配一个字符。
• ** 必须独占路径段，表示零层或多层文件夹。
• 不支持正则、花括号扩展、取反及设备路径。

文件夹列表
• 明确文件夹直接选中；明确文件拒绝。
• 通配符选择符合规则的文件夹，不直接选择文件正文。
• 例如 E:\项目、E:\工作区\项目*。
• 每个选中文件夹随后按原合并流程递归取文件。
• 同一份排除名单分别以每个源文件夹为根匹配。
• 规则按行顺序；单条命中文件夹按完整路径排序。
• 每个源文件夹内部保持原文件排序。
• 相同文件夹去重；上级和下级文件夹不擅自归并。
• 文件夹来源可重叠，同一实际文件只输出一个正文块。

文件列表
• 明确文件或通配符选文件，沿用原文件列表语义。
• 普通文件夹不能直接选，请使用文件夹列表或文件通配符。
• 原有明确文件夹软链接／文件夹联接（Junction）授权展开行为保留。
• 文件列表不使用文件夹来源的两份排除名单。
• 规则按行顺序，单条命中文件按完整路径排序。
• “组合合并”先处理文件夹列表，再补充文件列表。
• 明确补回的文件排除项归入首次对应源文件夹的文件排序；
  被排除文件夹内部或其他外部补充保持文件列表顺序。

检查反馈
• 绿色：有效且无重复；黄色：与前面的规则重复。
• 红色：规则、路径或访问错误；灰色：主动停用。
• 无色：检查中、排版或涉及链接待本次授权。
• 悬浮规则行查看简短摘要，底部显示当前统计。
• 后台检查只查询元信息，不弹窗、不授权、不读普通正文。
• 遇首个待授权链接停止路线，不探查下游。
• 每次正式执行重新检查，不用旧颜色作为执行资格。
• 输入、基准变化或窗口销毁会取消旧检查；
  过期结果不能覆盖新正文的检查状态。

编辑与保存
• 查找Ctrl+F、替换Ctrl+H、撤销Ctrl+Z、重做Ctrl+Y。
• 收藏、导入、清空、自动换行分别属于各自文本框。
• 右上角浏览：文件夹每次选一个，文件支持多选。
• 每个文本框的浏览、导入分别记住自己的最近有效文件夹，重启保留。
• 取消或失败不覆盖记录；旧位置失效时从程序所在文件夹重新选择。
• 选择的绝对路径逐行追加到名单末尾，不替换原文。
• 取消不修改；重复保留并由检查提示；追加可整体撤销。
• 浏览只填写路径，不授予链接访问或正式合并资格。
• 自动换行只改变显示，不决定成果换行。
• 主页面沿现有编辑完成点保存，不逐字写盘。
• 可以保存含红项的来源列表，正式使用该列表时必须修正。
• “合并文件夹”不使用文件列表；“合并文件”不使用文件夹列表。
• “组合合并”使用两份来源列表。
• 本次使用的列表任一错误，整次合并停止，不执行通过子集。

链接及成果
• 普通来源静默保留；链接节点在首次跟随前逐个选择。
• 同次任务同一实际节点决定共用，不保存未来授权。
• 未选在断点收口；取消任务不发布成果。
• 链接授权、来源隔离、固定路线复核及正文解码政策保持。
• 输出文件不得由文件列表明确选中；文件夹遍历自动跳过。
• 成果只输出成功文件正文块，遗漏名单集中放在尾部。
• 同一实际正文的别名要求不同换行成果时拒绝。
• 附加实际路径只用于说明，拆分仍按名义主路径定位。
"""


def show_source_list_help(parent):
    dialog = tk.Toplevel(parent)
    dialog.title("文件夹与文件列表 - 填写规则")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    footer = styled_frame(dialog)
    footer.pack(side="bottom", fill="x", padx=16, pady=12)
    frame, text = styled_text_with_scrollbars(
        dialog, height=25, wrap="word", readonly=True,
    )
    frame.pack(fill="both", expand=True, padx=16, pady=(12, 0))
    text.configure(state="normal")
    text.insert("1.0", SOURCE_LIST_HELP)
    text.configure(state="disabled")

    def close():
        unregister_popup(dialog)
        dialog.destroy()
        return True

    styled_button(footer, "关闭", close, width=10).pack(side="right")
    try:
        center_window(dialog, 860, min(720, dialog.winfo_screenheight() - 100))
        take_modal_control(dialog)
        register_popup(dialog, close)
        parent.wait_window(dialog)
    finally:
        unregister_popup(dialog)
        if dialog.winfo_exists():
            dialog.destroy()


def _check_jobs(jobs, base, cancel, object_kind):
    """工作线程只交付领域结果，具体I/O失败成为本次列表诊断。"""
    try:
        return inspect_demand_rules(
            jobs, base, cancel, object_kind=object_kind,
        )
    except (OSError, ValueError) as error:
        return {
            number: DemandMatch(issue=str(error)) for number, _ in jobs
        }


class PointerSummaryTooltip:
    """规则摘要靠近鼠标显示；不改变其他控件的通用Tooltip行为。"""

    def __init__(self, widget):
        self.widget = widget
        self.text = ""
        self.after_id = None
        self.window = None
        self.label = None
        self.x = self.y = 0

    def move(self, event):
        self.x, self.y = event.x_root, event.y_root
        if self.window is not None:
            self.position()

    def position(self):
        if self.window is None:
            return
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        screen_width = self.widget.winfo_screenwidth()
        screen_height = self.widget.winfo_screenheight()
        x = self.x + 16
        y = self.y + 20
        if x + width > screen_width - 8:
            x = self.x - width - 16
        if y + height > screen_height - 8:
            y = self.y - height - 12
        self.window.geometry(f"+{max(0, x)}+{max(0, y)}")

    def schedule_show(self):
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
        if self.text:
            self.after_id = self.widget.after(350, self.show)

    def show(self):
        self.after_id = None
        if not self.text or not self.widget.winfo_exists():
            return
        window = tk.Toplevel(self.widget)
        self.window = window
        window.withdraw()
        window.overrideredirect(True)
        self.label = tk.Label(
            window, text=self.text, justify="left",
            bg="#fff8dc", fg="#222222",
            relief="solid", borderwidth=1,
            padx=8, pady=5, wraplength=420,
            font=THEME["font_main"],
        )
        self.label.pack()
        self.position()
        window.deiconify()

    def hide(self):
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
            self.label = None


class SourceListEditor:
    """两份列表共用生命周期；目录／文件差异通过明确类别传递。"""

    def __init__(
        self, parent, panel, *, field, title, object_kind,
        favorite_key, wrap_key,
    ):
        self.panel = panel
        self.object_kind = object_kind
        self.destroyed = False
        self.version = 0
        self.after_id = None
        self.running = None
        self.pending = False
        self.matches = ()
        self.summaries = ()
        self.hover_line = None
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"ZipatchSource-{object_kind}",
        )
        self.text = create_managed_text_box(
            parent, title, panel.cfg[field], height=15, mono=True,
            commits=panel.commits, config=panel.cfg, field=field, page="merge",
            favorite_key=favorite_key, enable_import_text=True,
            browse_kind=object_kind,
            preserve_trailing_newlines=True, wrap_config_key=wrap_key,
        )
        for name, (foreground, background) in HIGHLIGHTS.items():
            self.text.tag_configure(
                "source_" + name, foreground=foreground, background=background,
            )
        self.status = tk.StringVar(master=self.text, value="待检查")
        summary_row = styled_frame(parent, bg=THEME["bg_panel"])
        summary_row.pack(fill="x", pady=(4, 0))

        status_label = tk.Label(
            summary_row, textvariable=self.status, anchor="w", width=1,
            bg=THEME["bg_panel"], fg=THEME["fg_dim"], font=THEME["font_main"],
        )
        status_label.pack(side="left", fill="x", expand=True)
        add_tooltip(
            status_label,
            "绿色有效；黄色重复；红色错误；灰色停用；"
            "无色待检查或待授权。详细规则见“填写规则”。",
        )
        self.editing = ListTextEditing(
            self.text, panel.commits, self.on_text,
        )
        self.text._prepare_favorite_content = self.prepare_favorite
        self.text._tooltip = PointerSummaryTooltip(self.text)
        self.text.bind("<Motion>", self.on_hover, add="+")
        self.text.bind("<Leave>", self.leave_hover, add="+")
        self.text.bind("<Destroy>", self.on_destroy, add="+")
        self.base_trace = panel.relative_base_folder.trace_add(
            "write", self.base_changed,
        )
        self.request()

    def prepare_favorite(self, content, display=True):
        # 与旧文件列表一致：收藏保存表达，不授权或展开链接。
        return clean_rule_quotes(content)

    def on_text(self, content):
        self.request()

    def base_changed(self, *_):
        self.request()

    def request(self):
        if self.destroyed:
            return
        self.version += 1
        self.pending = True
        self.matches = ()
        self.summaries = ()
        if self.running is not None:
            self.running[1].set()
        self.clear_highlights()
        self.status.set("待检查")
        self.hover_line = None
        self.text._tooltip.hide()
        if self.after_id is not None:
            self.text.after_cancel(self.after_id)
        self.after_id = self.text.after(CHECK_DELAY_MS, self.poll)

    def clear_highlights(self):
        for name in HIGHLIGHTS:
            self.text.tag_remove("source_" + name, "1.0", "end")

    def poll(self):
        self.after_id = None
        if self.destroyed:
            return
        try:
            if self.running is not None and self.running[2].done():
                version, cancel, future = self.running
                self.running = None
                try:
                    results = future.result()
                except DemandCheckCancelled:
                    results = None
                if results is not None and not cancel.is_set() and version == self.version:
                    lines = demand_physical_lines(self.editing.complete())
                    self.matches = tuple(
                        results.get(number) for number in range(1, len(lines) + 1)
                    )
                    self.render(lines)
            if self.running is None and self.pending:
                self.pending = False
                content = self.editing.complete()
                jobs = tuple(enumerate(demand_physical_lines(content), 1))
                base = normalize_windows_display_path(
                    self.panel.relative_base_folder.get(),
                )
                cancel = Event()
                future = self.executor.submit(
                    _check_jobs, jobs, base, cancel, self.object_kind,
                )
                self.running = (self.version, cancel, future)
                self.status.set("检查中")
        finally:
            if not self.destroyed and (self.running is not None or self.pending):
                self.after_id = self.text.after(POLL_MS, self.poll)

    def render(self, lines):
        selection = build_demand_selection(self.matches)
        self.clear_highlights()
        summaries = []
        enabled = errors = waiting = repeated = 0
        noun = "文件夹" if self.object_kind == "directory" else "文件"
        for number, (raw, match, duplicate) in enumerate(
            zip(lines, self.matches, selection.duplicates), 1,
        ):
            tag = None
            if is_exclusion_layout_line(raw):
                summary = ""
            elif raw.startswith("|"):
                summary, tag = "已停用，不参与合并", "disabled"
            else:
                enabled += 1
                if match is None:
                    summary = "待检查"
                elif match.requires_authorization:
                    waiting += 1
                    summary = "涉及链接，执行时选择；后台未进入目标"
                elif match.issue:
                    errors += 1
                    summary, tag = match.issue, "error"
                elif match.disabled:
                    summary, tag = "本次来源路线未接通", "disabled"
                elif duplicate.determined:
                    repeated += bool(duplicate.duplicates)
                    summary = (
                        f"命中{duplicate.hits}个{noun}；"
                        f"重复{duplicate.duplicates}个；新增{duplicate.additions}个"
                    )
                    tag = "warning" if duplicate.duplicates else "success"
                else:
                    summary = f"命中{duplicate.hits}个{noun}；前序待定，重复尚未确定"
            summaries.append(summary)
            if tag is not None:
                self.text.tag_add(
                    "source_" + tag, f"{number}.0", f"{number}.end",
                )
        for name in HIGHLIGHTS:
            self.text.tag_lower("source_" + name)
        for name in ("search_match", "search_current", "sel"):
            self.text.tag_raise(name)
        self.summaries = tuple(summaries)
        self.status.set(
            f"启用{enabled}条 · 重复{repeated}条 · "
            f"错误{errors}条 · 待授权{waiting}条"
        )

    def on_hover(self, event):
        tooltip = self.text._tooltip
        tooltip.move(event)
        number = int(self.text.index(f"@{event.x},{event.y}").split(".")[0])
        if number == self.hover_line:
            return
        self.hover_line = number
        tooltip.hide()
        summary = (
            self.summaries[number - 1]
            if number <= len(self.summaries) else "当前规则待检查"
        )
        tooltip.text = summary[:240]
        if summary:
            tooltip.schedule_show()

    def leave_hover(self, event=None):
        self.hover_line = None
        self.text._tooltip.hide()

    def on_destroy(self, event):
        if event.widget is not self.text or self.destroyed:
            return
        self.destroyed = True
        self.text._tooltip.hide()
        if self.after_id is not None:
            self.text.after_cancel(self.after_id)
            self.after_id = None
        if self.running is not None:
            self.running[1].set()
        self.panel.relative_base_folder.trace_remove("write", self.base_trace)
        self.executor.shutdown(wait=False, cancel_futures=True)