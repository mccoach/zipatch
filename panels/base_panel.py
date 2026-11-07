# -*- coding: utf-8 -*-

import tkinter as tk


from core.constants import THEME
from core.content_publication import ContentPublicationResult
from ui.message_dialogs import safe_show_error

from services.restore_service import RestoreExecutionError
from services.patch_executor import PatchExecutionError
from services.patch_restore import BackupRestoreExecutionError
from services.stream_output import StreamOutputError, StreamOutputPostError
from ui.theme import styled_frame



def publication_failure_title(error):
    """只解释当前异常明确拥有的业务事实，不搜索文字或异常原因。"""
    if isinstance(error, StreamOutputPostError):
        return "业务动作已完成，附属处理失败"
    if isinstance(error, PatchExecutionError):
        if error.role == "正式保护":
            return "修改包正式保护或事实发布未完整结束"
        if error.role == "辅助输出":
            return "修改包工作已完成，辅助输出失败"
        if error.work_completed:
            return "修改包工作已完成，后续流程失败"
        if error.results:
            current = error.results[-1]
            if isinstance(current, ContentPublicationResult) and not current.succeeded:
                if not current.mutation_attempted:
                    return "修改包文件发布前复核失败"
                if current.mechanism == "in_place":
                    return (
                        "修改包正文已完成，后续处理失败"
                        if current.content_completed else
                        "修改包原对象写回未完整结束"
                    )
                if current.replacement == "unknown":
                    return "修改包文件替换结果未知"
                return "修改包文件已替换，后续核验或确认失败"
        return "修改包执行流程未完整结束"
    if isinstance(error, BackupRestoreExecutionError):
        if error.role == "正式保护":
            return "正式恢复保护或事实发布未完整结束"
        auxiliary = error.auxiliary_result
        if error.role == "辅助输出":
            if auxiliary is not None and auxiliary.replacement == "unknown":
                return "恢复工作已完成，辅助日志替换结果未知"
            return "恢复工作已完成，辅助日志未完整结束"
        result = error.result
        if result is not None and result.completed:
            return "恢复工作已完成，后续流程失败"
        if result is not None and result.results:
            current = result.results[-1]
            if isinstance(current, ContentPublicationResult) and not current.succeeded:
                if not current.mutation_attempted:
                    return "恢复文件发布前复核失败"
                if current.mechanism == "in_place":
                    return (
                        "恢复正文已完成，后续处理失败"
                        if current.content_completed else
                        "恢复原对象写回未完整结束"
                    )
                if current.replacement == "unknown":
                    return "恢复文件替换结果未知"
                return "恢复文件已替换，后续核验或确认失败"
        return "正式恢复流程未完整结束"
    if isinstance(error, RestoreExecutionError):
        result = error.result
        auxiliary = result.log_result
        if auxiliary is not None and not auxiliary.succeeded:
            if not auxiliary.mutation_attempted:
                return "拆分辅助日志发布前复核失败"
            if auxiliary.mechanism == "in_place":
                return (
                    "拆分辅助日志正文已完成，后续处理失败"
                    if auxiliary.content_completed else
                    "拆分辅助日志原对象写回未完整结束"
                )
            if auxiliary.replacement == "unknown":
                return "拆分辅助日志替换结果未知"
            return "拆分辅助日志已替换，后续核验或确认失败"

        # 文件完成来自所属协调的最终核验事实；
        # 正常步骤前缀、正文确认和日志成功都不是整任务成功证明。
        if (
            error.task.plan.files
            and len(result.completed_files) == len(error.task.plan.files)
        ):
            return "拆分文件已完成，后续流程失败"

        if result.results:
            current = result.results[-1]
            if not current.succeeded:
                if isinstance(current, ContentPublicationResult):
                    if not current.mutation_attempted:
                        return "拆分文件发布前复核失败"
                    if current.mechanism == "in_place":
                        return (
                            "拆分文件正文已完成，后续处理失败"
                            if current.content_completed else
                            "拆分文件原对象写回未完整结束"
                        )
                    if current.replacement == "unknown":
                        return "拆分文件替换结果未知"
                    return "拆分文件已替换，后续核验或确认失败"
                return "拆分目录步骤未完整结束"
        return "拆分流程未完整结束"
    if isinstance(error, StreamOutputError):

        publication = error.result.publication
        if publication is None or not publication.mutation_attempted:
            return "成果未发布，输出流程已停止"
        if publication.mechanism == "in_place":
            return (
                "成果正文已完成，后续处理失败"
                if publication.content_completed else
                "成果原对象写回未完整结束"
            )
        if publication.replacement == "unknown":
            return "成果文件替换结果未知"
        return "成果文件已替换，后续核验或确认失败"
    return None


class BasePanel(tk.Frame):
    """面板组织自己的操作；编辑和保存通过应用提交控制器协作。"""

    config_key = None

    def __init__(self, parent, app):
        super().__init__(parent, bg=THEME["bg_panel"])
        self.app = app
        self.root = app.root
        self.config_data = app.config_data
        self.cfg = self.config_data[self.config_key]
        self.commits = app.commits
        self.log = app.log
        self.set_status = app.status_var.set
        self.build()

    def build(self):
        raise NotImplementedError

    def make_body(self):
        body = styled_frame(self, bg=THEME["bg_panel"])
        body.pack(fill="x")
        return body

    def prepare(self, fields=None, parent=None):
        return self.commits.prepare_business(self.config_key, fields, parent)

    def handle_error(self, title, error, parent=None):
        status = publication_failure_title(error) or "执行流程未完整结束"
        self.set_status(status)
        self.log(f"{title}：{status}：{error}")
        safe_show_error(status, str(error), parent=parent or self.root)