# -*- coding: utf-8 -*-

from contextlib import contextmanager
from copy import deepcopy
from time import perf_counter

from core.config import SaveStatus
from core.message_utils import safe_show_error
from core.text_io import get_text_value


class EditorBinding:
    """草稿归控件所有；普通编辑与已准备文本共用一个接受协议。"""

    def __init__(
        self, controller, widget, config, field, page,
        variable=None, normalizer=None, history_key=None,
    ):
        self.controller = controller
        self.widget = widget
        self.config = config
        self.field = field
        self.page = page
        self.variable = variable
        self.normalizer = normalizer or (lambda value: value)
        self.history_key = history_key
        self.dirty = False
        self.destroyed = False
        self._suspend_depth = 0
        self._trace_id = None
        widget._config_binding = self
        widget._commit_current_value = self.commit

        if variable is not None:
            self._trace_id = variable.trace_add("write", self.mark_dirty)
        else:
            widget.edit_modified(False)
            widget.bind("<<Modified>>", self.on_modified, add="+")
        widget.bind("<FocusOut>", self.on_focus_out, add="+")
        if variable is not None:
            widget.bind("<Return>", self.on_return, add="+")
        widget.bind("<Destroy>", self.on_destroy, add="+")
        controller.editors.add(self)

    def mark_dirty(self, *_):
        self.dirty = True

    def on_modified(self, event=None):
        if not self.destroyed and self.widget.edit_modified():
            self.dirty = True
            self.widget.edit_modified(False)

    def read_current_value(self):
        if self.variable is not None:
            return self.variable.get()
        return self.controller.read_text(self.widget)

    def commit(self, prepared_text=None):
        self.controller.metrics["editor_accept_calls"] += 1
        if self.destroyed or self._suspend_depth:
            return False
        if prepared_text is not None:
            if self.variable is not None:
                raise ValueError("已准备文本仅用于 Text 编辑器。")
            self.dirty = True
        elif self.variable is None and self.widget.edit_modified():
            self.dirty = True
        if not self.dirty:
            return False

        value = self.read_current_value() if prepared_text is None else prepared_text.rstrip("\n")
        value = self.normalizer(value)

        history = None
        if self.history_key and value:
            histories = self.controller.manager.config_data["entry_history"]
            maximum = self.controller.manager.config_data["settings"]["entry_history_max_items"]
            history = [value] + [
                previous for previous in histories.get(self.history_key, [])
                if previous != value
            ]
            history = history[:maximum]

        # 显示规范化必须在共享字段接受前完成，控件错误不会留下半次接受。
        if self.variable is not None:
            if self.variable.get() != value:
                self.variable.set(value)
        changed = self.controller.manager.accept(self.config, {self.field: value})
        if history is not None:
            changed |= self.controller.manager.accept(
                self.controller.manager.config_data["entry_history"],
                {self.history_key: history},
            )
        if self.variable is None:
            self.widget.edit_modified(False)
        self.dirty = False
        return changed

    def on_focus_out(self, event=None):
        if not self.destroyed and not self._suspend_depth:
            self.controller.submit_editors([self], self.widget.winfo_toplevel())

    def on_return(self, event=None):
        self.controller.submit_editors([self], self.widget.winfo_toplevel())

    @contextmanager
    def suspend_focus_commit(self):
        self._suspend_depth += 1
        try:
            yield
        finally:
            self._suspend_depth -= 1

    def on_destroy(self, event=None):
        if event.widget is not self.widget:
            return
        self.destroyed = True
        self.controller.editors.discard(self)
        if self._trace_id is not None:
            self.variable.trace_remove("write", self._trace_id)
            self._trace_id = None


class ConfigCommitController:
    """接受范围、保存结果和同步操作生命周期的协调入口。"""

    def __init__(self, root, manager):
        self.root = root
        self.manager = manager
        self.editors = set()
        self.operation_depth = 0
        self.metrics = {
            "editor_accept_calls": 0,
            "text_reads": 0,
            "text_read_seconds": 0.0,
            "business_starts": 0,
        }

    def register(self, *args, **kwargs):
        return EditorBinding(self, *args, **kwargs)

    def select(self, page=None, fields=None):
        return [
            editor for editor in self.editors
            if (page is None or editor.page == page)
            and (fields is None or editor.field in fields)
        ]

    def accept_editors(self, editors):
        changed = False
        for editor in tuple(editors):
            changed |= editor.commit()
        return changed

    def commit_active_editor(self):
        focused = self.root.focus_get()
        commit = getattr(focused, "_commit_current_value", None)
        return bool(commit()) if callable(commit) else False

    @contextmanager
    def operation(self):
        """只保护同步流程生命周期，不合并保存，不跨阶段保持 batch。"""
        self.operation_depth += 1
        try:
            yield
        finally:
            self.operation_depth -= 1

    def finish(self, parent=None, include_session=False):
        result = self.manager.save(include_session=include_session)
        if result.status is SaveStatus.FAILED:
            # 错误提示运行嵌套事件循环；提示返回前不能销毁调用者所属界面。
            # 这里只保护提示生命周期，不合并保存，也不接受其他草稿。
            with self.operation():
                safe_show_error(
                    "配置保存失败",
                    "最新配置尚未写入磁盘，可再次提交重试。\n\n"
                    f"{result.error}",
                    parent=parent or self.root,
                )
        return result

    def submit_editors(self, editors, parent=None):
        self.accept_editors(editors)
        return self.finish(parent)

    def submit_values(self, config, values, parent=None):
        self.manager.accept(config, values)
        return self.finish(parent)

    def complete_text_operation(self, binding, prepared_text=None):
        binding.commit(prepared_text)
        return self.finish(binding.widget.winfo_toplevel())

    def read_text(self, widget):
        """读取一次正文并记录耗时；不接受字段，不请求保存。"""
        start = perf_counter()
        self.metrics["text_reads"] += 1
        try:
            return get_text_value(widget)
        finally:
            self.metrics["text_read_seconds"] += perf_counter() - start

    def prepare_business(self, page, fields=None, parent=None):
        """先接受并固定必要参数，再检查保存；失败不返回执行参数。"""
        self.accept_editors(self.select(page, fields))
        config = self.manager.config_data[page]
        selected_fields = config.keys() if fields is None else fields
        parameters = {
            field: deepcopy(config[field])
            if isinstance(config[field], (dict, list, set))
            else config[field]
            for field in selected_fields
        }
        if not self.finish(parent, include_session=True).persisted:
            return None
        return parameters

    def accept_draft(self, config, values, parent):
        if self.manager._batch_depth:
            raise RuntimeError("可取消草稿必须在独立同步保存阶段接受。")
        previous = {key: deepcopy(config[key]) for key in values}
        completed = False
        result = None
        try:
            self.manager.accept(config, values)
            result = self.manager.save(include_session=True)
            completed = result.persisted
        finally:
            if not completed:
                self.manager.accept(config, previous)

        if not completed:
            safe_show_error(
                "内容未保存",
                "内容仍保留在当前窗口，请重试或选择放弃。\n\n"
                f"{result.error}",
                parent=parent,
            )
        return completed