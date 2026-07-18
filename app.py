# -*- coding: utf-8 -*-

import sys
import tkinter as tk

from core.config import load_user_config, save_user_config, normalize_feature_order
from core.constants import THEME
from core.message_utils import safe_show_error
from core.paths import center_window, get_asset_path, raise_and_focus
from core.time_utils import current_log_time
from feature_registry import get_feature_registry
from ui.dialogs import create_managed_text_box
from ui.theme import (
    apply_global_theme,
    styled_frame,
    styled_label_frame,
    styled_button,
)
from ui.feature_tabs import DraggableFeatureTabs
from ui.window_manager import register_popup, unregister_popup


class ZipatchApp:
    """
    主窗口只负责：
    1. 加载/保存配置；
    2. 创建顶部功能标签；
    3. 切换功能面板；
    4. 提供全局日志与状态能力。

    各功能自己的按钮、输入框、文本框、浏览器、执行逻辑入口，
    全部放在各自 panel 内，不再耦联到主窗口。
    """

    def __init__(self, root):
        self.root = root
        self.config_data = load_user_config()
        self.feature_registry = get_feature_registry()

        normalize_feature_order(self.config_data, self.feature_registry)
        save_user_config(self.config_data)

        initial_mode = self.config_data.get("active_mode", "merge")
        if initial_mode not in self.feature_registry:
            initial_mode = self.config_data["ui"]["feature_order"][0]

        self.mode = tk.StringVar(value=initial_mode)
        self.status_var = tk.StringVar(value="就绪")
        self.log_auto_scroll = True

        self.tabs = None
        self.content_frame = None
        self.current_panel = None
        self.log_text = None

        apply_global_theme(root)
        self.build_ui()
        self.show_panel(initial_mode, persist=False)

    def apply_window_icon(self):
        icon_path = get_asset_path("assets/icon.ico")

        if not icon_path.exists():
            print(f"[图标] 未找到窗口图标：{icon_path}", file=sys.stderr)
            return

        try:
            self.root.iconbitmap(str(icon_path))
        except Exception as e:
            print(f"[图标] 设置窗口图标失败：{e}", file=sys.stderr)

    def maximize_main_window(self):
        """
        启动时默认最大化主窗口。

        Windows/Tk 常用 state("zoomed")；
        少数平台不支持时，退回到 attributes("-zoomed", True)。
        如果都不可用，则保留 center_window 设置的初始尺寸。
        """
        try:
            self.root.state("zoomed")
            return
        except Exception:
            pass

        try:
            self.root.attributes("-zoomed", True)
        except Exception:
            pass

    def build_ui(self):
        self.root.title("智派-文本代码修改合并助手 Zipatch v1.2.2-20260718")
        self.apply_window_icon()
        self.root.resizable(True, True)
        center_window(self.root, 1080, 800)
        self.maximize_main_window()
        raise_and_focus(self.root)

        top = styled_frame(self.root)
        top.pack(fill="x", padx=16, pady=(14, 8))

        tk.Label(
            top,
            text="功能选择",
            bg=THEME["bg"],
            fg=THEME["fg_dim"],
            font=THEME["font_main"],
        ).pack(side="left", padx=(0, 12))

        self.tabs = DraggableFeatureTabs(
            parent=top,
            feature_registry=self.feature_registry,
            config_data=self.config_data,
            get_current_mode=lambda: self.mode.get(),
            on_select=self.show_panel,
            on_order_changed=self.on_feature_order_changed,
        )
        self.tabs.pack(side="left", fill="x", expand=True)

        # 联系作者按钮，放在标签行最右侧
        styled_button(
            top,
            "联系作者",
            self.show_contact,
            width=8,
        ).pack(side="right", padx=(8, 0))

        self.content_frame = tk.Frame(
            self.root,
            bg=THEME["bg_panel"],
            highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        self.content_frame.pack(fill="x", padx=16, pady=(0, 8))

        log_outer = styled_label_frame(self.root, "运行日志")
        log_outer.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        log_body = styled_frame(log_outer, bg=THEME["bg_panel"])
        log_body.pack(fill="both", expand=True, padx=8, pady=8)

        self.log_text = create_managed_text_box(
            parent=log_body,
            label_text="日志内容",
            initial_value="",
            height=12,
            mono=True,
            readonly=True,
            enable_favorites=False,
            enable_clear=True,
            enable_wrap_toggle=True,
            config_data=self.config_data,
            save_config_func=lambda: save_user_config(self.config_data),
            wrap_config_key="app.log_text",
        )

        self.log_text.bind("<MouseWheel>", self.on_log_scroll)
        self.log_text.bind("<Button-4>", self.on_log_scroll)
        self.log_text.bind("<Button-5>", self.on_log_scroll)
        self.log_text.bind("<KeyRelease>", self.on_log_scroll)

        bottom = styled_frame(self.root)
        bottom.pack(fill="x", padx=16, pady=(0, 14))

        tk.Label(
            bottom,
            textvariable=self.status_var,
            bg=THEME["bg"],
            fg=THEME["fg_dim"],
            font=THEME["font_main"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

    def show_contact(self):
        """
        弹出联系作者弹窗：
        - 屏幕居中；
        - 显示微信二维码；
        - 底部引导文字；
        - 关闭按钮 + Esc 关闭。
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("联系作者")
        dialog.configure(bg=THEME["bg"])
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        center_window(dialog, 340, 440)

        def close(event=None):
            unregister_popup(dialog)
            dialog.destroy()
            return "break"

        dialog.protocol("WM_DELETE_WINDOW", close)

        qr_path = get_asset_path("assets/wechat_qr.png")

        if qr_path.exists():
            try:
                from PIL import Image, ImageTk

                img = Image.open(str(qr_path)).resize((260, 260),
                                                      Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)

                qr_label = tk.Label(
                    dialog,
                    image=photo,
                    bg=THEME["bg"],
                    pady=0,
                )
                qr_label.image = photo  # 防止被 GC
                qr_label.pack(pady=(28, 0))

            except ImportError:
                tk.Label(
                    dialog,
                    text="请安装 Pillow 以显示二维码：\npip install Pillow",
                    bg=THEME["bg"],
                    fg=THEME["fg_dim"],
                    font=THEME["font_main"],
                    justify="center",
                ).pack(pady=(40, 0))

            except Exception as e:
                tk.Label(
                    dialog,
                    text=f"二维码加载失败：\n{e}",
                    bg=THEME["bg"],
                    fg=THEME["fg_dim"],
                    font=THEME["font_main"],
                    justify="center",
                    wraplength=300,
                ).pack(pady=(40, 0))

        else:
            tk.Label(
                dialog,
                text=f"二维码图片未找到，\n请将微信二维码图片放置于：\nassets/wechat_qr.png",
                bg=THEME["bg"],
                fg=THEME["fg_dim"],
                font=THEME["font_main"],
                justify="center",
                wraplength=300,
            ).pack(pady=(40, 0))

        tk.Label(
            dialog,
            text="扫码添加作者微信，欢迎反馈交流",
            bg=THEME["bg"],
            fg=THEME["fg_label"],
            font=THEME["font_main"],
            justify="center",
        ).pack(pady=(16, 0))

        styled_button(
            dialog,
            "关闭",
            close,
            width=10,
        ).pack(pady=(16, 24))

        register_popup(dialog, close)

    def on_feature_order_changed(self, new_order):
        """
        功能标签顺序变化后的唯一落点。
        """
        self.config_data.setdefault("ui", {})
        self.config_data["ui"]["feature_order"] = list(new_order)
        save_user_config(self.config_data)

        self.log(f"功能标签顺序已更新：{' / '.join(new_order)}")

        if self.tabs:
            self.tabs.refresh()

    def clear_content(self):
        if self.current_panel is not None:
            try:
                self.current_panel.destroy()
            except Exception:
                pass
            self.current_panel = None

        for child in self.content_frame.winfo_children():
            child.destroy()

    def show_panel(self, mode, persist=True):
        if mode not in self.feature_registry:
            order = self.config_data["ui"]["feature_order"]
            mode = order[0] if order else "merge"

        self.mode.set(mode)

        if persist:
            self.config_data["active_mode"] = mode
            save_user_config(self.config_data)

        self.clear_content()

        panel_class = self.feature_registry[mode]["panel_class"]

        self.current_panel = panel_class(
            parent=self.content_frame,
            app=self,
            config_data=self.config_data,
            log_func=self.log,
            set_status=self.status_var.set,
            save_config=lambda: save_user_config(self.config_data),
        )
        self.current_panel.pack(fill="x", padx=14, pady=12)

        self.status_var.set("就绪")

        if self.tabs:
            self.tabs.refresh()

    def log(self, message):
        line = f"[{current_log_time()}]  {message}\n"

        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            self.log_text.configure(state="disabled")

            if self.log_auto_scroll:
                self.log_text.see("end")

            self.root.update_idletasks()
        except Exception:
            print(line, end="")

    def on_log_scroll(self, event=None):
        self.root.after(80, self.refresh_log_auto_scroll_state)

    def refresh_log_auto_scroll_state(self):
        try:
            _, last = self.log_text.yview()
            self.log_auto_scroll = last >= 0.999
        except Exception:
            self.log_auto_scroll = True

    def handle_panel_error(self, title, error, parent=None):
        self.status_var.set("执行失败")
        self.log(f"{title}失败：{error}")
        safe_show_error(title, str(error), parent=parent or self.root)
