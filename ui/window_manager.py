# -*- coding: utf-8 -*-

_popup_stack = []


def _window_exists(win):
    try:
        return bool(win.winfo_exists())
    except Exception:
        return False


def get_focus_owner(window):
    """
    返回当前显示域中的真实焦点控件。

    临时浮窗的存续判断只认这个焦点事实：
    - 焦点在浮窗内部：临时浮窗可以继续存在；
    - 焦点不在浮窗内部：临时浮窗必须关闭。
    """
    try:
        return window.focus_displayof()
    except Exception:
        return None


def is_descendant_or_self(widget, ancestor):
    """
    判断 widget 是否为 ancestor 本身或其真实子孙控件。

    控件归属关系只能来自 Tkinter 的 widget.master 父链，
    不能来自 str(widget) 的字符串前缀。

    例如：

        .!toplevel.!frame.!toplevel
        .!toplevel.!frame.!toplevel2

    二者只是兄弟 Toplevel。
    第二个字符串虽然以第一个字符串为前缀，但第二个并不是第一个的子控件。
    """
    if widget is None or ancestor is None:
        return False

    current = widget

    while current is not None:
        if current is ancestor:
            return True

        try:
            current = current.master
        except Exception:
            return False

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
    focused = get_focus_owner(window)
    return is_descendant_or_self(focused, window)


def _focus_is_inside_any(focused, widgets):
    if focused is None:
        return False

    for widget in widgets:
        if is_descendant_or_self(focused, widget):
            return True

    return False


def _unbind_item_bindings(item):
    window = item.get("window")
    bindings = item.get("temporary_focus_bindings") or []

    for sequence, funcid, bind_all in bindings:
        try:
            if bind_all:
                window.unbind_all(sequence)
            else:
                window.unbind(sequence, funcid)
        except Exception:
            pass

    item["temporary_focus_bindings"] = []


def _schedule_temporary_focus_validation(window):
    if not _window_exists(window):
        return

    try:
        window.after_idle(validate_temporary_popups)
    except Exception:
        pass


def validate_temporary_popups():
    """
    维护临时浮窗的唯一存续不变量：

    close_on_focus_out=True 的窗口必须“有焦才存在”。
    每次焦点变化或鼠标点击事件闭环结束后，统一校验所有临时浮窗；
    当前真实焦点不在临时浮窗内部，也不在显式 guard 内，则关闭该临时浮窗。

    这不是主动互斥规则，而是基于焦点事实的存续校验。
    """
    cleanup_popup_stack()

    if not _popup_stack:
        return

    focus_reference = _popup_stack[-1]["window"]
    focused = get_focus_owner(focus_reference)

    for item in list(_popup_stack):
        window = item["window"]

        if not item.get("close_on_focus_out"):
            continue

        if not _window_exists(window):
            unregister_popup(window)
            continue

        guards = list(item.get("focus_guard_widgets") or [])

        if is_descendant_or_self(focused, window):
            continue

        if _focus_is_inside_any(focused, guards):
            continue

        close_registered_popup(window, restore_focus=False)


def register_popup(
    window,
    close_callback=None,
    focus_on_register=True,
    close_on_focus_out=False,
    focus_guard_widgets=None,
):
    """
    注册弹窗/浮窗。

    弹窗栈是 ESC 逐级关闭的唯一真相源：
    - 后注册的窗口位于栈顶；
    - ESC 只关闭当前栈顶；
    - 栈顶关闭后再恢复下一层窗口焦点。

    close_on_focus_out=True 的窗口被定义为“有焦才存在”的临时浮窗：
    - 它不依赖单次 FocusOut 事件是否可靠送达；
    - 每次焦点变化或鼠标点击事件闭环结束后，统一校验所有临时浮窗；
    - 当前焦点不在临时浮窗内部时，关闭对应临时浮窗。
    """
    cleanup_popup_stack()
    unregister_popup(window)

    focus_guard_widgets = list(focus_guard_widgets or [])

    item = {
        "window": window,
        "close_callback": close_callback,
        "close_on_focus_out": close_on_focus_out,
        "focus_guard_widgets": focus_guard_widgets,
        "temporary_focus_bindings": [],
    }

    _popup_stack.append(item)

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
        def schedule_validation(event=None):
            _schedule_temporary_focus_validation(window)

        focus_out_id = window.bind("<FocusOut>", schedule_validation, add="+")
        focus_in_id = window.bind("<FocusIn>", schedule_validation, add="+")
        button_id = window.bind("<ButtonPress>", schedule_validation, add="+")
        global_focus_in_id = window.bind_all("<FocusIn>", schedule_validation, add="+")
        global_focus_out_id = window.bind_all("<FocusOut>", schedule_validation, add="+")
        global_button_id = window.bind_all("<ButtonPress>", schedule_validation, add="+")

        item["temporary_focus_bindings"] = [
            ("<FocusOut>", focus_out_id, False),
            ("<FocusIn>", focus_in_id, False),
            ("<ButtonPress>", button_id, False),
            ("<FocusIn>", global_focus_in_id, True),
            ("<FocusOut>", global_focus_out_id, True),
            ("<ButtonPress>", global_button_id, True),
        ]

    if focus_on_register:
        focus_window(window)
    else:
        try:
            window.lift()
        except Exception:
            pass

    if close_on_focus_out:
        _schedule_temporary_focus_validation(window)


def unregister_popup(window):
    global _popup_stack

    remaining = []

    for item in _popup_stack:
        if item["window"] is window:
            _unbind_item_bindings(item)
        else:
            remaining.append(item)

    _popup_stack = remaining


def close_registered_popup(window, restore_focus=True):
    """
    关闭指定注册弹窗。

    restore_focus:
    - True：关闭后把焦点交还给新的栈顶弹窗；
    - False：关闭后不自动恢复栈顶焦点，由业务动作自行决定焦点去向。
      例如“应用收藏项”后，焦点应回到目标文本框，而不是回到父弹窗壳体。
    """
    cleanup_popup_stack()

    for item in reversed(_popup_stack):
        if item["window"] is window:
            callback = item.get("close_callback")

            if callback:
                close_result = callback()

                if close_result is False:
                    return "break"
            else:
                try:
                    window.destroy()
                except Exception:
                    pass

            unregister_popup(window)

            if restore_focus:
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

    return close_registered_popup(window)


def focus_top_popup():
    cleanup_popup_stack()

    if not _popup_stack:
        return

    focus_window(_popup_stack[-1]["window"])