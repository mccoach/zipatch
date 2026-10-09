# -*- coding: utf-8 -*-

# --- 默认附加文本 ---

PREAMBLE_TEXT = ("我正在开发缠论分析系统，以下是现有的全量前端代码和开发规范、需求规则等，"
                 "你要全面阅读并充分理解开发与输出规范、功能需求和用户意图，并在后续任务中严格遵守，"
                 "接下来我们需要在这个代码基础上继续修改开发\n")

ENDING_TEXT = (
    "以上现有前端代码全部发送完毕，请全面阅读并充分理解，完成后告诉我，我会继续提出开发需求。\n"
    "我要再次强调，1. 严控范围：只对有明确具体要求的部分做改动，对未明确要求修改的内容要确保全部完美回归；"
    "2. 极简重构（严格遵循奥卡姆剃刀原则，从业务需求反推最本质的解决方案，以最稳妥简洁高效直接的逻辑做根因拉直重构式修改，不做补丁摞补丁的堆砌和头疼医头脚疼医脚的拼凑；"
    "3. 尊重原文（修改代码时，如无必要则不改变原代码中各组块的顺序，便于我对比前后差异，如确有需求必须改变顺序，则应该在注释中说明理由并说清楚是如何调序的）；"
    "4. 查缺补漏（如果明确要求是全量输出，就必须严格全量输出，输出前仔细检查决不允许任何遗漏和省略，因为我要整体替换，绝不允许因任何遗漏省略而导致功能缺损崩溃）；"
    "5. 合规变更（一切修改行为都强制要求严格遵循通用开发规范中“变更控制与交付保障协议（SOP）”规定）；"
    "6. 有序输出（一切文件内容的输出都要放在代码块里，避免格式错乱）；"
    "7. 有效交流（我是个编程小白，要用浅显易懂小白友好的自然语言跟我沟通，不要大量使用代码，我读不懂的，措辞要简洁，挑关键的讲，不要翻来覆去啰里啰嗦的信息轰炸）。"
)

SCAN_PREAMBLE_TEXT = ("全景扫描模式：下列为指定根目录下所有层级的“文件清单”（统一为绝对路径），仅列路径，不包含内容。\n")

SCAN_ENDING_TEXT = ("全景扫描已完成，请针对该目录文件结构帮我编写一个全面而稳妥的 .gitignore文件。")

# --- 默认排除项 ---

DEFAULT_EXCLUDE_FOLDERS = [
    ".git",
    ".github",
    ".vscode",
    "__pycache__",
    "node_modules",
    ".venv",
    "dist",
    "build",
    "public",
    "scripts",
    "tests",
    "dev_tests",
    "var",
    "99_归档",
]

DEFAULT_EXCLUDE_FILES = [
    ".DS_Store",
    "Thumbs.db",
    "README.md",
    "*.md",
    "*.pyc",
    "*.pyo",
    "*.o",
    "*.so",
    "*.dll",
    "*.exe",
    "*.log",
    "*.tmp",
    "*.bak",
    "*.bak1",
    "*.bak2",
    ":",
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.bmp",
    "*.svg",
    "*.ico",
    "*.mp3",
    "*.wav",
    "*.mp4",
    "*.mov",
    "*.avi",
    "*.zip",
    "*.rar",
    "*.tar",
    "*.gz",
    "*.pdf",
    "*.doc",
    "*.docx",
    "*.xls",
    "*.xlsx",
    "*.ppt",
    "*.pptx",
    ".gitignore",
    "*.sqlite",
    "*.sqlite-wal",
    "*.sqlite-shm",
]

# --- 合并/还原协议标记 ---

CODE_HEADER_LINE = "---以下是源代码---"
CODE_FOOTER_LINE = "---源代码结束---"
FILE_SECTION_START_TEMPLATE = "---第{index}个文件---"

MESSAGE_FILE_EXCLUDED = "文件命中排除名单，内容略"
MESSAGE_CANNOT_READ = "无法读取或为非文本文件"
MESSAGE_READ_ERROR = "*** 读取文件时发生错误"

SKIPPED_LOG_FILENAME = "_RESTORE_SKIPPED_FILES_.log"

# --- 修改包执行器协议常量 ---

PATCH_PROTOCOL_NAME = "ZIPATCH_V3"
PATCH_BOUNDARY_PREFIX = "ZIPATCH_BOUNDARY_"
PATCH_START_PREFIX = f"<<{PATCH_PROTOCOL_NAME}"
PATCH_END = f"<</{PATCH_PROTOCOL_NAME}>>"
PATCH_PROTOCOL_DESCRIPTION = f"{PATCH_PROTOCOL_NAME} 动态 boundary 原文块协议"

SUPPORTED_PATCH_OPS = {
    "write_file",
    "append_text",
    "replace_between",
    "replace_exact",
    "delete_file",
    "delete_dir",
    "rename_file",
    "move_file",
    "copy_file",
    "rename_dir",
    "move_dir",
    "copy_dir",
    "create_dir",
}

PATCH_TEXT_BLOCK_STARTERS = {
    "---PATH": "path",
    "---NEW_PATH": "new_path",
    "---START_MARKER": "start_marker",
    "---END_MARKER": "end_marker",
    "---CONTENT": "content",
    "---OLD": "old",
    "---NEW": "new",
}

# --- 编码 ---

ENCODINGS_TO_TRY = [
    "utf-8-sig",
    "utf-8",
    "gb18030",
    "gbk",
    "latin-1",
]

CONFIG_FILENAME = "zipatch_config.json"

# --- 文本框自动换行默认状态 ---
#
# 说明：
# - 这里保存的是“每个文本框”的 UI 偏好，不是业务数据；
# - key 使用“功能.语义”的稳定命名；
# - create_managed_text_box 只负责按 key 读取和保存；
# - 各业务面板只负责传入自己文本框的唯一 key。
TEXT_WRAP_DEFAULTS = {
    "scan.exclude_folders": True,
    "scan.exclude_files": True,

    "scan.preamble": True,
    "scan.ending": True,
    "merge.exclude_folders": True,
    "merge.exclude_files": True,

    "merge.regular_preamble": True,
    "merge.regular_ending": True,
    "merge.demand_file_list": True,
    "merge.demand_preamble": True,
    "merge.demand_ending": True,
    "patch.patch_text": False,
    "patch.result_text": False,
    "patch.protocol_doc_text": True,
    "app.log_text": True,
}

# --- 主题配色 ---

THEME = {
    "bg": "#d8d8d8",
    "bg_panel": "#cecece",
    "bg_input": "#e0e0e0",
    "bg_btn": "#b0b0b0",
    "bg_btn_accent": "#888888",
    "bg_btn_hover": "#707070",
    "bg_btn_selected": "#888888",
    "bg_log": "#e4e4e4",
    "fg": "#1a1a1a",
    "fg_dim": "#707070",
    "fg_label": "#333333",
    "fg_on_dark": "#ffffff",
    "accent": "#555555",
    "accent_dim": "#707070",
    "border": "#b0b0b0",
    "border_light": "#a0a0a0",
    "select_bg": "#555555",
    "select_fg": "#ffffff",
    "warning": "#d97706",
    "danger": "#cc0000",
    "danger_bg": "#ffd6d6",
    "font_main": ("Microsoft YaHei UI", 10),
    "font_title": ("Microsoft YaHei UI", 11, "bold"),
    "font_mono": ("Consolas", 9),
}

# --- 默认配置 ---

DEFAULT_CONFIG = {
    "active_mode": "merge",
    "settings": {
        "entry_history_max_items": 10,
    },
    "entry_history": {},
    "ui": {
        "feature_order": ["scan", "merge", "patch", "restore"],
    },
    "text_wrap": TEXT_WRAP_DEFAULTS,
    "scan": {
        "source_folder": "",
        "output_folder": "",
        "output_filename": "panorama_scan.txt",
        "exclude_folders": "",
        "exclude_files": "",
        "preamble_text": SCAN_PREAMBLE_TEXT,
        "ending_text": SCAN_ENDING_TEXT,
        "include_size": False,
        "include_date": False,
        "open_after_done": True,
        "force_overwrite": True,
    },
    "merge": {
        "source_folder": "",
        "output_folder": "",
        "output_filename": "merged_code.txt",
        "exclude_folders": "\n".join(DEFAULT_EXCLUDE_FOLDERS),
        "exclude_files": "\n".join(DEFAULT_EXCLUDE_FILES),
        "preamble_text": PREAMBLE_TEXT,
        "ending_text": ENDING_TEXT,
        "code_header_line": CODE_HEADER_LINE,
        "code_footer_line": CODE_FOOTER_LINE,
        "open_after_done": True,
        "force_overwrite": True,
        "demand_file_list_text": "",
        "demand_preamble_text": "",
        "demand_ending_text": "",
    },
    "restore": {
        "source_txt_file": "",
        "target_folder": "",
        "code_header_line": CODE_HEADER_LINE,
        "code_footer_line": CODE_FOOTER_LINE,
        "existing_file_policy": "overwrite",
        "open_after_done": True,
    },
    "patch": {
        "project_root": "",
        "patch_mode": "apply",
        "allow_delete": False,
        "allow_multi_replace_exact": False,
        "backup_enabled": True,
        "backup_dir": "99_归档/AI文件修改备份",
        "restore_source_dir": "",
        "keep_restore_source_path": False,
        "patch_text": "",
        "last_result_text": "",
        "open_backup_after_done": False,
        "protocol_doc_source_path": "",
    },
    "favorites": {
        "scan_preamble": [],
        "scan_ending": [],
        "scan_exclude_folders": [],
        "scan_exclude_files": [],

        "merge_regular_preamble": [],
        "merge_regular_ending": [],
        "merge_exclude_folders": [],
        "merge_exclude_files": [],

        "demand_file_list": [],
        "demand_preamble": [],
        "demand_ending": [],
        "patch_text": [],
    },
}
