# -*- coding: utf-8 -*-

"""两个正式文本写入业务共用的换行设置。"""

import tkinter as tk

from core.constants import THEME
from ui.window_manager import center_window
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.rule_list_editor import (
    RULE_DISABLED_BACKGROUND, RULE_WARNING_BACKGROUND, RuleListEditor,
)
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, unregister_popup, take_modal_control


NEWLINE_HELP = r"""CRLF 文件名单填写规则

LF、CRLF 都表示换行。
LF 常用于跨平台文本，CRLF 常用于 Windows 批处理。
初始名单为 *.bat 和 *.CMD，大小写不影响匹配。

修改包“标准化文本换行”默认打开：
打开时，命中任意启用规则使用 CRLF；未命中使用 LF。
空名单或全部停用时，修改包实际写入的文本使用 LF。
关闭时，保留最终正文原有换行，不使用或校验本名单。
开启标准化只转换换行形式，不裁剪空白、不补末尾换行。
关闭标准化不表示保留原文件编码或 BOM。

合并与代码拆分还原各有独立标准化开关，默认关闭：
开启时命中使用 CRLF，未命中使用 LF；
关闭时保留完整正文，不使用或校验本名单。
修改包的开关不控制代码拆分还原，也不控制合并。
合并逐正文转换，容器结构保持 CRLF；不补源码末尾换行。

本设置不影响备份恢复、路径全景扫描或文件复制移动。

一、每行一条文件规则
填写完整名称、通配符或相对路径，不用逗号、分号分隔规则。
输入、粘贴和回填时实时删除全部半角双引号，包括路径内部的双引号。
保存时忽略空行，保留重复规则和顺序。
不展开环境变量或 ~，不支持行尾注释。
名称里的分号、逗号、方括号和内部空格是普通字符。
不自动删除首尾空格；只有半角空格的规则无效。
规则不要求目前存在对应文件，零命中不是错误。

例：
*.bat
*.CMD
启动脚本.cmd
启动*.cmd
scripts\*.cmd
tools\**\*.bat

二、路径和根限定
修改包以本次项目根目录为基准。
代码拆分的指定目录模式以指定目录为基准，保留根限定。
原位模式按实际绝对目标路径尾部匹配，忽略开头根限定。
拆分只有覆盖或跳过，不自动改名；按实际目标决定换行。
按目录合并相对于本次源文件夹匹配，开头斜杠限定该根。
按名单合并从来源绝对路径尾部向前匹配，开头斜杠不限定盘符根：
\scripts\*.cmd 和 scripts\*.cmd 都能匹配
E:\scripts\a.cmd 与 E:\Project\scripts\a.cmd。
普通路径段必须连续；跨层级使用 scripts\**\*.cmd。
此区别只作用于合并换行选择，不改变文件选择名单或排除规则。
主页面统一通过“合并”执行，模式为仅目录、仅列表、目录+列表。
仅列表全部按绝对路径尾部匹配。
目录+列表在源文件夹范围内保留目录根语义，
补充的外部文件按绝对路径尾部匹配。

不加开头斜杠，可以从任意层级开始匹配。
开头一个 \ 或 /，在指定目录模式只从指定目录根开始匹配；
原位模式忽略此根限定，从实际绝对目标路径尾部匹配。
scripts\*.cmd 匹配 scripts 当前层级的 CMD 文件。
\*.cmd 只匹配根层文件，不匹配 scripts\start.cmd。

合法启用规则保存时统一反斜杠并移除末尾斜杠。
不允许盘符、UNC、独立 . 或 ..、内部连续分隔符。

三、通配符
* 匹配一个路径段内零个或多个字符，不跨目录。
? 匹配一个路径段内一个字符。
** 必须独占路径段，支持零层或多层目录。
tools\**\*.bat 匹配 tools 及其子目录的 BAT 文件。
不支持 a**b、**.bat、***、正则、花括号扩展或取反。
[ab].cmd 匹配字面文件名 [ab].cmd。

四、无扩展名条件
行首 : 表示文件没有扩展名并满足后面的模式。
单独 : 匹配所有无扩展名文件。
:README* 匹配以 README 开头的无扩展名文件。
:.git* 可匹配 .gitignore，不匹配 .git.txt。
:\README 只匹配根层无扩展名文件 README。
单独 : 有效；:\、:/、:: 和正文冒号无效。

五、停用和颜色
删除双引号后，第一个字符为 | 表示停用。
|*.cmd 不参与 CRLF 选择；去掉 | 可以重新启用。
单独 | 有效，有前导空格的“ |...”不是停用。
Ctrl+` 逐行切换行首一个 |；无选区处理光标行，有选区各行独立切换。
选区结束在下一行行首时不处理该下一行；屏幕折行不算新行。
空行变为 |，||abc 变为 |abc。一次切换可一次撤销，不自动保存。
停用正文除删除双引号外保持原样，不校验正文。

红：无效，阻止保存。可以修正、删除或加 | 停用。
黄：有效，首尾半角空格有风险，保存时需要当次确认。
灰：停用，不参与 CRLF 选择。
普通名称内部空格不一律标黄。

禁止控制字符、非法路径结构、以句点结尾的名称、
中间路径段末尾空格和 Windows 保留设备名。
例如 CON、NUL、CON .txt 无效；CON* 可以作为模式。

六、保存和收藏
编辑、查找替换、撤销重做、清空和失焦只改变草稿。
明确保存时整理正文并校验；看到正文整理不代表保存成功。
保存失败保留窗口和草稿，可以重试或选择放弃。
空名单可以保存，空正文不能添加为收藏。

收藏保存校验当前收藏正文，不提交未保存的业务名单。
支持新增、同名覆盖、改名、删除和原文回填。
收藏改名校验收藏原文，不回填当前草稿。
收藏回填只实时删除半角双引号，其他正文保留；正式保存名单时再整理。

自动换行只控制文本框显示，不决定成果文件换行。
设置只影响之后准备的新任务。
已经固定的修改包预演任务不会随设置变化。
"""


def show_newline_help(parent):

    dialog = tk.Toplevel(parent)
    dialog.title("换行设置 - 填写规则")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
    center_window(dialog, 860, 720)
    frame, text = styled_text_with_scrollbars(
        dialog, height=30, wrap="word", readonly=True,
    )
    frame.pack(fill="both", expand=True, padx=16, pady=(16, 8))
    text.configure(state="normal")
    text.insert("1.0", NEWLINE_HELP)
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



class NewlineDraftSession(TextDraftSession):
    def __init__(self, dialog, commits, widget, status):
        super().__init__(
            dialog, commits, commits.manager.config_data["settings"],
            {"crlf_file_list": widget},
        )
        self.status = status
        self.list_editor = RuleListEditor(
            dialog, commits, self.widgets,
            (("crlf_file_list", "CRLF 文件名单", "file"),), status,
            commits.manager.config_data["settings"],
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


def edit_newline_settings(parent, commits):
    dialog = tk.Toplevel(parent)
    dialog.title("换行设置")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
    center_window(dialog, 900, 700)
    header = styled_frame(dialog)
    header.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(
        header,
        text="LF、CRLF 都表示换行。初始名单为 *.bat 和 *.CMD。\n"
             "修改包标准化默认打开；打开时命中使用 CRLF，未命中使用 LF。\n"
             "修改包关闭标准化时保留原换行，不使用或校验本名单。\n"
             "合并与拆分各有独立开关，默认关闭；开启时才使用本名单。\n"
             "按目录相对于源目录；按名单从绝对路径尾部匹配。\n"
             "备份恢复、路径扫描及复制移动不受影响。\n"
             "快捷键：Ctrl+F 查找，Ctrl+H 替换，Ctrl+Z 撤销，Ctrl+Y 重做，Ctrl+` 逐行停用／启用。",
        bg=THEME["bg"], fg=THEME["fg_label"], font=THEME["font_main"],
        anchor="w", justify="left", wraplength=740,
    ).pack(side="left", fill="x", expand=True)
    styled_button(
        header, "填写规则", lambda: show_newline_help(dialog), width=10,
    ).pack(side="right")
    legend = styled_frame(dialog)
    legend.pack(fill="x", padx=16, pady=4)
    for color, description in (
        (THEME["danger_bg"], "红：无效，阻止保存"),
        (RULE_WARNING_BACKGROUND, "黄：有效，有空格风险"),
        (RULE_DISABLED_BACKGROUND, "灰：停用，不参与 CRLF 选择"),
    ):
        tk.Label(legend, text="  ", bg=color, relief="solid", bd=1).pack(
            side="left", padx=(0, 5),
        )
        tk.Label(
            legend, text=description, bg=THEME["bg"], fg=THEME["fg_label"],
            font=THEME["font_main"],
        ).pack(side="left", padx=(0, 16))
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16)
    widget = create_managed_text_box(
        body, "CRLF 文件名单",
        commits.manager.config_data["settings"]["crlf_file_list"],
        height=20, mono=True, commits=commits,
        favorite_key="crlf_file_list", wrap_config_key="settings.crlf_file_list",
    )
    status = tk.StringVar(master=dialog)
    session = NewlineDraftSession(dialog, commits, widget, status)
    row = styled_frame(dialog)
    row.pack(fill="x", padx=16, pady=12)
    tk.Label(
        row, textvariable=status, bg=THEME["bg"], fg=THEME["fg_dim"],
        font=THEME["font_main"], anchor="w",
    ).pack(side="left", fill="x", expand=True)
    styled_button(row, "取消", session.close, width=10).pack(side="right", padx=(8, 0))
    styled_button(row, "保存", session.save_and_close, width=10, accent=True).pack(side="right")
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    parent.wait_window(dialog)
    return session.saved and not getattr(parent._root(), "_zipatch_closed", False)