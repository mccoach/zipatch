# -*- coding: utf-8 -*-

"""名单写入过滤及正文完成边界，不解释规则、不保存或扫描。"""

import sys
from contextlib import contextmanager

from core.rule_lists import clean_rule_quotes


class ListTextEditing:
    """正文修订、业务比较和持久化是不同事实。"""

    def __init__(self, widget, commits, on_text):
        self.widget = widget
        self.commits = commits
        self.on_text = on_text
        self.destroyed = False
        self.depth = 0
        self.revision = 0
        self.completed_revision = 0
        source = widget.get("1.0", "end-1c")
        self.text = clean_rule_quotes(source)
        if source != self.text:
            widget.delete("1.0", "end")
            widget.insert("1.0", self.text)
        # 安装发生在窗口初始化，初始插入和等价清洗不是用户撤销历史。
        widget.edit_reset()
        widget.edit_modified(False)

        self.original_command = widget._w + "_zipatch_original"
        widget.tk.call("rename", widget._w, self.original_command)
        widget.tk.createcommand(widget._w, self.command)
        widget._list_text_editing = self
        widget._edit_text_scope = self.edit_group

        previous = widget._content_operation_completed

        def completed(prepared_text=None):
            final = self.complete()
            return previous(final)

        widget._content_operation_completed = completed
        widget.bind("<<Modified>>", self.on_modified, add="+")
        widget.bind(
            "<Control-grave>",
            widget._text_editor_controller.toggle_disabled_lines,
        )
        # 执行已有 Text 类编辑脚本，再阻止类绑定重复执行。
        # 原生剪贴板语义仍归 Tk；这里只建立同次正文完成和撤销边界。
        for sequence in ("<<Paste>>", "<<PasteSelection>>", "<<Cut>>", "<<Clear>>"):
            widget.bind(
                sequence,
                lambda event, selected=sequence:
                self.class_edit(event, selected),
            )
        widget.bind("<Destroy>", self.on_destroy, add="+")

    def command(self, *arguments):
        attempted = False
        completed = False
        try:
            command = arguments[0]
            history_action = (
                arguments[1]
                if command == "edit" and len(arguments) > 1
                and arguments[1] in ("undo", "redo") else None
            )
            mutation = command in ("insert", "delete", "replace") or (
                history_action is not None
            )
            if mutation and str(self.widget.cget("state")) != "normal":
                completed = True
                return ""

            if history_action is not None:
                capability = "canundo" if history_action == "undo" else "canredo"
                available = self.widget.tk.call(
                    self.original_command, "edit", capability,
                )
                # 没有历史是正常无操作，不制造跨 Tcl 的异常。
                if not self.widget.tk.getboolean(available):
                    completed = True
                    return ""

            if command in ("insert", "replace"):
                arguments = list(arguments)
                first = 2 if command == "insert" else 3
                for index in range(first, len(arguments), 2):
                    arguments[index] = clean_rule_quotes(arguments[index])
                if command == "insert" and not any(
                    arguments[index]
                    for index in range(first, len(arguments), 2)
                ):
                    completed = True
                    return ""
                if command == "replace" and len(arguments) == 4:
                    if self.widget.get(arguments[1], arguments[2]) == arguments[3]:
                        completed = True
                        return ""

            attempted = mutation
            result = self.widget.tk.call(self.original_command, *arguments)
            completed = True
            return result
        finally:
            if attempted:
                # 失败尝试也使旧关联失效，后续收口核验实际正文。
                self.revision += 1
            if not completed:
                # 正常清理命令也可能继承外层正在传播的异常上下文。
                # 只有本次命令未成功返回，才报告其自身的原异常。
                error_type, error, trace = sys.exc_info()
                if error_type is not None:
                    self.widget._root().report_callback_exception(
                        error_type, error, trace,
                    )

    def complete(self, prepared_text=None):
        if self.destroyed or self.depth:
            return self.text
        if self.revision == self.completed_revision:
            return self.text
        # 成功的整体替换已经拥有确定正文；普通编辑及失败后的
        # 再次收口仍读取真实控件，不以旧关联替代实际正文。
        final = (
            self.commits.read_text(
                self.widget, preserve_trailing_newlines=True,
            )
            if prepared_text is None else prepared_text
        )
        self.widget.edit_modified(False)
        if final != self.text:
            # 派生处理成功后才确认当前正文关联；失败不伪装完成。
            self.on_text(final)
            self.text = final
        self.completed_revision = self.revision
        return self.text

    def on_modified(self, event=None):
        if not self.destroyed:
            self.complete()

    @contextmanager
    def edit_group(self, *, prepared_text=None):
        outer = self.depth == 0
        if outer:
            automatic = self.widget.cget("autoseparators")
            self.widget.edit_separator()
            self.widget.configure(autoseparators=False)
        self.depth += 1
        completed = False
        try:
            yield
            completed = True
        finally:
            self.depth -= 1
            if outer:
                self.widget.edit_separator()
                self.widget.configure(autoseparators=automatic)
                if completed:
                    self.complete(prepared_text)

    def class_edit(self, event, sequence):
        if self.destroyed or str(self.widget.cget("state")) != "normal":
            return "break"
        script = self.widget.tk.call("bind", "Text", sequence)
        if not script:
            return "break"
        # Text 标准剪贴板绑定使用窗口及鼠标坐标，不使用正文转义。
        substitutions = {
            "%W": self.widget._w,
            "%x": str(event.x),
            "%y": str(event.y),
            "%X": str(event.x_root),
            "%Y": str(event.y_root),
        }
        for source, target in substitutions.items():
            script = script.replace(source, target)
        with self.edit_group():
            self.widget.tk.eval(script)
        self.widget._content_operation_completed()
        return "break"

    def on_destroy(self, event):
        if event.widget is not self.widget or self.destroyed:
            return
        self.destroyed = True
        self.widget.tk.deletecommand(self.widget._w)
        self.widget._list_text_editing = None
        self.widget._edit_text_scope = None
        self.on_text = None