# -*- coding: utf-8 -*-

import sys
import tkinter as tk
from tkinter import TclError

from core.config import ConfigSaveManager, SaveStatus, load_user_config, normalize_feature_order
from core.constants import THEME
from ui.message_dialogs import safe_ask_yes_no, safe_show_error, safe_show_info
from core.merged_format import OutputSettingsError
from core.paths import get_asset_path
from ui.window_manager import center_window, raise_and_focus
from core.time_utils import current_log_time
from feature_registry import get_feature_registry
from ui.config_editing import ConfigCommitController
from ui.dialogs import create_managed_text_box
from ui.feature_tabs import DraggableFeatureTabs
from ui.theme import apply_global_theme, styled_button, styled_frame
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
        configuration_notices = []
        self.config_data, needs_write = load_user_config(
            config_path, notices=configuration_notices,
        )
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
        self._scroll_after_id = None

        apply_global_theme(root)
        self.build_ui()
        self.show_panel(self.mode.get())
        saved = self.commits.finish() if needs_write else None
        for notice in configuration_notices:
            message = notice + (
                "\n配置已确认保存，重启后仍会保留。"
                if saved is not None and saved.persisted else
                "\n配置保存尚未确认；请先解决保存问题，"
                "否则重启后仍可能需要重新转换。"
            )
            self.log(message)
            safe_show_info("拆分设置已调整，请核对", message, self.root)

    def build_ui(self):
        root = self.root
        root.title("智派-文本代码修改合并助手 Zipatch v3.7.0-20261111")
        root.protocol("WM_DELETE_WINDOW", self.close_application)
        root.resizable(True, True)
        center_window(root, 1080, 800)

        try:
            icon = get_asset_path("assets/icon.ico")
            if icon.exists():
                root.iconbitmap(str(icon))
        except (OSError, ValueError, TclError) as error:
            # 图标是附属资源；拒绝不安全资源，不阻止主业务窗口使用。
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
        outer = tk.Frame(
            root, bg=THEME["bg_panel"], highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        outer.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        body = styled_frame(outer, bg=THEME["bg_panel"])
        body.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_text = create_managed_text_box(
            body, "运行日志", "", height=12, mono=True, readonly=True,
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


    def show_panel(self, mode):
        if self.commits.operation_depth:
            return
        if mode not in self.feature_registry:
            mode = self.config_data["ui"]["feature_order"][0]
        previous = self.current_panel
        if previous is not None and previous.config_key == mode:
            self.tabs.refresh_button_styles()
            return

        changed = False
        if previous is not None:
            editors = self.commits.select(page=previous.config_key)
            changed = self.commits.accept_editors(editors)
            if any(editor.dirty for editor in editors):
                return

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
            editors = self.commits.select()
            self.commits.accept_editors(editors)
            if any(editor.dirty for editor in editors):
                return
            try:
                result = self.config_manager.save(include_session=True)
            except OutputSettingsError as error:
                safe_show_error("输出配置无效，未退出", str(error), self.root)
                return
            if result.status is SaveStatus.FAILED:
                if not safe_ask_yes_no(
                    "配置保存失败",
                    result.failure_description
                    + "\n\n是否接受上述磁盘状态并放弃继续保存，仍然退出？"
                    "\n强制退出不会自动恢复旧配置文件。",
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

        try:
            qr = get_asset_path("assets/wechat_qr.png")
            if qr.exists():
                from PIL import Image, ImageTk
                with Image.open(qr) as image:
                    photo = ImageTk.PhotoImage(
                        image.resize((260, 260), Image.Resampling.LANCZOS),
                    )
                label = tk.Label(dialog, image=photo, bg=THEME["bg"])
                label.image = photo
                label.pack(pady=(28, 0))
            else:
                tk.Label(
                    dialog, text="二维码图片未找到：assets/wechat_qr.png",
                    bg=THEME["bg"],
                ).pack(pady=40)
        except (ImportError, OSError, ValueError, TclError) as error:
            tk.Label(
                dialog, text=f"二维码无法加载：\n{error}", bg=THEME["bg"],
            ).pack(pady=40)

        tk.Label(
            dialog, text="扫码添加作者微信，欢迎反馈交流", bg=THEME["bg"],
            fg=THEME["fg_label"], font=THEME["font_main"],
        ).pack(pady=(16, 0))
        styled_button(dialog, "关闭", close, width=10).pack(pady=(16, 24))
        register_popup(dialog, close)

    def log(self, message):
        text = self.log_text
        text.configure(state="normal")
        # 会话日志只追加，不自动截断；主动清空仍由现有按钮负责。
        text.insert("end", f"[{current_log_time()}]  {message}\n")
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
