# -*- coding: utf-8 -*-

import tkinter as tk
from dataclasses import replace

from core.constants import THEME
from core.exclusion_rules import (
    ExclusionListResult, analyze_exclusion_list, format_exclusion_diagnostics,
)
from core.paths import center_window
from core.text_io import replace_text_keep_undo
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup


EXCLUSION_FIELDS = (
    ("exclude_folders", "目录名单", "directory"),
    ("exclude_files", "文件名单", "file"),
)


EXCLUSION_DISABLED_FOREGROUND = "#555555"
EXCLUSION_DISABLED_BACKGROUND = "#c6c6c6"
EXCLUSION_WARNING_BACKGROUND = "#fff2a8"

EXCLUSION_RULES_TEXT = r"""排除名单填写规则

一、目录名单与文件名单
────────────────────────────────────────
【目录名单】
填写目录名称或相对于所选源文件夹的目录路径。
命中目录后，实际跳过整个目录及其全部子目录、文件。
所选源文件夹本身不是目录名单的匹配对象。

【文件名单】
填写完整文件名、文件名通配符或相对于源文件夹的文件路径。

名称采用完整匹配，不会自动做子串匹配。
所有匹配均不区分大小写；保存时保留字母原来的大小写。

例：
cache
目录名单中匹配名为 cache 的目录，不匹配 mycache、cache_old。

README.md
文件名单中匹配 README.md，不匹配 README.md.bak。


二、逐行填写与保存时的处理
────────────────────────────────────────
1. 每条规则独占一行。
   不用逗号、分号或空格分隔多条规则。
   名称内部的空格、逗号、分号均是普通字符，参与匹配。

2. 只把 LF 和 CRLF 识别为换行。
   Tab、孤立回车及其他控制字符不是规则分隔符；
   出现在启用条目中会作为非法字符报告。

3. 明确保存或保存排除收藏时，删除所有半角双引号 "。
   包括外层和正文内部的双引号。
   单引号、反引号、中文引号不会被自动删除。

4. 删除双引号后完全为空的行会移除。
   只包含半角空格的行不是空行，而是无效规则。

5. 不自动去掉规则首尾的半角空格。
   有效模式的外围空格参与匹配，并产生黄色风险提示。

6. 不自动去重，不调整规则顺序。

7. 打开名单窗口时，立即根据原文显示红、黄、灰高亮。
   输入、粘贴、撤销、重做、查找替换和收藏回填后，
   高亮随当前原文更新，不保留已经失效的标记。
   展示分析不改动正文、不弹窗、不提交名单配置。
   只有明确保存或保存收藏时，才标准化、确认并提交。
   未标准化时，高亮对应原文行号；回写后对应当前显示行号。

8. 名单规则不展开环境变量或 ~，
   也不要求对应文件或目录目前真实存在。


三、临时放行：行首半角竖线 |
────────────────────────────────────────
删除双引号后，第一个字符是 |，表示该条规则临时放行。

例：
|cache
|src/cache/
|*.log
|:.git*

放行条目：
• 不参与排除匹配。
• 打开窗口即显示灰色文字和灰色背景，编辑后随当前原文更新。
• 除删除半角双引号外，正文保持原样。
• 不检查正文是否合法。
• 不统一正文斜杠，不清理尾斜杠。
• 不触发空格风险确认。

去掉行首 | 后，再次保存时按启用规则重新处理。

注意：
• | 必须是删除双引号后的第一个字符。
• “ |cache”有前导空格，不是放行条目，正文竖线非法。
• “:|README”也不是放行条目。
• 单独“|”是有效放行条目。
• 分号 ; 在排除名单中不表示放行或注释。
  按需合并路径名单的分号放行规则与这里不同。


四、无扩展名条件：文件名单行首半角冒号 :
────────────────────────────────────────
只适用于文件名单；目录名单不支持这个条件。

删除双引号后，第一个字符是 :，
要求文件没有扩展名，同时满足冒号后面的名称或路径模式。

例：
:
任意层级的全部无扩展名文件。

:README*
任意层级中，以 README 开头且没有扩展名的文件。

:.git*
匹配 .gitignore、.gitattributes、.gitconfig 等无扩展名文件；
不匹配 .git.txt、.gitconfig.bak。

:\README*
仅源文件夹根下，以 README 开头且没有扩展名的文件。

无扩展名按 PureWindowsPath.suffix 判断：
• README、LICENSE、.env、.gitignore：没有扩展名。
• main.py、archive.tar.gz、.config.json：有扩展名。

注意：
• 只有文件名单中的单独“:”允许没有后续模式。
• “:\”或“:/”没有模式正文，是无效规则。
• 正文冒号、重复冒号以及 Windows 盘符冒号均不允许。
• 类别只判断一次，不支持叠加“:|”等组合。


五、匹配范围
────────────────────────────────────────
【默认：任意层级】
不加开头斜杠时，可以从源文件夹下任意层级开始匹配。

例：
cache
匹配 cache、a\cache、a\b\cache。

src\cache
匹配 src\cache、a\src\cache；
不匹配 src\other\cache，因为普通路径段必须连续。

README.md
匹配任意层级中的 README.md。

【根限定：开头一个 \ 或 /】
启用模式正文的第一个字符是单个斜杠时，
只从本次所选源文件夹的根开始匹配。

例：
\cache
只匹配根下的 cache，不匹配 a\cache。

\src\cache
只匹配根下的 src\cache，不匹配 a\src\cache。

\*.log
只匹配根下的 .log 文件，不匹配子目录中的 .log 文件。

:\README
只匹配根下的无扩展名文件 README。

注意：
• “根”是本次所选源文件夹，不是磁盘根目录。
• 开头斜杠是根限定符，不是绝对路径。
• 不允许 Windows 盘符路径、UNC 路径、重复开头斜杠。
• 单独“\”或“/”没有模式正文，是无效规则。


六、路径分隔符与标准化
────────────────────────────────────────
• 可以输入 / 或 \，也可以混用。
• 合法启用规则保存时统一使用反斜杠 \。
• 合法启用规则末尾的一个或多个斜杠会移除。
• 放行正文不做上述斜杠处理。
• 内部连续分隔符形成空路径段，是无效规则。
• 不允许独立 . 或 .. 路径段。
• 非法路径不会通过自动清理伪装成合法路径。

例：
src/cache/       → src\cache
/src/cache/      → \src\cache
src/cache\sub/   → src\cache\sub
|src/cache/      → 保留原样

无效例：
src//cache
src\\cache
\\server\share
src\..\cache
src\.\cache


七、通配符
────────────────────────────────────────
【*：零个或多个字符，不跨目录】
例：
*.log
匹配任意层级的 .log 文件。

\*.log
只匹配源文件夹根下的 .log 文件。

test_*.py
匹配 test_.py、test_a.py。

cache*
匹配 cache、cache_old。
填写在哪份名单，就作用于哪类对象。

【?：恰好一个字符，不跨目录】
例：
part?.txt
匹配 part1.txt，不匹配 part.txt、part10.txt。

【**：独立路径段，支持零层或多层】
双星号必须独占一个路径段。

例：
src\**\*.py
匹配 src\a.py、src\lib\a.py、a\src\lib\deep\a.py。

\src\**\*.py
只从根下的 src 开始，不匹配 a\src\lib\a.py。

src\**\README
匹配 src\README、src\sub\README 等。

tools\**
目录名单中：
匹配 tools 本身及其后代目录。
命中 tools 后直接剪枝，不进入其子树。

文件名单中：
匹配 tools 下面的文件，包括多层子目录里的文件；
不会把 tools 目录本身当成文件匹配。

**
目录名单中匹配全部子目录，所选源文件夹本身不参与。
文件名单中匹配全部文件，包括无扩展名文件。

无效例：
a**b
**.py
***
src\**name\a.py

【其他符号不是高级语法】
方括号、花括号、感叹号、井号、分号、逗号和反引号等，
只要不属于非法字符，都作为普通名称字符匹配。

例：
[ab].txt 只匹配字面名称 [ab].txt，不表示 a.txt 或 b.txt。
!file.txt 不表示取反。
不支持正则表达式、Gitignore 取反或分号注释。


八、无效规则：红色
────────────────────────────────────────
启用条目不允许：
• 尖括号 < >、正文冒号 :、正文竖线 |、控制字符。
• Windows 盘符路径、UNC 路径、重复开头分隔符。
• 内部空路径段、独立 . 或 .. 路径段。
• 不独占路径段的双星号。
• 名称正文只包含半角空格。
• 名称组件以句点结尾，或句点后只有半角空格。
• 中间路径段以半角空格结尾。
• Windows 保留设备名，包括带扩展名形式。

保留设备名：
CON、PRN、AUX、NUL、CONIN$、CONOUT$、
COM1 至 COM9、LPT1 至 LPT9，
以及 COM¹、COM²、COM³、LPT¹、LPT²、LPT³。

无效例：
CON
con.txt
CON .txt
folder \file.txt
file.
bad|name
<none>

带通配符的组件不作为确定的设备名拒绝；
例如 CON*、COM?.txt 可以作为模式。

出现红项时，两份业务名单都不能保存。
可以修正、删除，或在行首明确加 | 放行；
程序不会自动删除红项。


九、空格风险：黄色
────────────────────────────────────────
模式正文首部或尾部的半角空格参与匹配。
规则可能合法，但会因这些空格而无法命中预期对象。

例：
 README*
README
:\ README*

诊断会列出首尾半角空格数量，
并以转义形式展示原文，使空格和控制字符可见。

普通名称内部的空格不是外围空格风险，
例如 my notes.txt、report .txt。

同一条目同时有无效问题和外围空格时，只显示红色诊断；
修正无效问题后，再按当前内容判断黄色风险。

黄色条目有效，不必删除；保存时需要当次确认。


十、红黄灰图例与保存流程
────────────────────────────────────────
红：无效，阻止保存。
黄：有效，有空格风险，需要当次确认。
灰：临时放行，不参与排除，不触发风险确认。

点击“保存”：
1. 一次读取两份名单。
2. 使用统一解释能力处理。
3. 回写标准化文本，显示红黄灰诊断。
4. 有红项：两份名单都不提交，不覆盖原已接受名单。
5. 只有黄项：可以确认保存，也可以返回检查。
6. 无红黄项：正常提交。
7. 磁盘保存失败：保留窗口，恢复共享配置的原名单。

标准化回写不等于保存成功。
输入框诊断行号对应回写后的当前行号。
关闭窗口时选择保存，使用相同校验与提交流程。

排除收藏：
• 添加或覆盖时校验所属名单，不提交业务名单配置。
• 改名时校验收藏正文，诊断行号对应收藏原文。
• 收藏回填保留原文，正式保存名单时才处理。
• 空名单可以保存配置，但空正文不能添加为收藏。


十一、扫描与常规合并
────────────────────────────────────────
【全景扫描】
排除文件不输出、不计入文件数量。
排除目录实际剪枝，其内部对象不计入输出统计。

【常规合并】
排除文件保留路径、文件段落和开始结束标记；
正文显示“文件命中排除名单，内容略”，不读取原文件正文。
排除目录内部不生成任何文件段落。

本次输出文件自身自动跳过。
按需合并使用独立路径名单，不应用这两份排除名单。


十二、快捷键与规则窗口
────────────────────────────────────────
Ctrl+F：查找
Ctrl+H：替换
Ctrl+Z：撤销
Ctrl+Y：重做

规则窗口支持滚动阅读。
点击“关闭”、按 Esc 或关闭标题栏，返回排除名单窗口。
"""


def show_exclusion_rules(parent):
    """展示只读帮助，不读取草稿、不提交配置。"""
    root = parent._root()
    previous_grab = parent.grab_current()
    dialog = tk.Toplevel(parent)
    dialog.title("排除名单 - 填写规则")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    dialog.grab_set()
    center_window(dialog, 900, 720)

    frame, text = styled_text_with_scrollbars(
        dialog, height=30, mono=False, wrap="word", readonly=True,
    )
    frame.pack(fill="both", expand=True, padx=16, pady=(16, 8))
    text.configure(state="normal")
    text.insert("1.0", EXCLUSION_RULES_TEXT)
    text.configure(state="disabled")

    buttons = styled_frame(dialog)
    buttons.pack(fill="x", padx=16, pady=(0, 14))

    def close():
        unregister_popup(dialog)
        dialog.destroy()
        return True

    styled_button(buttons, "关闭", close, width=10).pack(side="right")
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


def confirm_exclusion_diagnostics(parent, named_results):
    errors = any(result.has_errors for _, result in named_results)
    warnings = any(result.has_warnings for _, result in named_results)
    if not errors and not warnings:
        return True

    dialog = tk.Toplevel(parent)
    dialog.title("排除名单未保存" if errors else "排除名单空格风险确认")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    previous_grab = parent.grab_current()
    dialog.grab_set()
    center_window(dialog, 820, 550)
    accepted = False

    introduction = (
        "本次未保存。请修正或仅删除红色条目后重新保存。\n"
        "黄色条目有效，无需删除。\n"
        "行号对应下列所属名单正文：输入框校验使用回写后的行号，"
        "收藏改名校验使用收藏原文行号。\n"
        "条目采用转义展示，使空格及控制字符可见。"
        if errors else
        "黄色条目有效。外围空格会参与匹配，可能导致未命中。\n"
        "是否确认保存？行号对应下列所属名单正文："
        "输入框校验使用回写后的行号，收藏改名校验使用收藏原文行号。\n"
        "条目采用转义展示；首尾空格数量在风险原因中明确列出。"
    )
    tk.Label(
        dialog, text=introduction, justify="left", anchor="w",
        bg=THEME["bg"], fg=THEME["fg"], font=THEME["font_main"],
        wraplength=780,
    ).pack(fill="x", padx=16, pady=12)
    frame, text = styled_text_with_scrollbars(
        dialog, height=20, mono=True, wrap="word", readonly=True,
    )
    frame.pack(fill="both", expand=True, padx=16)
    text.configure(state="normal")
    text.insert("1.0", format_exclusion_diagnostics(named_results))
    text.configure(state="disabled")
    buttons = styled_frame(dialog)
    buttons.pack(fill="x", padx=16, pady=12)

    def close(confirm=False):
        nonlocal accepted
        accepted = confirm
        unregister_popup(dialog)
        dialog.destroy()
        return True

    styled_button(
        buttons, "返回修改" if errors else "返回检查",
        close, width=12,
    ).pack(side="right", padx=(8, 0))
    if not errors:
        styled_button(
            buttons, "确认保存", lambda: close(True), width=12, accent=True,
        ).pack(side="right")

    register_popup(dialog, close, exit_blocker=True)

    def return_to_editor(event=None):
        close()
        return "break"

    # 退出准备期间全局窗口管理器禁止关闭业务草稿；
    # 此窗口只返回校验决定，必须仍可取消，不应被该禁令锁住。
    dialog.bind("<Escape>", return_to_editor)
    dialog.protocol("WM_DELETE_WINDOW", return_to_editor)
    try:
        parent.wait_window(dialog)
    finally:
        unregister_popup(dialog)
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
    return accepted


class ExclusionDraftSession(TextDraftSession):
    """高亮派生于当前原文；标准化、确认和配置提交只发生在明确保存时。"""

    def __init__(self, dialog, commits, config, widgets, status):
        super().__init__(dialog, commits, config, widgets)
        self.status = status
        for field, _, _ in EXCLUSION_FIELDS:
            widget = widgets[field]
            widget.tag_configure(
                "exclusion_disabled",
                foreground=EXCLUSION_DISABLED_FOREGROUND,
                background=EXCLUSION_DISABLED_BACKGROUND,
            )
            widget.tag_configure(
                "exclusion_warning", background=EXCLUSION_WARNING_BACKGROUND,
            )
            widget.tag_configure("exclusion_error", background=THEME["danger_bg"])
            widget.edit_modified(False)
            widget.bind("<<Modified>>", self.on_modified, add="+")
            widget._prepare_favorite_content = (
                lambda content, display=True, selected=field:
                self.prepare_favorite(selected, content, display=display)
            )
            result = analyze_exclusion_list(self.baseline[field], self.rule_kind(field))
            self.render_highlights(widget, result, use_input_lines=True)
        self.status.set("高亮随当前原文显示；明确保存时才标准化并提交")

    def rule_kind(self, field):
        return next(
            kind for selected, _, kind in EXCLUSION_FIELDS if selected == field
        )

    def render_highlights(self, widget, result, use_input_lines=False):
        """只更新展示标签，不回写正文，不接受或保存配置。"""
        tags = {
            "disabled": "exclusion_disabled",
            "warning": "exclusion_warning",
            "error": "exclusion_error",
        }
        for tag in tags.values():
            widget.tag_remove(tag, "1.0", "end")
        for diagnostic in result.diagnostics:
            number = (
                diagnostic.input_line if use_input_lines
                else diagnostic.display_line
            )
            widget.tag_add(
                tags[diagnostic.state], f"{number}.0", f"{number}.end",
            )
        for tag in tags.values():
            widget.tag_lower(tag)
        widget.tag_raise("search_match")
        widget.tag_raise("search_current")
        widget.tag_raise("sel")

    def on_modified(self, event):
        widget = event.widget
        if self._destroyed or not widget.edit_modified():
            return
        widget.edit_modified(False)
        field = next(
            field for field, current in self.widgets.items() if current is widget
        )
        content = self.commits.read_text(widget)
        result = analyze_exclusion_list(content, self.rule_kind(field))
        self.render_highlights(widget, result, use_input_lines=True)
        self.status.set("已修改，尚未保存；高亮对应当前原文")

    def render_results(self, results, original_values):
        """明确处理正文时回写标准化文本，再按显示行号更新高亮。"""
        for field, result in results.items():
            widget = self.widgets[field]
            if result.normalized_text != original_values[field]:
                replace_text_keep_undo(widget, result.normalized_text)
            widget.edit_modified(False)
            self.render_highlights(widget, result)

    def accept(self, values):
        results = {
            field: analyze_exclusion_list(values[field], kind)
            for field, _, kind in EXCLUSION_FIELDS
        }
        self.render_results(results, values)
        named_results = tuple(
            (name, results[field]) for field, name, _ in EXCLUSION_FIELDS
        )
        errors = any(result.has_errors for result in results.values())
        warnings = any(result.has_warnings for result in results.values())
        self.status.set(
            "本次未保存：红色条目需要修正；黄色条目有效"
            if errors else
            "校验完成：黄色条目有效，等待当次确认"
            if warnings else
            "校验完成，正在保存"
        )
        confirmed = confirm_exclusion_diagnostics(self.dialog, named_results)
        if errors or not confirmed:
            return False
        normalized_values = {
            field: result.normalized_text for field, result in results.items()
        }
        completed = super().accept(normalized_values)
        self.status.set("已保存" if completed else "保存失败，内容仍在当前窗口")
        return completed

    def prepare_favorite(self, field, content, display=True):
        definition = next(
            definition for definition in EXCLUSION_FIELDS if definition[0] == field
        )
        _, name, kind = definition
        result = analyze_exclusion_list(content, kind)
        if display:
            self.render_results({field: result}, {field: content})
        else:
            # 收藏改名不回填当前草稿，诊断指向收藏原文行号。
            result = ExclusionListResult(
                result.normalized_text,
                result.rules,
                tuple(
                    replace(diagnostic, display_line=diagnostic.input_line)
                    for diagnostic in result.diagnostics
                ),
                result.line_map,
            )
            name += "（当前收藏正文）"
        self.status.set("收藏校验完成；名单配置尚未提交")
        confirmed = confirm_exclusion_diagnostics(self.dialog, ((name, result),))
        if result.has_errors or not confirmed:
            self.status.set("收藏未保存；名单配置尚未提交")
            return None
        return result.normalized_text


def create_exclusion_dialog(parent, title, config, commits, page):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    dialog.grab_set()
    center_window(dialog, 920, 780)

    header = styled_frame(dialog)
    header.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(
        header,
        text="快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做。",
        justify="left", anchor="w",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
        wraplength=730,
    ).pack(side="left", fill="x", expand=True, padx=(0, 12))
    styled_button(
        header, "填写规则", lambda: show_exclusion_rules(dialog), width=10,
    ).pack(side="right")

    legend = styled_frame(dialog)
    legend.pack(fill="x", padx=16, pady=(4, 4))
    tk.Label(
        legend, text="色块图例：", bg=THEME["bg"], fg=THEME["fg_label"],
        font=THEME["font_main"],
    ).pack(side="left", padx=(0, 8))
    for color, description in (
        (THEME["danger_bg"], "红：无效，阻止保存"),
        (EXCLUSION_WARNING_BACKGROUND, "黄：有效，有空格风险"),
        (EXCLUSION_DISABLED_BACKGROUND, "灰：临时放行，不参与排除"),
    ):
        tk.Label(
            legend, text="  ", bg=color, relief="solid", bd=1,
        ).pack(side="left", padx=(0, 5))
        tk.Label(
            legend, text=description, bg=THEME["bg"], fg=THEME["fg_label"],
            font=THEME["font_main"],
        ).pack(side="left", padx=(0, 16))

    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16)
    widgets = {}
    for field, name, _ in EXCLUSION_FIELDS:
        label = (
            "排除目录名称／相对路径"
            if field == "exclude_folders" else
            "排除完整文件名／相对路径"
        )
        widgets[field] = create_managed_text_box(
            body, label, config[field], height=11, mono=True, commits=commits,
            favorite_key=f"{page}_{field}", wrap_config_key=f"{page}.{field}",
        )
    status = tk.StringVar(master=dialog, value="尚未校验")
    session = ExclusionDraftSession(dialog, commits, config, widgets, status)
    buttons = styled_frame(dialog)
    buttons.pack(fill="x", padx=16, pady=12)
    tk.Label(
        buttons, textvariable=status, anchor="w",
        bg=THEME["bg"], fg=THEME["fg_dim"], font=THEME["font_main"],
    ).pack(side="left", fill="x", expand=True)
    styled_button(buttons, "取消", session.close, width=10).pack(
        side="right", padx=(8, 0),
    )
    styled_button(
        buttons, "保存", session.save_and_close, width=10, accent=True,
    ).pack(side="right")
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    parent.wait_window(dialog)
    return session.saved and not getattr(parent._root(), "_zipatch_closed", False)
