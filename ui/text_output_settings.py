# -*- coding: utf-8 -*-

"""合并首尾标签设置及共用正文帮助；拆分边界只从来源声明读取。"""

import tkinter as tk

from core.constants import THEME
from ui.message_dialogs import safe_show_error
from core.merged_format import OutputSettingsError, validate_output_settings
from ui.window_manager import center_window
from ui.dialogs import TextDraftSession, create_managed_text_box
from ui.theme import styled_button, styled_frame, styled_text_with_scrollbars
from ui.window_manager import register_popup, take_modal_control


OUTPUT_HELP = r"""合并、拆分与备份还原规则

一、合并来源与选择

主页面三个按钮直接确定本次来源，不需要另选模式：
• 仅目录：使用源文件夹和排除名单。
• 仅列表：使用合并列表，不使用排除名单。
• 目录+列表：目录排序在前，列表补充在后。
目录模式必须填写源文件夹，不使用程序当前目录代替。
仅列表全部为绝对规则时可不填源文件夹；相对规则仍需要有效基准。

列表明确包含优先于目录文件排除；被排除目录内文件也能明确补入。
同一名义路径保留首次位置；同一实际普通文件的别名只生成一个块、
只取得一次正文，并保留全部实际使用的入口依赖。
实际别名要求不同换行成果或结构叶子同时具有后代时拒绝。
目录模式跳过输出文件；列表明确选中输出文件或其实际对象则拒绝。

主页面每次重新准备来源，不把名单编辑器的颜色当作执行资格。
后台检查不弹授权窗口、不进入未授权链接目标。
合并先定位全部列表通配符固定前缀及目录来源根，再集中勾选链接枚举范围；
尚未进入这些链接实际目录时即可局部跳过，不从未选范围取得文件。
同一任务同一名义入口和完整观察只展示一次，目录和列表共用决定。
不同入口分别展示；新任务或路径定义变化不复用旧决定。
第一阶段只允许枚举所选普通子树元信息，不读取正文或进入途中其他链接目标。
第二阶段选择具体成果块，两阶段不能互相替代。
扫描链接源根同样在首次实际枚举前勾选；全不选产生空成果，取消不发布。
普通目录及三类链接结构作为来源块输出；必要空目录保留。
全部来源元信息及输出隔离先检查，再由共用机制决定范围。
扫描、合并、修改包、拆分和备份还原统一使用链接筛选政策：
有效展示范围全为普通项时静默保留；存在链接相关项时才弹筛选，
普通项自动保留，固定跳过项不能勾回。
业务分别提供自己的固定操作计划，不重复实现窗口筛选及决定投影。
链接筛选不替代完整预演、强制保护或必要的最终执行确认。
未选块不读取正文、不输出，不保留该块后续访问依赖。
全不选与取消不同；空容器不具有可拆分文件块。
所有模式共用主页面“附加文本”。

二、正文、遗漏名单与时间

普通正文按严格编码候选解码，输出UTF-8，不保留来源编码或BOM。
带明确BOM的UTF-16按标记严格解码；损坏时不猜其他编码；
UTF-32标记拒绝；无BOM不猜UTF-16。
无标记正文使用UTF-8、GB18030、GBK严格候选，
不使用替代字符或Latin-1兜底。

目录来源先按名称排除，排除目录不进入，排除文件不读正文。
排除及未授权项不生成正文或占位块；
明确可处理的正文读取／严格解码失败同样不生成块。
成果末尾集中列主动忽略和被动失败名单，附路径、类别及原因。
名单决定止于目录时只列该目录，不枚举或重复展开后代。
显式列表包含仍可补回目录来源排除的文件。
其他资源、安全、来源漂移及发布故障仍中断，不伪装遗漏后成功。
完成摘要区分主动忽略、正文失败和时间缺失。

合并不设置或发布占位正文，拆分也不再配置或比较忽略标识。
普通文件块里的全部正文均按真实内容处理，包括空文件和原来的占位文字。
成果尾部遗漏名单仅作说明，不作为拆分重建记录。
旧成果若含占位文字，不再自动跳过；执行拆分前请核对这些文件块。

合并“记录文件修改时间”默认打开：
只为普通文件记录本机本地时区秒级修改时间，不属于源码。
无法取得时显示“未取得”，日志及摘要明确提示，不伪造时间。
关闭后不查询或生成普通文件时间行；成果生成时间仍保留。
结构块不携带文件正文或普通文件时间。
时间不能证明内容版本唯一或相同。

三、换行与动态容器

合并、拆分各有独立标准化开关，默认关闭。
关闭保留正文CRLF、LF、单独CR、空白和真实尾换行，不校验CRLF名单。
打开只转换换行：命中名单用CRLF，否则LF；
不补末尾换行、不压缩空行、不裁剪空白。
修改包的独立标准化开关默认打开，不控制合并或拆分。

目录合并相对名义源根匹配，保留名单根限定；
列表合并从名义绝对路径尾部匹配，忽略开头根限定。
组合模式根内用目录语义，补充外部来源用路径尾部语义。
拆分指定目录模式按名义映射位置相对基准匹配；
原位模式按实际绝对位置尾部匹配。

合并容器第一行ZIPATCH_MERGED声明是本层边界权威，
新成果使用version 2，包含两位字母数字标识和可读首尾标签。
两位标识有3844种组合，节头、开始和结束行直接追加标识，
不再附加空格或方括号。
标识检查全部已获取正文、路径、结构、时间及附加文本；
冲突则更换标识，不重新获取来源。
全部候选均冲突时明确停止，不生成边界有歧义的成果。
此前version 1的32位长标识容器仍可读取，但新成果不再生成旧格式。
开始／结束设置只属于合并生成标签；
拆分没有旧标记设置或参数，直接读取来源第一行。
声明后的本层节头、开始、结束和包尾使用同一标识。
不搜索正文中的声明，不递归拆开嵌套容器。
旧标记、嵌套声明及其他完整合并成果均按普通正文保留。

容器结构使用CRLF；每份正文后额外追加一个独立协议分行符，
原正文尾换行不抵扣；拆分只移除紧邻结束行的一个实际协议分行符。
空文件、末尾空行、无末尾换行分别保留。
声明错误、截断、缺少本层包尾、包尾后非空内容整份拒绝。
只接受新声明式来源，不自动读取旧无声明格式。
动态边界不能恢复外部工具已改写的正文换行。

四、链接与扫描

文件软链接、目录软链接、Junction保存系统实际原定义；
直接引用和最终实际对象不是原定义，不能互相代替。
目录链接及Junction默认是叶子，不展开目标；
文件链接结构块同样不携带目标正文。
相对原定义保持原样，换父目录后直接引用可能变化，
准备时展示该影响，不偷偷改成绝对目标。

扫描只输出所选文件和链接入口，不读取正文。
普通目录统计保留已枚举的结构上下文，不冒充所选文件数量。
勾选大小／日期时，所选链接目标元信息另行明确授权：
普通文件显示目标字节数与修改时间；
目录只显示自身修改时间，不递归计算内容大小；
不存在目标明确不可用。
发布前核对固定来源和目标元信息，变化则停止。

五、拆分位置、选择与执行

唯一输入是源码框快照；导入只是把正文放入输入框。
按既有配置完成点保存，不是每敲一字写盘。
导入源文件后来变化、移动或删除，不改变已收到的快照。
来源只解析、转换和编码一次，执行消费固定成果。

指定目录模式：
完整盘符路径映射到指定目录下的大写盘符字母目录，并保留后续层级。
例如E:\项目\a.py映射到指定目录\E\项目\a.py。
相对路径直接拼接指定目录，不写回原绝对来源。
原位模式：绝对路径直接定位；相对路径需要明确绝对基准。
路径穿越、映射后重复、叶子后代、实际范围冲突拒绝；
网络共享和设备表达不支持。

普通文件覆盖／跳过政策保持；不自动改名，不删除目录覆盖成正文。
目录记录只要求目录存在，不清空未列出的成员。
代码拆分不创建、删除或改指向链接，只创建普通文件和目录。
目标根、各级目录或末端文件已有链接时，先展示原定义和实际位置，
由用户筛选授权后沿既有路线工作，链接保持不变。
目录软链接和Junction来源结构只作为名义目录要求；
缺失时创建普通目录，不按来源定义创建链接。
文件链接结构只有原定义而没有正文时，明确报告并跳过：
不读取其保存定义指向的对象，不编造空文件，不把定义JSON当正文。
悬空文件链接没有既有实际正文对象时拒绝，不猜测创建目标。
所选子项可以按固定计划创建必要普通父目录，
不因未选父目录记录而取得清空父目录权限。
未选记录不获取旧正文、不保护、不执行。
经链接正文访问先展示实际位置并授权，访问依赖不变成保护对象。
完整准备及执行确认之前不创建工作目录。
取消、执行尝试或失败后旧任务失效。

六、修改包目录枚举与OP选择

修改包先检查全部参数、入口类型及名义路径冲突；
任何一项失败，不开始目录树枚举或正文获取。
需要经目录链接父链枚举实际目录时，先集中按OP勾选范围。
一个OP的来源和目标共同选择，不拆开两端。
未选OP不枚举、不读取正文、不保护、不执行，
最终OP窗口不能重新勾回；未选访问依赖不保留到执行期。
全不选仍可继续准备其他OP，取消则整包停止。
枚举只查询普通子树元信息，树内目录链接保持叶子。
之后展示完整OP结构及链接定义，再选择最终工作范围；
该阶段才授予所选范围的正文获取资格，完整预演后仍须执行确认。
两次选择只属于本任务，不保存为未来授权。
选择等待后固定入口改指向，停止，不从新现场扩大范围。
实际别名、来源冲突、正文定位、材料或联合检查失败仍整包拒绝。

六、强制保护、材料与失败处理

修改包、拆分、主动备份还原均强制保护，不提供关闭开关。
全部必要原状态、严格version 4清单和backup.complete成功后，
才开始首个工作改变；原先不存在只记录不存在，不造空材料。
无实际工作不创建空保护任务。
备份失败清理本次唯一任务目录，失败则提示残余并交人工处理；
不删除其他任务或原有父目录，不继续工作。

实际工作对象由共同保护投影拥有；纯访问依赖不成为恢复对象。
链接保存为独立普通定义材料，混合树内同样如此；
材料中不创建活动源链接，不读取引用目标正文。
材料、完整定义、摘要、清单编号及位置严格对应。
拒绝缺失、损坏、额外文件或空目录、活动链接、共享材料、
重复或未知字段及范围矛盾，不猜测旧备份完整性。

材料位于任务files目录，保留原名及相对层级。
单卷有适用基准时，基准与全部对象父目录取共同位置；
无适用基准或跨卷时按卷分组，避免重名。
布局在任务准备时固定；不改变清单绝对恢复位置。
文件可人工取用，正式恢复仍要求完整清单和严格材料核验。

普通单名称正文完整暂存后发布；
现存硬链接正文写回原对象，保留关联关系，不扫描其他名称。
入口复制覆盖只替换指定名称，不写入旧硬链接共享正文。
原生名称操作失败不切换复制删除。
新名称独立重建不等于恢复原硬链接拓扑。

修改包工作失败按原固定范围和真实步骤尝试自动回滚。
主动恢复工作失败按还原前保护及实际步骤尝试回滚。
自动回滚不二次备份、不自动重试或续执行；
无法可靠对应现场或备份变化时停止并保留备份。
原材料同名同字节但换成新对象，也不重新取得回滚来源资格。
清单事实发布失败使用本任务已确认文档及固定原材料，
不重读失败清单，不使用未发布候选或旧格式。
拆分失败沿用停止政策，不自动回滚，保留前序成果和备份。
工作已完成后的辅助日志故障不冒充工作未执行，不启动工作回滚。
拆分有实际工作及跳过项时，工作后在本次备份任务内发布跳过日志；
取消或无工作不单为日志创建任务。

七、独立“备份还原”页面

修改包页只执行修改包，拆分页只拆分容器；
主动恢复只在第五页“备份还原”进行。
来源填写含recovery.json与backup.complete的完整绝对任务目录，
不是files目录。还原保护目录也必须是明确绝对路径。
接受新的有效来源后，保护目录跟随来源上一级；
手动覆盖保持，再次改变有效来源重新跟随。
不要求用户填写保护项目根；材料布局基准内部提供，
不以保护保存位置解释或改变清单恢复目标。

可按清单对象选择恢复；一个完整目录树是一个对象，
不任意拆开树内成员。未选对象不获取当前正文、不保护、不执行。
原清单和引用材料仍整体严格检查，选择不绕过损坏来源。
恢复按内容、存在性和结构比较，仅时间不同不触发恢复。
父链经过目录链接时，首次旧正文获取或目录枚举前授权实际范围；
随后完整预检及所选执行确认，执行及回滚复核原访问依赖。
改指向则停止，不访问新指向，不扩大恢复范围。

本页的备份原状态恢复与代码拆分不同：
为准确恢复先前删除或改变的入口，备份恢复及自动回滚仍可按固定依据
移除或重建链接；只用保存原定义，不把定义JSON写成普通正文。
代码拆分不创建或消灭链接的政策不改变备份恢复及明确入口操作。
恢复目录树可以移除其固定范围内的额外内容，应核对完整预检。
全部所选对象最终核验后才报所选完成；
部分恢复不宣称整份备份全部恢复。
仅完整恢复成功且未勾选保留来源时才清空来源字段；
部分恢复、取消或失败保留来源，不删除备份。
自动打开仅针对本次实际产生的还原前保护任务，不猜历史目录。
来源备份和还原前保护备份在恢复失败时均保留。

所有新边界检查都是固定事实比较，不是文件锁。
不承诺完整元数据、原硬链接拓扑、全部竞态、
整批原子事务或断电自动恢复。
新系统只读取严格version 4，不兼容转换旧恢复格式，也不删除旧材料。
恢复清单版本与Zipatch V3修改包版本不是同一个概念。
"""


class OutputFormatDraftSession(TextDraftSession):
    """首尾标签同次接受，设置失败不留下部分配置。"""

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
            safe_show_error("首尾标记未保存", str(error), self.dialog)
            return False
        return super().accept(values)


def edit_output_format_settings(parent, commits, config, page):
    """只编辑合并首尾标签；拆分不提供标识设置入口。"""
    if page != "merge":
        raise ValueError("首尾标记设置只适用于合并页面")
    if config is not commits.manager.config_data[page]:
        raise ValueError("首尾标记设置必须编辑合并页面的正式配置")

    page_title = "文件代码合并"
    setting_title = "容器标记"
    definitions = (
        ("code_header_line", "开始标记（单行）"),
        ("code_footer_line", "结束标记（单行）"),
    )
    boundary_notice = (
        "开始／结束文字只作可读标签；两位动态标识自动避撞并写入第一行声明。"
        "标签必须非空、单行且不同。\n"
    )
    dialog = tk.Toplevel(parent)
    dialog.title(f"{page_title} - {setting_title}")
    dialog.configure(bg=THEME["bg"])
    dialog.transient(parent)
    take_modal_control(dialog)
    center_window(dialog, 1000, 540)
    tk.Label(
        dialog,
        text=f"当前只编辑“{page_title}”，不会修改另一个功能页。\n"
             + boundary_notice
             + "忽略及正文失败项只列成果末尾名单，不生成文件块。\n"
             + "自动换行只改变显示；清空、导入和收藏回填只改变草稿。"
             "应用：联合保存但不关闭；保存：成功后关闭；"
             "取消：放弃未应用的修改。收藏管理独立保存。",
        bg=THEME["bg"], fg=THEME["fg_label"],
        font=THEME["font_main"],
        justify="left", anchor="w", wraplength=950,
    ).pack(fill="x", padx=16, pady=(12, 4))
    row = styled_frame(dialog)
    row.pack(side="bottom", fill="x", padx=16, pady=12)
    body = styled_frame(dialog)
    body.pack(fill="both", expand=True, padx=16)
    body.grid_columnconfigure(0, weight=1)
    widgets = {}
    for number, (field, label) in enumerate(definitions):
        body.grid_rowconfigure(number, weight=1, uniform="output_format_rows")
        input_area = styled_frame(body)
        input_area.grid(row=number, column=0, sticky="nsew", pady=(0, 4))
        widgets[field] = create_managed_text_box(
            input_area, label, config[field], height=4,
            commits=commits, favorite_key=f"{page}_{field}",
            enable_favorites=True, enable_clear=True,
            enable_wrap_toggle=True, enable_import_text=True,
            preserve_trailing_newlines=True, preserve_import_newlines=True,
            wrap_config_key=f"{page}.{field}",
        )
    session = OutputFormatDraftSession(dialog, commits, config, widgets, page)
    styled_button(row, "取消", session.cancel, width=10).pack(
        side="right", padx=(8, 0),
    )
    styled_button(
        row, "保存", session.save_and_close, width=10, accent=True,
    ).pack(side="right", padx=(8, 0))
    styled_button(row, "应用", session.apply, width=10).pack(side="right")
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
