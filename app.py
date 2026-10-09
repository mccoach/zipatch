# -*- coding: utf-8 -*-

import sys
import tkinter as tk
from tkinter import TclError

from core.config import ConfigSaveManager, SaveStatus, load_user_config, normalize_feature_order
from core.constants import THEME
from core.message_utils import safe_ask_yes_no, safe_show_error, safe_show_info
from core.paths import center_window, get_asset_path, raise_and_focus
from core.time_utils import current_log_time
from feature_registry import get_feature_registry
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.feature_tabs import DraggableFeatureTabs
from ui.theme import apply_global_theme, styled_button, styled_frame, styled_label_frame
from ui.window_manager import (
    exit_blocking_popup, focus_window, prepare_all_for_exit,
    register_popup, unregister_popup,
)


class ZipatchApp:
    """主窗口拥有已接受配置、页面缓存和统一提交协调能力。"""

    def __init__(self, root, config_path=None):
        self.root = root
        self.root._zipatch_closed = False
        self.feature_registry = get_feature_registry()
        self.config_data, needs_write = load_user_config(config_path)
        needs_write |= normalize_feature_order(self.config_data, self.feature_registry)
        self.config_manager = ConfigSaveManager(
            self.config_data, path=config_path, needs_write=needs_write,
        )
        self.commits = ConfigCommitController(root, self.config_manager)
        self.mode = tk.StringVar(value=self.config_data["active_mode"])
        self.status_var = tk.StringVar(value="就绪")
        self.panels = {}
        self.current_panel = None
        self.log_auto_scroll = True
        self.log_max_lines = 5000
        self.log_trim_lines = 500
        self._scroll_after_id = None

        apply_global_theme(root)
        self.build_ui()
        self.show_panel(self.mode.get())
        if needs_write:
            self.commits.finish()

    def build_ui(self):
        root = self.root
        root.title("智派-文本代码修改合并助手 Zipatch v3.1.0-20261010")
        root.protocol("WM_DELETE_WINDOW", self.close_application)
        root.resizable(True, True)
        center_window(root, 1080, 800)

        icon = get_asset_path("assets/icon.ico")
        if icon.exists():
            try:
                root.iconbitmap(str(icon))
            except TclError as error:
                print(f"[图标] 设置失败：{error}", file=sys.stderr)

        if sys.platform == "win32":
            root.state("zoomed")
        raise_and_focus(root)

        top = styled_frame(root)
        top.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(
            top, text="功能选择", bg=THEME["bg"], fg=THEME["fg_dim"],
            font=THEME["font_main"],
        ).pack(side="left", padx=(0, 12))
        self.tabs = DraggableFeatureTabs(
            top, self.feature_registry, self.config_data,
            lambda: self.mode.get(), self.show_panel, self.on_feature_order_changed,
        )
        self.tabs.pack(side="left", fill="x", expand=True)
        styled_button(top, "联系作者", self.show_contact, width=8).pack(
            side="right", padx=(8, 0),
        )

        self.content_frame = tk.Frame(
            root, bg=THEME["bg_panel"], highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        self.content_frame.pack(fill="x", padx=16, pady=(0, 8))
        outer = styled_label_frame(root, "运行日志")
        outer.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        body = styled_frame(outer, bg=THEME["bg_panel"])
        body.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_text = create_managed_text_box(
            body, "日志内容", "", height=12, mono=True, readonly=True,
            enable_favorites=False, commits=self.commits,
            wrap_config_key="app.log_text",
        )
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<KeyRelease>"):
            self.log_text.bind(sequence, self.on_log_scroll, add="+")
        bottom = styled_frame(root)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        tk.Label(
            bottom, textvariable=self.status_var, bg=THEME["bg"],
            fg=THEME["fg_dim"], font=THEME["font_main"], anchor="w",
        ).pack(side="left", fill="x", expand=True)

    def commit_active_editor(self):
        return self.commits.commit_active_editor()

    def show_panel(self, mode):
        if mode not in self.feature_registry:
            mode = self.config_data["ui"]["feature_order"][0]
        previous = self.current_panel
        if previous is not None and previous.config_key == mode:
            self.tabs.refresh_button_styles()
            return

        changed = False
        if previous is not None:
            changed = self.commits.accept_editors(
                self.commits.select(page=previous.config_key)
            )

        self.mode.set(mode)
        self.config_manager.accept(self.config_data, {"active_mode": mode}, persist=False)
        if changed:
            self.commits.finish()

        if previous is not None:
            previous.pack_forget()
        if mode not in self.panels:
            self.panels[mode] = self.feature_registry[mode]["panel_class"](
                self.content_frame, self,
            )
        self.current_panel = self.panels[mode]
        self.current_panel.pack(fill="x", padx=14, pady=12)
        self.status_var.set("就绪")
        self.tabs.refresh_button_styles()

    def on_feature_order_changed(self, order):
        changed = self.config_manager.accept(
            self.config_data["ui"], {"feature_order": list(order)}
        )
        if changed:
            self.commits.finish()
            self.tabs.refresh()
            self.log(f"功能标签顺序已更新：{' / '.join(order)}")

    def close_application(self):
        if getattr(self.root, "_zipatch_closing", False):
            return
        blocker = exit_blocking_popup(self.root)
        if self.commits.operation_depth or blocker is not None:
            safe_show_info(
                "当前操作尚未结束",
                "请先完成或取消当前业务确认、文件选择或收藏名称输入，再退出程序。",
                parent=blocker or self.root,
            )
            if blocker is not None:
                focus_window(blocker)
            return

        self.root._zipatch_closing = True
        try:
            if not prepare_all_for_exit(self.root):
                return
            self.commits.accept_editors(self.commits.select())
            result = self.config_manager.save(include_session=True)
            if result.status is SaveStatus.FAILED:
                if not safe_ask_yes_no(
                    "配置保存失败",
                    "最新设置或结果未能写入磁盘。\n\n"
                    f"{result.error}\n\n是否放弃未保存内容，仍然退出？",
                    parent=self.root, icon="warning",
                ):
                    return
            if self._scroll_after_id is not None:
                self.root.after_cancel(self._scroll_after_id)
                self._scroll_after_id = None
            # 等待设置窗口的回调可能在销毁后恢复；
            # 先记录正式退出事实，使恢复的回调不再访问已销毁的界面。
            self.root._zipatch_closed = True
            self.root.destroy()
        finally:
            self.root._zipatch_closing = False

    def show_contact(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("联系作者")
        dialog.configure(bg=THEME["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        center_window(dialog, 340, 440)

        def close():
            unregister_popup(dialog)
            dialog.destroy()
            return True

        qr = get_asset_path("assets/wechat_qr.png")
        if qr.exists():
            try:
                from PIL import Image, ImageTk
                with Image.open(qr) as image:
                    photo = ImageTk.PhotoImage(image.resize((260, 260), Image.Resampling.LANCZOS))
                label = tk.Label(dialog, image=photo, bg=THEME["bg"])
                label.image = photo
                label.pack(pady=(28, 0))
            except (ImportError, OSError) as error:
                tk.Label(dialog, text=f"二维码无法加载：\n{error}", bg=THEME["bg"]).pack(pady=40)
        else:
            tk.Label(dialog, text="二维码图片未找到：assets/wechat_qr.png", bg=THEME["bg"]).pack(pady=40)

        tk.Label(
            dialog, text="扫码添加作者微信，欢迎反馈交流", bg=THEME["bg"],
            fg=THEME["fg_label"], font=THEME["font_main"],
        ).pack(pady=(16, 0))
        styled_button(dialog, "关闭", close, width=10).pack(pady=(16, 24))
        register_popup(dialog, close)

    def log(self, message):
        text = self.log_text
        text.configure(state="normal")
        text.insert("end", f"[{current_log_time()}]  {message}\n")
        lines = int(text.index("end-1c").split(".")[0]) - 1
        if lines > self.log_max_lines:
            remove = max(self.log_trim_lines, lines - self.log_max_lines)
            text.delete("1.0", f"{remove + 1}.0")
        text.configure(state="disabled")
        if self.log_auto_scroll:
            text.see("end")

    def on_log_scroll(self, event=None):
        if self._scroll_after_id is not None:
            self.root.after_cancel(self._scroll_after_id)
        self._scroll_after_id = self.root.after(80, self.refresh_log_auto_scroll_state)

    def refresh_log_auto_scroll_state(self):
        self._scroll_after_id = None
        self.log_auto_scroll = self.log_text.yview()[1] >= 0.999

    def handle_panel_error(self, title, error, parent=None):
        self.status_var.set("执行失败")
        self.log(f"{title}失败：{error}")
        safe_show_error(title, str(error), parent=parent or self.root)
