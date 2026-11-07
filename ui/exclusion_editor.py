# -*- coding: utf-8 -*-

import tkinter as tk
from core.constants import THEME
from ui.window_manager import center_window
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
• 空行和仅含半角空格的行是排版，保留位置、数量和空格数量。
• 开头、中间、末尾及连续空行都保留；排版不高亮、不参与排除。
• 不自动删除重复行，不调整顺序。
• 实际规则的首尾空格仍参与匹配，不自动删除。
• Tab、孤立回车及禁用控制字符不是排版，启用行仍按非法内容处理。
• 不支持行尾注释，备注可单独写成停用行。
• 不展开环境变量或 ~。
• 规则不要求对应文件或目录目前真实存在。
• 使用普通回车换行；Tab 不是规则分隔符。

例：
"src/cache/"    → src\cache
"*.log"         → *.log
""              → 删除双引号，保留这条空白排版行

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
• 根限定符或无扩展名条件之后的模式正文只包含半角空格。
  整行只有半角空格时属于排版，不是非法规则。
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
黄：有效，可能是首尾半角空格风险，也可能是结构等价重复。
灰：临时停用，不参与排除。
空白排版没有高亮。

同一名单中，标准化结构相同的合法启用规则，
后续项标黄并引用最早项的实际行号。
例如 *.LOG 与 *.log，SRC/cache/ 与 src\cache。
只比较相同结构及 ASCII 字母大小写差异，不判断一般包含关系。
cache 与 \cache、README* 与 :README*、
带实际空格与不带空格的规则不同。

重复项保留，不删除、不合并，也不增加确认。
首尾半角空格风险仍需要确认；同一行的原因一起展示。
插入光标移入对应物理行，可在底部状态区查看原因。
有选区时仍按插入光标所在行显示；离开后恢复实际状态。
确认、保存和关闭决定期间优先显示操作状态；
取消或返回修改后仍能继续编辑并查看当前原因。
保存失败提示保留至业务名单成功保存，不会被行诊断掩盖。

编辑时的颜色对应当前填写内容。
双引号实时删除；保存时整理合法规则的斜杠，不删除排版。
空白行也占据行号，请按当前实际物理行检查。

仅看到文字已整理，不代表保存成功；
如提示保存失败，请重试，草稿仍保留在窗口。

收藏保存同样校验对应名单。
收藏回填只实时删除半角双引号，其他正文保留；正式保存名单时再整理。
业务名单可以为空或只有排版，表示没有启用排除规则。
收藏新增、覆盖和改名校验至少需要一条非排版行；
| 或 |   这样的停用行也满足最低内容要求，红项仍不能保存收藏。
仅排版收藏操作会先拒绝，不为这次拒绝整理当前草稿。
历史仅排版收藏仍可显示、回填供编辑，不自动删除。
收藏和明确保存保留全部排版与重复项。
只修改空行或空格行也属于未保存变化，关闭时仍需决定保存或放弃。


十一、确认排除效果
────────────────────────────────────────
全景扫描：
排除文件不列出、不计入文件数量；
排除目录及其内部内容不列出、不计入统计。

按目录合并：
排除文件仍保留文件路径和段落，
正文使用本次“主动忽略标识”，默认“文件命中排除名单，内容略”；
明确可处理的正文失败使用独立“被动失败标识”并继续。
具体原因只记运行日志，不写块外JSON或占位正文；
完成弹窗分别提示主动忽略和被动失败数量。
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
            semantics="exclusion",
        )

    def values(self):
        return self.list_editor.values()

    def has_changes(self, values):
        return self.list_editor.has_changes()

    def accept(self, values):
        editor = self.list_editor
        with editor.operation_status("正在校验并保存排除名单"):
            completed = False
            try:
                results = editor.prepare(values)
                if not editor.confirm(results):
                    editor.set_status(
                        "本次未保存：名单需要修正"
                        if any(result.has_errors for result in results.values())
                        else "本次未保存：空格风险确认已取消"
                    )
                    return False
                completed = super().accept({
                    field: result.normalized_text
                    for field, result in results.items()
                })
                if completed:
                    editor.accepted(results)
                    editor.set_status("已保存")
                else:
                    editor.mark_save_failed()
                return completed
            finally:
                # 自然传播程序异常，同时保留操作未完成事实。
                # 红项拒绝和取消未尝试持久化，不登记保存失败。
                if not completed and editor.base_status == "校验完成，等待确认或保存":
                    editor.set_status("保存未完成，内容仍在当前窗口")

    def prepare_close(self):
        with self.list_editor.operation_status("正在处理排除名单关闭决定"):
            return super().prepare_close()


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
        (RULE_WARNING_BACKGROUND, "黄：空格风险或结构重复"),
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

    # 先为按钮保留完整空间，提示只消费剩余宽度。
    actions = styled_frame(buttons)
    actions.pack(side="right", anchor="s", padx=(12, 0))
    styled_button(actions, "取消", session.close, width=10).pack(
        side="right", padx=(8, 0),
    )
    styled_button(
        actions, "保存", session.save_and_close, width=10, accent=True,
    ).pack(side="right")

    # width=1 避免长提示提出过大的横向尺寸请求；
    # 实际宽度由剩余空间决定，窗口缩放时同步调整换行宽度。
    status_label = tk.Label(
        buttons, textvariable=status, anchor="w", justify="left",
        width=1, wraplength=1,
        bg=THEME["bg"], fg=THEME["fg_dim"], font=THEME["font_main"],
    )
    status_label.pack(side="left", fill="x", expand=True)

    def resize_status_wrap(event):
        status_label.configure(wraplength=max(1, event.width))

    status_label.bind("<Configure>", resize_status_wrap)
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    parent.wait_window(dialog)
    return session.saved and not getattr(parent._root(), "_zipatch_closed", False)
