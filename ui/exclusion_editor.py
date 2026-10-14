# -*- coding: utf-8 -*-

import tkinter as tk
from core.constants import THEME
from core.paths import center_window
from ui.rule_list_editor import (
    RULE_DISABLED_BACKGROUND, RULE_WARNING_BACKGROUND, RuleListEditor,
)
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup, take_modal_control


EXCLUSION_FIELDS = (
    ("exclude_folders", "目录名单", "directory"),
    ("exclude_files", "文件名单", "file"),
)



EXCLUSION_RULES_TEXT = r"""排除名单填写规则

一、先选对名单
────────────────────────────────────────
【目录名单】
填写目录名称或相对于源文件夹的目录路径。
命中目录后，跳过该目录及其全部子目录和文件。
你选择的源文件夹本身不参与目录名单匹配。

【文件名单】
填写完整文件名、文件名通配符，
或相对于源文件夹的文件路径。

例：
cache
写在目录名单中，排除名为 cache 的目录及其内容。

README.md
写在文件名单中，匹配 README.md，
不匹配 README.md.bak。

名称采用完整匹配，不自动做子串匹配。
匹配不区分大小写，保存时保留原来的字母大小写。


二、每行一条规则
────────────────────────────────────────
• 不用逗号、分号或空格分隔多条规则。
• 名称内部的空格、逗号、分号、方括号等是普通字符。
• 输入、粘贴和收藏回填时实时删除全部半角双引号 "，包括路径内部的双引号。
• 删除双引号后完全为空的行会移除。
• 不自动删除重复行，不调整顺序。
• 不自动去掉首尾空格，空格参与匹配。
• 只有空格的行是无效规则。
• 不支持行尾注释，备注可单独写成停用行。
• 不展开环境变量或 ~。
• 规则不要求对应文件或目录目前真实存在。
• 使用普通回车换行；Tab 不是规则分隔符。

例：
"src/cache/"    → src\cache
"*.log"         → *.log
""              → 移除空行

单引号、反引号和中文引号不会自动删除。
填写绝对路径或网络共享路径无效；
这里填写的是名称或相对于源文件夹的匹配规则。


三、临时停用：行首 |
────────────────────────────────────────
删除双引号后，第一个字符为半角竖线 |，
表示该行不参与排除，让原本会被排除的对象暂时放行。
去掉行首 | 即可重新启用。
Ctrl+` 逐行切换行首一个 |；无选区只处理光标所在物理行。
有选区时各行独立切换；结束在下一行行首时不处理该下一行。
屏幕折行不算新行。空行变为 |，||abc 变为 |abc。
一次切换可一次撤销，不自动保存。

例：
|cache
|src/cache/
|*.log
|:.git*

停用行显示灰色。
除删除半角双引号外，停用正文保持原样，
不统一斜杠、不检查正文、不触发空格风险确认。

注意：
“ |cache”有前导空格，不是停用规则。
“:|README”也不是停用规则。
单独“|”是有效停用行。
分号 ; 不表示停用或注释。


四、无扩展名条件：文件名单行首 :
────────────────────────────────────────
只适用于文件名单，不适用于目录名单。

行首半角冒号 : 表示：
文件没有扩展名，并且满足冒号后的名称或路径规则。

例：
:
排除任意层级的全部无扩展名文件。

:README*
排除任意层级中，以 README 开头的无扩展名文件。

:.git*
匹配 .gitignore、.gitattributes、.gitconfig 等；
不匹配 .git.txt 或 .gitconfig.bak。

:\README*
只匹配源文件夹根下以 README 开头的无扩展名文件。

无扩展名示例：
README、LICENSE、.env、.gitignore。

有扩展名示例：
main.py、archive.tar.gz、.config.json。

只有文件名单中的单独“:”允许没有后续模式。
“:\”或“:/”没有模式正文，是无效规则。
冒号只能作为文件名单的行首条件，不允许正文冒号、
盘符冒号或“::”“:|”等组合。


五、匹配范围
────────────────────────────────────────
【默认：任意层级】
不加开头斜杠，可以从源文件夹下任意层级开始匹配。

cache
匹配 cache、a\cache、a\b\cache；
不匹配 mycache 或 cache_old。

src\cache
匹配 src\cache、a\src\cache；
不匹配 src\other\cache，因为普通路径段必须连续。

README.md
匹配任意层级中的 README.md。

【根限定：开头一个 \ 或 /】
只从本次源文件夹的根开始匹配，不是磁盘根目录。

\cache
只匹配源文件夹根下的 cache。

\src\cache
只匹配根下的 src\cache，不匹配 a\src\cache。

\*.log
只匹配源文件夹根下的 .log 文件。

:\README
只匹配源文件夹根下的无扩展名文件 README。

不允许盘符路径、网络共享路径或重复开头斜杠。
单独“\”或“/”没有模式正文，是无效规则。


六、路径分隔符
────────────────────────────────────────
可以输入 / 或 \，也可以混用。
合法启用规则保存时：
• 统一使用反斜杠 \。
• 移除末尾的一个或多个斜杠。

例：
src/cache/       → src\cache
/src/cache/      → \src\cache
src/cache\sub/   → src\cache\sub

停用行不做以上处理：
|src/cache/      → 保留原样

内部连续分隔符形成空路径段，是无效规则。
不允许独立的 . 或 .. 路径段。

无效例：
src//cache
src\\cache
src\..\cache
src\.\cache


七、通配符
────────────────────────────────────────
【*：零个或多个字符，不跨目录】

*.log
匹配任意层级的 .log 文件。

\*.log
只匹配源文件夹根下的 .log 文件。

test_*.py
匹配 test_.py、test_a.py 等。

cache*
匹配 cache、cache_old；
写在哪份名单，就作用于哪类对象。

【?：一个字符，不跨目录】

part?.txt
匹配 part1.txt，不匹配 part.txt 或 part10.txt。

【**：独立路径段，支持零层或多层】

src\**\*.py
匹配 src\a.py、src\lib\a.py、
a\src\lib\deep\a.py。

\src\**\*.py
只从根下的 src 开始，不匹配 a\src\lib\a.py。

src\**\README
匹配 src\README、src\sub\README 等。

tools\**
目录名单中，匹配 tools 本身及其后代目录，
因此整个 tools 目录会被跳过。
文件名单中，匹配 tools 下所有层级的文件，
不会把 tools 目录本身当成文件。

**
目录名单中匹配全部子目录，但不包括源文件夹本身。
文件名单中匹配全部文件，包括无扩展名文件。

双星号必须独占路径段。以下写法无效：
a**b
**.py
***
src\**name\a.py

不支持正则表达式、花括号扩展或取反。
[ab].txt 只匹配字面名称 [ab].txt。
!file.txt 不表示取反。


八、无效规则：红色
────────────────────────────────────────
启用规则中不允许：
• 尖括号 < >、正文冒号 :、正文竖线 | 或控制字符。
• 盘符路径、网络共享路径或重复开头分隔符。
• 内部空路径段、独立 . 或 .. 路径段。
• 不独占路径段的双星号。
• 名称正文只包含半角空格。
• 名称组件以句点结尾，或句点后只有半角空格。
• 中间路径段以半角空格结尾。
• Windows 保留设备名，包括带扩展名的形式。

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

CON*、COM?.txt 等带通配符的写法可以作为模式。

出现红项时，两份业务名单都不能保存。
可以修正、删除，或在行首加 | 暂时停用；
程序不会替你删除红项。


九、首尾空格风险：黄色
────────────────────────────────────────
规则正文首部或尾部的半角空格参与匹配，
可能导致没有匹配到你预期的名称。

例如：
“ README*”开头有一个空格。
“README ”末尾有一个空格。

上面的中文引号只是为了展示空格，不要照着输入。

黄色表示规则有效，但有空格风险。
如果空格不是你的本意，请删除后保存；
如果确实要匹配带空格的名称，可以确认保存。

普通名称内部的空格不属于首尾空格风险，
例如 my notes.txt、report .txt。

有非法字符又有首尾空格时，先显示红色问题；
修正后再判断是否仍有黄色风险。

诊断中的“转义展示”用于看清空格、反斜杠和控制字符，
不要求你把路径改写成转义形式。


十、核对提示与保存
────────────────────────────────────────
红：无效，必须修正或停用后才能保存。
黄：有效，有首尾空格风险，保存时需要确认。
灰：临时停用，不参与排除。

编辑时的颜色对应当前填写内容。
双引号实时删除；保存时再整理空行和斜杠，行号可能随之变化；
请按提示中显示的当前行号检查。

仅看到文字已整理，不代表保存成功；
如提示保存失败，请重试，草稿仍保留在窗口。

收藏保存同样校验对应名单。
收藏回填只实时删除半角双引号，其他正文保留；正式保存名单时再整理。
空名单可以保存，空正文不能添加为收藏。


十一、确认排除效果
────────────────────────────────────────
全景扫描：
排除文件不列出、不计入文件数量；
排除目录及其内部内容不列出、不计入统计。

按目录合并：
排除文件仍保留文件路径和段落，
正文显示“文件命中排除名单，内容略”；
排除目录内部不生成文件段落。

本次输出文件自身自动跳过。
按名单合并使用独立的文件选择名单，
不使用这里的两份排除名单。
"""


def show_exclusion_rules(parent):
    """展示只读帮助，不读取草稿、不提交配置。"""

    dialog = tk.Toplevel(parent)
    dialog.title("排除名单 - 填写规则")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
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



class ExclusionDraftSession(TextDraftSession):
    """排除业务拥有两份名单的联合接受与提交。"""

    def __init__(self, dialog, commits, config, widgets, status):
        super().__init__(dialog, commits, config, widgets)
        self.status = status
        self.list_editor = RuleListEditor(
            dialog, commits, widgets, EXCLUSION_FIELDS, status, config,
        )

    def values(self):
        return self.list_editor.values()

    def has_changes(self, values):
        return self.list_editor.has_changes()

    def accept(self, values):
        results = self.list_editor.prepare(values)
        if not self.list_editor.confirm(results):
            return False
        completed = super().accept({
            field: result.normalized_text for field, result in results.items()
        })
        if completed:
            self.list_editor.accepted(results)
        self.status.set("已保存" if completed else "保存失败，内容仍在当前窗口")
        return completed


def create_exclusion_dialog(parent, title, config, commits, page):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
    center_window(dialog, 920, 780)

    header = styled_frame(dialog)
    header.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(
        header,
        text="快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做，Ctrl+` 逐行停用／启用。",
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
        (RULE_WARNING_BACKGROUND, "黄：有效，有空格风险"),
        (RULE_DISABLED_BACKGROUND, "灰：临时停用，不参与排除"),
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
