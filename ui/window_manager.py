# -*- coding: utf-8 -*-

from tkinter import TclError


_popup_stack = []


def _window_exists(window):
    try:
        return bool(window.winfo_exists())
    except TclError:
        return False


def is_descendant_or_self(widget, ancestor):
    if widget is None or ancestor is None:
        return False
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.master
    return False


def get_focus_owner(window):
    try:
        return window.focus_displayof()
    except TclError:
        return None


def cleanup_popup_stack():
    _popup_stack[:] = [
        record for record in _popup_stack if _window_exists(record["window"])
    ]


def focus_window(window):
    if _window_exists(window):
        window.deiconify()
        window.lift()
        window.focus_set()


def _install_observer(root):
    """解释器级监听只安装一次，不删除其他组件的全局监听。"""
    if getattr(root, "_zipatch_popup_observer_installed", False):
        return
    root._zipatch_popup_observer_installed = True
    root._zipatch_popup_validation_id = None

    def schedule(event=None):
        if getattr(root, "_zipatch_closing", False):
            return
        if root._zipatch_popup_validation_id is None:
            root._zipatch_popup_validation_id = root.after_idle(validate)

    def validate():
        root._zipatch_popup_validation_id = None
        if not getattr(root, "_zipatch_closing", False):
            validate_temporary_popups(root)

    def destroyed(event):
        if event.widget is root:
            if root._zipatch_popup_validation_id is not None:
                root.after_cancel(root._zipatch_popup_validation_id)
                root._zipatch_popup_validation_id = None
            _popup_stack[:] = [
                record for record in _popup_stack
                if record["window"]._root() is not root
            ]

    root.bind_all("<FocusIn>", schedule, add="+")
    root.bind_all("<FocusOut>", schedule, add="+")
    root.bind_all("<ButtonRelease>", schedule, add="+")
    root.bind("<Destroy>", destroyed, add="+")


def validate_temporary_popups(root=None):
    cleanup_popup_stack()
    records = [
        record for record in _popup_stack
        if root is None or record["window"]._root() is root
    ]
    if not records:
        return
    focused = get_focus_owner(records[-1]["window"])
    # 原生模态窗口期间焦点可能暂时不可查询；未知不等于确定离开。
    if focused is None:
        return

    for record in list(records):
        if not record["close_on_focus_out"]:
            continue
        window = record["window"]
        if not _window_exists(window):
            continue
        if is_descendant_or_self(focused, window):
            continue
        if any(
            is_descendant_or_self(focused, guard)
            for guard in record["focus_guard_widgets"]
        ):
            continue
        # 子窗口不是普通焦点离开：关闭父浮窗会同时销毁正在运行的子窗口。
        # 使用现有登记和 Tk 父子关系判断，不另建焦点状态机。
        if any(
            child["window"] is not window
            and _window_exists(child["window"])
            and is_descendant_or_self(child["window"], window)
            for child in records
        ):
            continue
        close_registered_popup(window, restore_focus=False)


def register_popup(
    window, close_callback=None, focus_on_register=True,
    close_on_focus_out=False, focus_guard_widgets=None,
    prepare_close=None, exit_blocker=False,
):
    cleanup_popup_stack()
    unregister_popup(window)
    root = window._root()
    _install_observer(root)
    _popup_stack.append({
        "window": window,
        "close_callback": close_callback,
        "prepare_close": prepare_close,
        "exit_blocker": exit_blocker,
        "close_on_focus_out": close_on_focus_out,
        "focus_guard_widgets": list(focus_guard_widgets or []),
    })

    def escape(event=None):
        close_top_popup(root)
        return "break"

    window.bind("<Escape>", escape, add="+")
    window.protocol("WM_DELETE_WINDOW", lambda: close_registered_popup(window))

    def destroyed(event):
        if event.widget is window:
            unregister_popup(window)

    window.bind("<Destroy>", destroyed, add="+")
    if focus_on_register:
        focus_window(window)
    else:
        window.lift()


def unregister_popup(window):
    _popup_stack[:] = [
        record for record in _popup_stack if record["window"] is not window
    ]


def close_registered_popup(window, restore_focus=True):
    cleanup_popup_stack()
    root = window._root()
    if getattr(root, "_zipatch_closing", False):
        return False
    record = next(
        (record for record in _popup_stack if record["window"] is window), None
    )
    if record is not None and record["close_callback"] is not None:
        if record["close_callback"]() is False:
            return False
    elif _window_exists(window):
        window.destroy()
    unregister_popup(window)
    if restore_focus:
        focus_top_popup(root)
    return True


def close_top_popup(root=None):
    cleanup_popup_stack()
    records = [
        record for record in _popup_stack
        if root is None or record["window"]._root() is root
    ]
    if records:
        return close_registered_popup(records[-1]["window"])
    return True


def focus_top_popup(root=None):
    cleanup_popup_stack()
    records = [
        record for record in _popup_stack
        if root is None or record["window"]._root() is root
    ]
    if records:
        focus_window(records[-1]["window"])


def exit_blocking_popup(root):
    cleanup_popup_stack()
    for record in reversed(_popup_stack):
        if record["window"]._root() is root and record["exit_blocker"]:
            return record["window"]
    return None


def prepare_all_for_exit(root):
    """只处理草稿决策，不关闭窗口；最终保存失败或取消时保持窗口原状。"""
    cleanup_popup_stack()
    for record in reversed(list(_popup_stack)):
        if record["window"]._root() is not root:
            continue
        callback = record["prepare_close"]
        if callback is not None and not callback():
            focus_window(record["window"])
            return False
    return True