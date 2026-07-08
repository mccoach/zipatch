# -*- coding: utf-8 -*-

_popup_stack = []


def _window_exists(win):
    try:
        return bool(win.winfo_exists())
    except Exception:
        return False


def cleanup_popup_stack():
    global _popup_stack

    _popup_stack = [
        item
        for item in _popup_stack
        if _window_exists(item["window"])
    ]


def focus_window(win):
    try:
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.focus_force)
    except Exception:
        pass


def is_focus_inside(window):
    try:
        focused = window.focus_get()
    except Exception:
        focused = None

    if focused is None:
        return False

    try:
        return str(focused).startswith(str(window))
    except Exception:
        return False


def register_popup(
    window,
    close_callback=None,
    focus_on_register=True,
    close_on_focus_out=False,
    focus_guard_widgets=None,
):
    """
    注册弹窗/浮窗。

    参数：
    - focus_on_register:
        True：注册后自动聚焦到弹窗。适合普通弹窗、收藏浮窗。
        False：只显示，不抢焦点。适合输入框历史浮窗。
    - close_on_focus_out:
        True：弹窗失焦后自动关闭。
    - focus_guard_widgets:
        失焦判断时允许保留的关联控件。
        例如历史浮窗关联的 Entry，即使焦点还在 Entry，也不关闭。
    """
    cleanup_popup_stack()
    unregister_popup(window)

    focus_guard_widgets = list(focus_guard_widgets or [])

    _popup_stack.append({
        "window": window,
        "close_callback": close_callback,
    })

    def on_escape(event=None):
        close_registered_popup(window)
        return "break"

    window.bind("<Escape>", on_escape, add="+")

    try:
        window.protocol(
            "WM_DELETE_WINDOW",
            lambda: close_registered_popup(window),
        )
    except Exception:
        pass

    if close_on_focus_out:
        def schedule_close(event=None):
            try:
                window.after(
                    120,
                    lambda: close_if_focus_outside(
                        window,
                        close_callback,
                        focus_guard_widgets,
                    ),
                )
            except Exception:
                pass

        window.bind("<FocusOut>", schedule_close, add="+")

    if focus_on_register:
        focus_window(window)
    else:
        try:
            window.lift()
        except Exception:
            pass


def close_if_focus_outside(window, close_callback=None, focus_guard_widgets=None):
    if not _window_exists(window):
        return

    focus_guard_widgets = list(focus_guard_widgets or [])

    try:
        focused = window.focus_get()
    except Exception:
        focused = None

    if focused is not None:
        try:
            if str(focused).startswith(str(window)):
                return
        except Exception:
            pass

        for widget in focus_guard_widgets:
            try:
                if focused is widget or str(focused).startswith(str(widget)):
                    return
            except Exception:
                pass

    if close_callback:
        close_callback()
    else:
        try:
            window.destroy()
        except Exception:
            pass

    unregister_popup(window)
    focus_top_popup()


def unregister_popup(window):
    global _popup_stack

    _popup_stack = [
        item
        for item in _popup_stack
        if item["window"] is not window
    ]


def close_registered_popup(window):
    cleanup_popup_stack()

    for item in reversed(_popup_stack):
        if item["window"] is window:
            callback = item.get("close_callback")
            if callback:
                callback()
            else:
                try:
                    window.destroy()
                except Exception:
                    pass

            unregister_popup(window)
            focus_top_popup()
            return "break"

    try:
        window.destroy()
    except Exception:
        pass

    return "break"


def close_top_popup():
    cleanup_popup_stack()

    if not _popup_stack:
        return "break"

    item = _popup_stack[-1]
    window = item["window"]
    callback = item.get("close_callback")

    if callback:
        callback()
    else:
        try:
            window.destroy()
        except Exception:
            pass

    unregister_popup(window)
    focus_top_popup()

    return "break"


def focus_top_popup():
    cleanup_popup_stack()

    if not _popup_stack:
        return

    focus_window(_popup_stack[-1]["window"])
