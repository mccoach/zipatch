# -*- coding: utf-8 -*-

from contextlib import contextmanager
from copy import deepcopy
from time import perf_counter

from core.config import SaveResult, SaveStatus
from core.merged_format import OutputSettingsError, validate_output_settings
from ui.message_dialogs import safe_show_error
from ui.text_editor import get_text_value
from core.rule_lists import RuleListResult, analyze_rule_list, clean_rule_quotes


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
        return self.controller.read_text(
            self.widget,
            preserve_trailing_newlines=getattr(
                self.widget, "_preserve_trailing_newlines", False,
            ),
        )

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

        if prepared_text is None:
            value = self.read_current_value()
        elif getattr(self.widget, "_preserve_trailing_newlines", False):
            value = prepared_text
        else:
            value = prepared_text.rstrip("\n")
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
        if changed:
            # 只通知已接受事实；不把连续输入当成基准变化。
            self.widget.event_generate("<<ConfigAccepted>>")
        return changed

    def on_focus_out(self, event=None):
        if not self.destroyed and not self._suspend_depth:
            editors = (
                self.controller.select(
                    self.page, {"code_header_line", "code_footer_line"},
                )
                if self.page in ("merge", "restore")
                and self.field in ("code_header_line", "code_footer_line")
                else [self]
            )
            self.controller.submit_editors(editors, self.widget.winfo_toplevel())

    def on_return(self, event=None):
        editors = (
            self.controller.select(
                self.page, {"code_header_line", "code_footer_line"},
            )
            if self.page in ("merge", "restore")
            and self.field in ("code_header_line", "code_footer_line")
            else [self]
        )
        self.controller.submit_editors(editors, self.widget.winfo_toplevel())

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
        # 每字段只保留当前配置结果；不累积草稿历史。
        self.rule_results = {}
        self.operation_depth = 0
        self.metrics = {
            "editor_accept_calls": 0,
            "text_reads": 0,
            "text_read_seconds": 0.0,
            "business_starts": 0,
        }

    def prepare_rule(self, config, field, kind, text=None, *, semantics="standard"):
        """只分析请求正文；类别及分析语义相同才复用结果。"""
        if semantics not in ("standard", "exclusion"):
            raise ValueError("未知名单分析语义")
        key = (id(config), field, kind, semantics)
        accepted = config[field]
        source = accepted if text is None else text
        cleaned = clean_rule_quotes(source)
        previous = self.rule_results.get(key)
        if previous is not None:
            result = previous[1]
            if cleaned in (result.source_text, result.normalized_text):
                if source == accepted:
                    self.rule_results[key] = (config, result)
                return result
        result = analyze_rule_list(source, kind, semantics=semantics)
        if source == accepted:
            # 固定业务正文等于当前配置时，首次结果必须保留。
            # 不接受另一份候选，也不以候选替换配置权威正文。
            self.rule_results[key] = (config, result)
        return result

    def remember_rule(self, config, field, result):
        """成功接受并保存后关联结果；失败候选不替代配置结果。"""
        if not isinstance(result, RuleListResult):
            raise TypeError("配置名单必须关联正式分析结果")
        if clean_rule_quotes(config[field]) not in (
            result.source_text, result.normalized_text,
        ):
            raise ValueError("名单结果不对应当前已接受正文")
        self.rule_results[
            (id(config), field, result.object_kind, result.semantics)
        ] = (config, result)

    def register(self, *args, **kwargs):
        return EditorBinding(self, *args, **kwargs)

    def select(self, page=None, fields=None):
        return [
            editor for editor in self.editors
            if (page is None or editor.page == page)
            and (fields is None or editor.field in fields)
        ]

    def accept_editors(self, editors):
        """同次标记联合接受；其他字段保持既有逐编辑器接受语义。"""
        editors = tuple(editors)
        marker_groups = {}
        ordinary = []
        for editor in editors:
            if (
                editor.page in ("merge", "restore")
                and editor.field in ("code_header_line", "code_footer_line")
            ):
                marker_groups.setdefault(
                    (editor.page, id(editor.config)), [],
                ).append(editor)
            else:
                ordinary.append(editor)

        changed = False
        try:
            for group in marker_groups.values():
                pending = [
                    editor for editor in group
                    if not editor.destroyed and not editor._suspend_depth and editor.dirty
                ]
                if not pending:
                    continue
                config = pending[0].config
                values = {
                    editor.field: editor.normalizer(editor.read_current_value())
                    for editor in pending
                }
                validate_output_settings(pending[0].page, {**config, **values})
                # 配置联合接受前，先完成全部显示层准备。
                for editor in pending:
                    value = values[editor.field]
                    if editor.variable.get() != value:
                        editor.variable.set(value)
                accepted = self.manager.accept(config, values)
                histories = self.manager.config_data["entry_history"]
                maximum = self.manager.config_data["settings"]["entry_history_max_items"]
                for editor in pending:
                    self.metrics["editor_accept_calls"] += 1
                    value = values[editor.field]
                    if editor.history_key and value:
                        history = [value] + [
                            previous for previous in histories.get(editor.history_key, [])
                            if previous != value
                        ]
                        accepted |= self.manager.accept(
                            histories, {editor.history_key: history[:maximum]},
                        )
                    editor.dirty = False
                changed |= accepted
                if accepted:
                    for editor in pending:
                        editor.widget.event_generate("<<ConfigAccepted>>")
            for editor in ordinary:
                changed |= editor.commit()
        except OutputSettingsError as error:
            with self.operation():
                safe_show_error(
                    "输出参数未接受",
                    str(error) + "\n\n请修正当前草稿后再继续。",
                    parent=self.root,
                )
        return changed

    @contextmanager
    def operation(self):
        """只保护同步流程生命周期，不合并保存，不跨阶段保持 batch。"""
        self.operation_depth += 1
        try:
            yield
        finally:
            self.operation_depth -= 1

    def finish(self, parent=None, include_session=False):
        try:
            result = self.manager.save(include_session=include_session)
        except OutputSettingsError as error:
            with self.operation():
                safe_show_error("输出配置无效", str(error), parent=parent or self.root)
            return SaveResult(SaveStatus.REJECTED)
        if result.status is SaveStatus.FAILED:
            # 错误提示运行嵌套事件循环；提示返回前不能销毁调用者所属界面。
            # 这里只保护提示生命周期，不合并保存，也不接受其他草稿。
            with self.operation():
                safe_show_error(
                    "配置保存失败",
                    result.failure_description
                    + "\n\n已接受内容仍保留在内存。"
                    "\n重试保存不会自动重新执行业务。",
                    parent=parent or self.root,
                )
        return result

    def submit_editors(self, editors, parent=None):
        editors = tuple(editors)
        self.accept_editors(editors)
        if any(getattr(editor, "dirty", False) for editor in editors):
            return SaveResult(SaveStatus.REJECTED)
        return self.finish(parent)

    def submit_values(self, config, values, parent=None):
        self.manager.accept(config, values)
        return self.finish(parent)

    def complete_text_operation(self, binding, prepared_text=None):
        binding.commit(prepared_text)
        return self.finish(binding.widget.winfo_toplevel())

    def read_text(self, widget, *, preserve_trailing_newlines=False):
        """观测一次读取；规则草稿保留物理行，普通文本语义不变。"""
        start = perf_counter()
        self.metrics["text_reads"] += 1
        try:
            if preserve_trailing_newlines:
                return widget.get("1.0", "end-1c")
            return get_text_value(widget)
        finally:
            self.metrics["text_read_seconds"] += perf_counter() - start

    def prepare_business(self, page, fields=None, parent=None):
        """先接受并固定必要参数，再检查保存；失败不返回执行参数。"""
        editors = self.select(page, fields)
        self.accept_editors(editors)
        if any(getattr(editor, "dirty", False) for editor in editors):
            return None
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
                "候选内容仍保留在当前窗口，共享内存字段已恢复"
                "到本次接受前的值。\n"
                "内存恢复不代表磁盘已经恢复。\n\n"
                + result.failure_description
                + "\n\n重试本窗口保存会发布候选内容；"
                "\n放弃草稿后的后续保存会发布当前已接受配置。"
                "\n关闭草稿窗口本身不执行磁盘回滚。",
                parent=parent,
            )
        return completed