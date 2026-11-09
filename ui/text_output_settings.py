# -*- coding: utf-8 -*-

"""合并与拆分共用格式设置模板；各页面独立草稿、收藏及显示偏好。"""

import tkinter as tk

from core.constants import THEME
from ui.message_dialogs import safe_show_error
from core.merged_format import OutputSettingsError, validate_output_settings
from ui.window_manager import center_window
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, take_modal_control


OUTPUT_HELP = r"""合并与拆分正文规则

合并入口与来源模式：
主页面只有一个“合并”按钮，模式只能单选。
仅目录：使用源文件夹和排除名单，不使用合并列表。
仅列表：使用合并列表，不使用排除名单；
全部启用规则为绝对路径时可不填写源文件夹，
相对规则仍以源文件夹为基准。
目录+列表：目录排序文件在前，列表补充文件在后。
仅目录和目录+列表必须填写有效源文件夹，
空值明确拒绝，不使用程序当前工作目录代替。
同一路径只合并一次，列表明确包含优先于目录文件排除；
被排除目录中的文件也可通过列表明确补入。
同路径已有目录位置保持，不因重复列表改变顺序。
空列表在目录+列表模式表示没有额外补充；
仅列表必须实际选中至少一个文件。
列表选择错误阻止使用列表的模式执行，不执行通过子集。
目录输出文件自身自动跳过；列表明确选中输出文件则拒绝。
合并列表弹窗只保存名单，保留说明、图例和检查记录，
不设置前言、后语，不提供文件合并执行入口。
所有模式统一使用“附加文本”中的前言、后语。
列表窗口最近检查的颜色不是执行资格，
主页面合并时完整检查本次所需列表一次。

标准化文本换行：
合并与拆分各有独立开关，默认关闭。
关闭保留完整正文的CRLF、LF、单独CR、空白和真实尾换行，
不使用或校验CRLF名单。输出仍为UTF-8，不保留来源编码或BOM。
开启只转换换行：命中CRLF名单使用CRLF，其余使用LF；
不裁剪空白、不压缩空行、不补源码末尾换行。
合并逐文件转换，合并包结构始终使用CRLF。

按目录合并的CRLF名单相对于本次源文件夹匹配；
开头一个斜杠限定源文件夹根。
按名单合并从每个来源绝对路径的尾部向前匹配，
开头斜杠不限定盘符根。
\scripts\*.cmd和scripts\*.cmd都可以匹配
E:\scripts\a.cmd与E:\Project\scripts\a.cmd。
普通路径段必须连续；跨层级使用scripts\**\*.cmd。
目录+列表模式中，源文件夹范围内文件使用目录根语义；
补充的外部文件使用绝对路径尾部匹配，不受源目录根限定。
仅列表模式全部使用绝对路径尾部匹配。
拆分指定目录模式相对于指定目录匹配，保留根限定；
原位模式按实际绝对目标路径尾部匹配，忽略开头根限定。

拆分正文与执行：
输入框是唯一源码输入，导入只把文件正文放入输入框。
输入框正文按既有配置完成点自动保存，切页及正常重启后保留；
不是每敲一个字符就写盘。保存失败明确提示，不假称已经持久化。
准备时只解析、转换和编码一次，全部待写成果固定在内存。
导入文件后来改变、移动或删除，不影响已收到的正文。
执行开始前复核全部固定目标一次，之后逐文件保护和发布，
只检查当前文件及其必要父目录，不反复读取全量工作成果。
当前目标变化仍拒绝旧任务；失败停止，不自动重试或回滚。
普通文件完整暂存后原子替换，硬链接仍通过原对象写回。
正式保护材料、清单和完整暂存仍保留必要完整性检查。
已完成成果不承担持续外部并发监控，不保证整批原子快照。

拆分位置与确认：
指定目录是写入范围及相对路径基准，可以尚不存在。
完整盘符绝对路径以盘符字母作为指定目录下的一级子目录，
保留盘符之后的全部层级；相对路径直接与指定目录拼接。
例如 E:\项目\src\a.py 写到 指定目录\E\项目\src\a.py，
src\a.py 写到 指定目录\src\a.py；不会写回来源绝对位置。
盘符目录统一使用大写字母。路径穿越、映射后冲突仍拒绝，
网络共享及设备路径仍不支持。
原位绝对路径直接定位；待处理相对路径需要明确绝对基准。
全部待写为绝对路径时，原位模式指定目录可留空。
全量检查、准备后统一确认，确认前不创建工作目录。
已有文件只覆盖或跳过；不再自动改名。
拆分自动备份初始开启，备份目录初始为空，请填写明确绝对路径。
指定目录和原位模式都不猜相对备份位置；关闭时不检查未使用位置。
开启时，每个文件改变前先保存必要原状态和正式恢复依据。
保存旧目标原字节及文件修改时间，不解码旧目标，不保存中间成果。
本次严格version 3备份可在拆分页或修改包页面通过同一正式恢复入口使用。
两个功能页的备份还原统一显示备份来源及备份目录，不使用执行模式的根目录。
恢复来源填写包含recovery.json的完整绝对任务目录，不填写files编号材料目录。
清单对象目标必须是绝对路径；目录子项相对于该已记录对象目录，不需要用户提供根。
开启还原前保护时，备份目录也必须是明确绝对路径；关闭时不使用该位置。
修改包执行修改仍使用项目根目录，修改备份目录仍允许相对项目根目录填写。
还原前保护与拆分自动备份是独立开关；保护使用备份目录设置。
完整原状态恢复成功且未选择保留来源时才清空来源字段，不删除材料。
部分保留、取消或失败均保留来源；保存重试不再次执行恢复。
原位拆分没有唯一成果目录，不自动打开未使用或仅作相对基准的指定目录。
恢复自动打开只针对本次实际生成的还原前保护任务，不猜历史目录。
旧version 2材料由隔离稳定旧程序使用，新系统不转换或兼容读取。
关闭时不保存覆盖前状态，覆盖前请核对风险。
备份或工作失败停止后续；前序成果和材料不自动撤销。
取消或任一执行失败后任务失效；执行失败立即停止后续，
此前已提交文件和已创建必要目录不自动撤销。
有实际待写文件、有跳过项且开启备份时，才发布拆分跳过日志。
日志存于本次备份任务文件夹，与recovery.json和编号材料放在同一任务目录。
关闭备份时，跳过原因仍显示在预演和运行日志中，不另存日志文件，
也不为了日志创建备份文件夹。取消或无待写不创建任务目录或发布日志。
目标检查遇到权限或其他系统故障时整份中断，不当作跳过。
其他业务日志位置不变；日志失败不重新还原文件。

主动忽略与被动失败标识：
两页面分别设置两个非空完整正文。
主动默认“文件命中排除名单，内容略”；
被动默认“文件读取或解码失败，内容未提供”。
两者不能相同或仅换行形式不同；可以多行，
首尾空白和真实尾换行都保留。
拆分先提取完整正文，再分别全文比较；
只把CRLF、LF、单独CR视为等价，不裁剪其他内容。
正常空文件不属于占位。
两个页面的标识应与本次包对应；真实源码也可能
恰好全文等于标识，请选用独特文本。

两种合并的读取：
使用UTF-8（含BOM）、GB18030、GBK严格解码，无Latin-1兜底。
明确不存在、权限拒绝、目录或父路径类型变化、
严格候选全部失败，均写被动标识并继续保留文件块。
成果发布后完成弹窗明确提示被动失败数量。
具体原因仅记运行日志，不进入占位正文或成果块外JSON。
其他I/O、资源不足、程序缺陷和发布故障仍中断。
名单最终选择检查仍须无红项且选中至少一个文件。

最后修改时间：
默认打开；路径下方显示“最后修改时间：YYYY-MM-DD HH:MM:SS”。
这是运行机器本地时区的秒级表示，不属于源码。
关闭不查询或生成逐文件时间行；成果生成时间仍保留。
单个时间无法取得时写“最后修改时间：未取得”，
日志记原因，完成弹窗列出数量和文件，不伪造时间。
同一秒多次修改、复制保留时间和外部调整都可能造成重复；
时间不能证明版本唯一或内容一致。

标记和协议分隔：
开始、结束标记须非空单行且不同；首尾空格保留。
开始标记不能以“最后修改时间：”开头，
避免与可选结构行混淆。合法的同次标记交换联合接受。
完整结束标记行是保留行；正文冲突拒绝生成。
正文中的节头与开始标记是普通正文。
完整正文后实际额外追加一个独立CRLF，
正文原有尾换行不抵扣；拆分仅移除紧邻结束行的
一个实际分行符，接受CRLF、LF及单独CR。
不裁剪其他正文或补尾换行。
外部工具改写的原换行不可恢复；
漏加额外分隔而正文仍有尾换行时，不能可靠辨认。
前言、后语中的完整文件节头可能成为块候选。
"""


class OutputFormatDraftSession(TextDraftSession):
    """标记和双占位同次接受，设置失败不留下部分配置。"""

    def __init__(self, dialog, commits, config, widgets, page):
        super().__init__(dialog, commits, config, widgets)
        self.page = page
        self.baseline = {
            field: widget.get("1.0", "end-1c")
            for field, widget in widgets.items()
        }

    def values(self):
        return {
            field: self.commits.read_text(widget, preserve_trailing_newlines=True)
            for field, widget in self.widgets.items()
        }

    def accept(self, values):
        try:
            validate_output_settings(self.page, {**self.config, **values})
        except OutputSettingsError as error:
            safe_show_error("标记与占位未保存", str(error), self.dialog)
            return False
        return super().accept(values)


def edit_output_format_settings(parent, commits, config, page):
    """共用四横行模板，页面配置和收藏分类分别定位，不同步对方。"""
    if page not in ("merge", "restore"):
        raise ValueError("标记与占位设置只适用于合并或拆分页面")
    if config is not commits.manager.config_data[page]:
        raise ValueError("标记与占位必须编辑对应页面的正式配置")

    page_title = "文件代码合并" if page == "merge" else "代码拆分还原"
    dialog = tk.Toplevel(parent)
    dialog.title(f"{page_title} - 标记与占位")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
    center_window(dialog, 1000, 760)
    tk.Label(
        dialog,
        text=f"当前只编辑“{page_title}”，不会修改另一个功能页。\n"
             "开始／结束标记必须非空、单行且不同；双占位可以多行，"
             "非空且不能换行等价。首尾空白保留。\n"
             "自动换行只改变显示；清空、导入和收藏回填只改变草稿，"
             "四项点击保存后联合生效。收藏管理独立保存。",
        bg=THEME["bg"], fg=THEME["fg_label"],
        font=THEME["font_main"],
        justify="left", anchor="w", wraplength=950,
    ).pack(fill="x", padx=16, pady=(12, 4))

    # 先保留底部动作空间，编辑区只消费剩余高度。
    row = styled_frame(dialog)
    row.pack(side="bottom", fill="x", padx=16, pady=12)

    # 四区同宽同高；标题和功能选项归同一managed text标题栏，
    # 不保留独占左侧的名称列，标题栏与输入框使用相同横向范围。
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16)
    body.grid_columnconfigure(0, weight=1)
    widgets = {}
    for number, (field, label) in enumerate((
        ("code_header_line", "开始标记（单行）"),
        ("code_footer_line", "结束标记（单行）"),
        ("ignore_text", "主动忽略标识"),
        ("failure_text", "被动失败标识"),
    )):
        body.grid_rowconfigure(
            number, weight=1, uniform="output_format_rows",
        )
        input_area = styled_frame(body)
        input_area.grid(
            row=number, column=0, sticky="nsew", pady=(0, 4),
        )
        widgets[field] = create_managed_text_box(
            input_area, label, config[field], height=4,
            commits=commits, favorite_key=f"{page}_{field}",
            enable_favorites=True, enable_clear=True,
            enable_wrap_toggle=True, enable_import_text=True,
            preserve_trailing_newlines=True, preserve_import_newlines=True,
            wrap_config_key=f"{page}.{field}",
        )

    session = OutputFormatDraftSession(dialog, commits, config, widgets, page)
    styled_button(row, "取消", session.close, width=10).pack(
        side="right", padx=(8, 0),
    )
    styled_button(
        row, "保存", session.save_and_close, width=10, accent=True,
    ).pack(side="right")
    register_popup(dialog, session.close, prepare_close=session.prepare_close)
    parent.wait_window(dialog)
    return session.saved and not getattr(parent._root(), "_zipatch_closed", False)


def show_output_help(parent):
    dialog = tk.Toplevel(parent)
    dialog.title("合并与拆分 - 正文规则")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
    center_window(dialog, 900, 740)
    frame, text = styled_text_with_scrollbars(
        dialog, height=30, wrap="word", readonly=True,
    )
    frame.pack(fill="both", expand=True, padx=16, pady=(16, 8))
    text.configure(state="normal")
    text.insert("1.0", OUTPUT_HELP)
    text.configure(state="disabled")
    row = styled_frame(dialog)
    row.pack(fill="x", padx=16, pady=(0, 14))

    def close():
        dialog.destroy()
        return True

    styled_button(row, "关闭", close, width=10).pack(side="right")
    register_popup(dialog, close)
    parent.wait_window(dialog)
