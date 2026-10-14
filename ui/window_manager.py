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
        permission = record["dismissal_allowed"]
        if permission is not None and not permission():
            # 业务拥有者可能正在与兄弟名称窗口或原生确认窗口交互。
            # 生命周期由拥有者判断；窗口管理器不复制其操作状态。
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


def restore_editor_interaction(widget):
    """明确编辑操作结束后的有条件恢复，不用于浮窗自动关闭抢焦点。"""
    if not _window_exists(widget):
        return False
    root = widget._root()
    if (
        getattr(root, "_zipatch_closed", False)
        or getattr(root, "_zipatch_closing", False)
        or str(widget.cget("state")) != "normal"
    ):
        return False
    owner = widget.winfo_toplevel()
    modal = root.grab_current()
    if modal is not None and modal is not owner:
        return False
    focused = get_focus_owner(root)
    if focused is not None and not is_descendant_or_self(focused, owner):
        return False
    if root.tk.call("tk", "windowingsystem") == "win32":
        if root.tk.getboolean(owner.attributes("-disabled")):
            return False
    # 用户明确返回本窗口编辑，且没有新的交互拥有者。
    # Windows 上 focus_set 可能只更新 Tk 记录，未取得实际输入归属。
    owner.lift()
    widget.focus_force()
    return True


def release_modal_control(window):
    context = getattr(window, "_zipatch_modal_context", None)
    if context is None:
        return
    del window._zipatch_modal_context
    root = window._root()
    if (
        not _window_exists(root)
        or getattr(root, "_zipatch_closed", False)
    ):
        return
    # 准备退出不是正式销毁；诊断返回后仍须归还原模态控制。
    # 被其他窗口取得或已经主动释放时，不恢复过期前置上下文。
    if root.grab_current() is not window:
        return
    previous_grab, previous_focus = context
    window.grab_release()
    if previous_grab is not None and _window_exists(previous_grab):
        previous_grab.grab_set()
    # 退出准备期间只归还模态所有权，不强制激活正文编辑。
    if getattr(root, "_zipatch_closing", False):
        return
    focused = get_focus_owner(root)
    if (
        previous_focus is not None and _window_exists(previous_focus)
        and (focused is None or is_descendant_or_self(focused, window))
        and (
            previous_grab is None
            or is_descendant_or_self(previous_focus, previous_grab)
        )
    ):
        if "state" not in previous_focus.keys():
            previous_focus.focus_set()
        elif str(previous_focus.cget("state")) == "normal":
            restore_editor_interaction(previous_focus)


def take_modal_control(window):
    """取得者拥有归还边界；显式 destroy 在真实销毁之前归还。"""
    if hasattr(window, "_zipatch_modal_context"):
        raise RuntimeError("窗口不能重复取得模态控制")
    root = window._root()
    window._zipatch_modal_context = (
        root.grab_current(), get_focus_owner(root),
    )
    window.grab_set()
    original_destroy = window.destroy

    def destroy():
        release_modal_control(window)
        original_destroy()

    window.destroy = destroy

    def destroyed(event):
        if event.widget is window:
            # 父窗口整体销毁等不经过实例 destroy 的路径：
            # 只在仍拥有控制时归还，不复活已改变的上下文。
            release_modal_control(window)

    window.bind("<Destroy>", destroyed, add="+")


def register_popup(
    window, close_callback=None, focus_on_register=True,
    close_on_focus_out=False, focus_guard_widgets=None,
    prepare_close=None, exit_blocker=False, dismissal_allowed=None,
):
    cleanup_popup_stack()
    unregister_popup(window, restore_context=False)
    root = window._root()
    _install_observer(root)
    _popup_stack.append({
        "window": window,
        "close_callback": close_callback,
        "prepare_close": prepare_close,
        "exit_blocker": exit_blocker,
        "close_on_focus_out": close_on_focus_out,
        "focus_guard_widgets": list(focus_guard_widgets or []),
        "dismissal_allowed": dismissal_allowed,
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


def unregister_popup(window, restore_context=True):
    if restore_context:
        release_modal_control(window)
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
        modal = root.grab_current()
        focused = get_focus_owner(root)
        if modal is not None:
            if focused is None or not is_descendant_or_self(focused, modal):
                focus_window(modal)
        else:
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