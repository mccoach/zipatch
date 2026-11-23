# -*- coding: utf-8 -*-

import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, TclError

from core.content_publication import ContentPublicationError
from services.patch_protocol_doc_sync import invalidate_protocol_doc_update
from core.config import SaveStatus
from core.constants import THEME
from ui.message_dialogs import safe_ask_risk_confirm, safe_ask_yes_no, safe_show_error, safe_show_info
from ui.widgets import open_path_with_default_app
from core.path_validation import normalize_windows_display_path
from core.paths import (
    get_app_dir, get_initial_dir_from_path,
    is_frozen_app, validate_required_path,
)
from core.recovery_paths import identity
from ui.text_editor import (
    get_text_value, replace_text_keep_undo,
    replace_text_preserve_view, set_text_value,
)
from ui.operation_lock import locked_page
from ui.patch_op_selection import PatchOpSelection
from core.time_utils import current_timestamp_text
from panels.base_panel import BasePanel, publication_failure_title
from services.patch_backup import resolve_backup_base_dir
from services.patch_file_plan import patch_observation_kind
from ui.backup_restore import open_last_backup_folder
from services.patch_protocol_doc_service import get_patch_protocol_doc
from services.patch_protocol_doc_sync import (
    apply_protocol_doc_update, is_protocol_doc_update_available, preview_protocol_doc_update,
)
from services.patch_service import (
    apply_patch, invalidate_preview, preview_patch,
)

from ui.dialogs import create_managed_text_box
from ui.newline_settings import edit_newline_settings
from ui.theme import styled_button, styled_frame
from ui.widgets import browse_folder, create_entry_row, make_checkbutton, make_radiobutton
from ui.window_manager import center_window, register_popup, unregister_popup


# 弹窗只提供概览；完整检查正文始终保留在预演报告中。
PREVIEW_FAILURE_SUMMARY_ITEMS = 3
PREVIEW_FAILURE_REASON_CHARACTERS = 320
PREVIEW_FAILURE_PATH_CHARACTERS = 160


class PatchPanel(BasePanel):
    config_key = "patch"

    def build(self):
        body = self.make_body()
        self.project_root = tk.StringVar(value=self.cfg["project_root"])
        self.backup_dir = tk.StringVar(value=self.cfg["backup_dir"])
        self.last_backup_root = ""
        self.project_root_entry = create_entry_row(
            body, "项目根目录", self.project_root,
            commits=self.commits, config=self.cfg, field="project_root", page="patch",
            history_key="patch.project_root",
            value_normalizer=normalize_windows_display_path,
            browse_command=lambda: browse_folder(
                self.project_root, "选择项目根目录", parent=self.root,
            ),
            open_command=lambda: open_path_with_default_app(
                self.project_root.get(), self.root,
            ),
        )
        self.backup_dir_entry = create_entry_row(
            body, "备份目录", self.backup_dir,
            commits=self.commits, config=self.cfg, field="backup_dir", page="patch",
            history_key="patch.backup_dir",
            value_normalizer=normalize_windows_display_path,
            browse_command=lambda: self.browse_project_relative_folder(
                self.backup_dir, "选择备份保存目录",
            ),
            open_command=self.open_configured_backup_dir,
            tooltip_text="相对备份目录基于项目根目录；绝对目录直接定位。",
        )
        options = styled_frame(body, bg=THEME["bg_panel"])
        options.pack(fill="x", pady=(10, 0))
        for field, label in (
            ("allow_delete", "允许删除文件/文件夹（危险）"),
            ("allow_multi_replace_exact", "允许多处精确替换"),
            ("normalize_text_newlines", "标准化文本换行"),
            ("open_backup_after_done", "完成后打开本次备份"),
        ):
            variable = tk.BooleanVar(value=self.cfg[field])
            setattr(self, field, variable)
            make_checkbutton(
                options, label, variable, commits=self.commits,
                config=self.cfg, field=field,
            ).pack(side="left", padx=(0, 12))
        area = styled_frame(body, bg=THEME["bg_panel"])
        area.pack(fill="both", expand=True, pady=(10, 0))
        area.grid_columnconfigure(0, weight=1, uniform="patch_text_columns")
        area.grid_columnconfigure(1, weight=1, uniform="patch_text_columns")
        area.grid_rowconfigure(0, weight=1)
        left, right = styled_frame(area), styled_frame(area)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.patch_text = create_managed_text_box(
            left, "修改包源码", self.cfg["patch_text"],
            height=22, mono=True, show_statistics=True,
            commits=self.commits, config=self.cfg,
            field="patch_text", page="patch", favorite_key="patch_text",
            wrap="none", enable_import_text=True,
            wrap_config_key="patch.patch_text",
            preserve_trailing_newlines=True, preserve_import_newlines=True,
            title_action_factory=lambda text, title_row:
            PatchOpSelection(text, title_row, self.commits),
        )
        self.result_text = create_managed_text_box(
            right, "预演 / 执行结果", self.cfg["last_result_text"],
            height=22, mono=True, readonly=True, commits=self.commits,
            enable_favorites=False, enable_copy=True,
            wrap="none", wrap_config_key="patch.result_text",
            on_content_changed=self.accept_result_control,
        )
        row = styled_frame(body, bg=THEME["bg_panel"])
        row.pack(fill="x", pady=(12, 0))
        styled_button(row, "一键清空", self.clear_all, width=10).pack(
            side="left", padx=(0, 8),
        )
        styled_button(row, "打开本次备份", self.open_last_backup_dir, width=12).pack(
            side="left",
        )
        styled_button(row, "换行设置", self.open_newline_settings, width=10).pack(
            side="left", padx=(8, 0),
        )
        self.apply_button = styled_button(
            row, "预演并执行", self.apply, width=12, danger=True,
        )
        self.apply_button.pack(side="right", padx=(8, 0))
        self.preview_button = styled_button(
            row, "仅预演", self.preview, width=14, accent=True,
        )
        self.preview_button.pack(side="right")
        styled_button(row, "修改包协议规范", self.show_protocol_doc, width=16).pack(
            side="right", padx=(0, 8),
        )

    def resolve_project_relative_initial_dir(self, value):
        root_text = normalize_windows_display_path(self.project_root.get())
        if not root_text:
            return None
        from core.recovery_paths import checked_chain
        root, root_chain = checked_chain(
            Path(root_text).expanduser(), include_observation=True,
        )
        if root_chain[-1][1] != "directory":
            return None
        value = normalize_windows_display_path(value)
        target = Path(value).expanduser() if value else root
        if not target.is_absolute():
            target = root / target
        return get_initial_dir_from_path(str(target)) or str(root)

    def browse_project_relative_folder(self, variable, title):

        return browse_folder(
            variable, title,
            initial_dir=self.resolve_project_relative_initial_dir(variable.get()),
            parent=self.root,
        )

    def open_configured_backup_dir(self):
        try:
            editors = self.commits.select("patch", {"project_root"})
            self.commits.submit_editors(editors, self.root)
            if any(editor.dirty for editor in editors):
                return
            path = resolve_backup_base_dir(
                self.cfg["project_root"], self.cfg["backup_dir"],
            )
        except (OSError, ValueError) as error:
            safe_show_error("打开失败", str(error), self.root)
            return
        open_path_with_default_app(str(path), self.root)

    def open_newline_settings(self):
        if edit_newline_settings(self.root, self.commits):
            self.log("换行设置已保存")

    def prepare_parameters(self):
        parameters = self.prepare({
            "project_root", "backup_dir", "open_backup_after_done",
            "allow_delete", "allow_multi_replace_exact",
            "normalize_text_newlines", "patch_text",
        })
        if parameters is not None:
            parameters["output_rules"] = (
                self.commits.prepare_rule(
                    self.config_data["settings"], "crlf_file_list", "file",
                ) if parameters["normalize_text_newlines"] else None
            )
        return parameters

    def display_result(self, text):
        self.result_text.configure(state="normal")
        replace_text_preserve_view(self.result_text, text)
        self.result_text.configure(state="disabled")
        return text

    def accept_result_control(self):
        if self.commits.operation_depth:
            return False
        text = get_text_value(self.result_text)
        return self.commits.submit_values(
            self.cfg, {"last_result_text": text}, self.root,
        ).request_satisfied

    def clear_all(self):
        """清空左右正文；路径、收藏、开关和运行日志保持。"""
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            replace_text_keep_undo(self.patch_text, "")
            self.display_result("")
            self.patch_text._config_binding.commit("")
            self.commits.manager.accept(self.cfg, {"last_result_text": ""})
            self.commits.finish(self.root, include_session=True)
            self.log("修改包执行器：已一键清空输入和结果")

    def result_section(self, title, content, cfg):
        mode = "执行修改"
        return (
            "=" * 60 + "\n"
            f"【{current_timestamp_text()}｜{title}｜{mode}】\n"
            + "=" * 60 + "\n" + content
        )

    def finish_result(self, text, updates=None, outcome="预演已完成"):
        # 已接受结果字段是历史权威源，控件只是其展示。
        # 组合操作的中间预演未接受，最终完整记录只追加一次。
        history = self.cfg["last_result_text"]
        final = self.display_result(
            history + "\n\n" + text if history else text
        )
        values = {"last_result_text": final}
        if updates:
            values.update(updates)
        self.commits.manager.accept(self.cfg, values)
        result = self.commits.manager.save(include_session=True)
        if result.status is SaveStatus.FAILED:
            safe_show_error(
                "结果未持久化",
                f"{outcome}。\n\n"
                + result.failure_description
                + "\n\n完整结果和关联设置仍保留在已接受内存配置中。"
                "\n保存重试不会再次追加记录，也不会自动再次执行业务。",
                self.root,
            )
        return result

    def run_preview(self, cfg):
        """消费本次固定参数；计算结果不写入面板或配置状态。"""
        project = validate_required_path(cfg["project_root"], "项目根目录")

        patch = validate_required_path(cfg["patch_text"], "修改包内容")
        from ui.patch_link_authorization import (
            make_patch_enumeration_selector, make_patch_scope_authorizer,
        )
        return preview_patch(
            authorize_patch_scope=make_patch_scope_authorizer(
                self.root, self.commits,
            ),
            select_patch_enumeration=make_patch_enumeration_selector(
                self.root, self.commits,
            ),
            output_rules=cfg["output_rules"],
            normalize_text_newlines=cfg["normalize_text_newlines"],
            project_root=project, patch_text=patch,
            allow_delete=cfg["allow_delete"],
            allow_multi_replace_exact=cfg["allow_multi_replace_exact"],
            backup_dir=cfg["backup_dir"],
        )

    def preview_failure(self, error, cfg):
        content = "【Dry Run 失败】\n\n" + str(error) + "\n\n" + traceback.format_exc()
        self.finish_result(
            self.result_section("Dry Run 失败", content, cfg),
            outcome="预演未能完成，以下保存失败仅针对失败报告",
        )
        self.set_status("Dry Run 失败")
        reason = str(error)
        if len(reason) > PREVIEW_FAILURE_REASON_CHARACTERS:
            reason = (
                reason[:PREVIEW_FAILURE_REASON_CHARACTERS]
                + "…（完整错误见报告）"
            )
        safe_show_error(
            "Dry Run 失败",
            "预演未能完成，本次不执行工作目标修改。\n\n"
            f"错误类型：{type(error).__name__}\n"
            f"主要原因：{reason}\n\n"
            "完整错误及上下文已保留在结果报告中。"
            "请排除问题后重新预演。",
            self.root,
        )

    def failure_message(self, result):
        """概述本次检查事实，不从历史报告或保存错误重建诊断。"""
        failures = tuple(check for check in result.check_results if not check.ok)
        if not failures:
            raise ValueError("失败概述必须消费包含失败检查的本次预演结果")

        single_failures = sum(
            check.op_type != "global_contract" for check in failures
        )
        global_failures = len(failures) - single_failures
        lines = [
            "Dry Run 未通过，整包禁止执行。",
            "",
            f"操作总数：{len(result.patch['operations'])}",
            f"单项检查失败：{single_failures}；全局检查失败：{global_failures}",
            "最终可执行 OP：0",
            "单项检查通过不表示取得部分执行资格。",
            "本次没有开始工作目标修改。",
            "",
            "主要问题：",
        ]
        # 全局冲突先展示，避免三个单项失败遮住整包范围冲突。
        selected = sorted(
            failures, key=lambda check: check.op_type != "global_contract",
        )[:PREVIEW_FAILURE_SUMMARY_ITEMS]
        for number, check in enumerate(selected, 1):
            path = check.path
            if len(path) > PREVIEW_FAILURE_PATH_CHARACTERS:
                path = (
                    path[:PREVIEW_FAILURE_PATH_CHARACTERS]
                    + "…（完整路径见报告）"
                )
            if check.op_type == "global_contract":
                heading = "全局检查"
                if check.op_id:
                    heading += f'，相关 OP：id="{check.op_id}"'
            else:
                heading = f'id="{check.op_id}" {check.op_type}'
            lines.append(f"{number}. {heading}")
            lines.append(f"   路径：{path}")
            reason = check.message
            if len(reason) > PREVIEW_FAILURE_REASON_CHARACTERS:
                reason = (
                    reason[:PREVIEW_FAILURE_REASON_CHARACTERS]
                    + "…（完整原因及相关 OP 见报告）"
                )
            lines.append("   原因：" + reason.replace("\n", "\n   "))

        remaining = len(failures) - len(selected)
        if remaining:
            lines.append(f"\n另有 {remaining} 条失败检查，详见完整预演报告。")
        lines.extend((
            "",
            "完整报告保留全部原因、路径及相关 OP。",
            "请修正规则、锚点或路径问题后重新预演；不执行通过子集。",
        ))
        return "\n".join(lines)

    def preview(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation(), locked_page(self):
            return self._preview()

    def _preview(self):
        cfg = self.prepare_parameters()
        if cfg is None:
            return

        self.set_status("正在执行 Dry Run 预演...")
        result = None
        try:
            try:
                self.commits.metrics["business_starts"] += 1
                result = self.run_preview(cfg)
            except (OSError, ValueError) as error:
                self.preview_failure(error, cfg)
                return
            self.set_status("Dry Run 预演完成")
            self.log("Dry Run 预演完成")
            saved = self.finish_result(
                self.result_section("Dry Run 预演", result.preview_text, cfg),
            )
            if result.failed_count:
                safe_show_error(
                    "Dry Run 存在失败项", self.failure_message(result), self.root,
                )
            elif saved.persisted:
                safe_show_info(
                    "Dry Run 完成",
                    "校验完成，请查看右侧预演结果。\n"
                    "本次独立预演结束后任务作废；执行时会重新准备并确认。",
                    self.root,
                )
        finally:
            if result is not None:
                invalidate_preview(result)

    def build_apply_operation_summary(self, cfg, preview):
        """确认范围只从固定计划派生，不读取磁盘或消费内部执行单元。"""
        groups = [
            ("create", "新增文件"), ("create_dir", "创建目录"),
            ("overwrite", "覆盖已有路径"), ("append_text", "追加文本"),
            ("replace_exact", "精确替换"), ("replace_between", "锚点区间替换"),
            ("rename_file", "文件改名"), ("move_file", "移动文件"),
            ("copy_file", "复制文件"), ("rename_dir", "目录改名"),
            ("move_dir", "移动目录"), ("copy_dir", "复制目录"),
            ("delete_file", "删除文件"), ("delete_dir", "删除文件夹"),
            ("skip", "固定跳过"),
        ]
        actions = preview.execution.plan.actions
        paths = {key: [] for key, _ in groups}
        counts = {key: 0 for key, _ in groups}
        object_paths = {"file": set(), "directory": set()}
        absent_paths = {"file": set(), "directory": set()}
        replacements = 0
        for action in actions:
            operation = action.payload
            kind = operation["op"]

            # expectations 已持有实际源、目标身份及预演存在性。
            # 不存在的目标没有实体类型，按已校验操作的文件/目录语义分类。
            for expected in action.expectations:
                observed_kind = patch_observation_kind(expected)
                object_kind = (
                    "file" if observed_kind == "file_symlink" else
                    "directory" if observed_kind in (
                        "directory_symlink", "junction",
                    ) else observed_kind
                )
                if object_kind == "absent":
                    object_kind = "directory" if kind.endswith("_dir") else "file"
                if object_kind not in object_paths:
                    raise ValueError("执行确认遇到固定计划中的非法对象类型")
                path_key = identity(expected.path)
                object_paths[object_kind].add(path_key)
                if observed_kind == "absent":
                    absent_paths[object_kind].add(path_key)

            display = operation["path"]
            group = kind
            if not action.execute:
                group = "skip"
            elif kind == "write_file":
                group = (
                    "create" if action.expectations[0].kind == "absent"
                    else "overwrite"
                )
            if "new_path" in operation:
                display += " -> " + operation["new_path"]
                if (
                    action.execute
                    and patch_observation_kind(action.expectations[1]) != "absent"
                ):
                    group = "overwrite"
            counts[group] += 1
            if group == "replace_exact":
                replacements += int(operation["count"])
            if display not in paths[group]:
                paths[group].append(display)
        existing = {
            "overwrite", "append_text", "replace_exact", "replace_between",
            "rename_file", "move_file", "copy_file", "rename_dir",
            "move_dir", "copy_dir", "delete_file", "delete_dir",
        }
        return {
            "groups": groups, "paths": paths, "counts": counts,
            "replacements": replacements, "existing": existing,

            "total_op_count": len(actions),
            "file_path_count": len(object_paths["file"]),
            "directory_path_count": len(object_paths["directory"]),
            "absent_file_path_count": len(absent_paths["file"]),
            "absent_directory_path_count": len(absent_paths["directory"]),
        }

    def format_grouped_file_list(self, summary, only_existing=False):
        lines = [
            f"总 OP：{summary['total_op_count']} 个（包含固定跳过，不代表提交数量）。",
            f"涉及路径：文件 {summary['file_path_count']} 个；"
            f"目录 {summary['directory_path_count']} 个。",
            "其中预演时尚不存在："
            f"文件目标 {summary['absent_file_path_count']} 个；"
            f"目录目标 {summary['absent_directory_path_count']} 个。",
            "路径按实际身份去重；双路径操作计入源和目标，目录内部对象不展开统计。",
            "尚不存在的目标按操作类型分类，不表示对应对象已经存在。",
            "",
            "【分类操作清单】",
            "",
        ]
        for key, title in summary["groups"]:
            if only_existing and key not in summary["existing"]:
                continue
            paths = summary["paths"][key]
            if not paths:
                continue
            extra = f"，文本替换 {summary['replacements']} 处" if key == "replace_exact" else ""
            lines.append(
                f"{title}：OP {summary['counts'][key]} 个{extra}，"
                f"路径记录 {len(paths)} 条"
            )
            lines.extend(f"- {path}" for path in paths)
            lines.append("")
        return "\n".join(lines).rstrip()

    def confirm_apply_execution(self, cfg, patch):
        summary = self.build_apply_operation_summary(cfg, patch)
        backup = (
            "本次强制备份：全部必要原状态及完整清单保存成功后才开始修改。"
            "\n备份失败清理本次任务目录，清理失败提示人工处理。"
            "\n工作失败自动恢复完整原备份，不生成二次备份，不自动续执行。"
            "\n执行消费固定成果；操作期间请勿外部修改涉及文件。"
        )
        return safe_ask_risk_confirm(
            "执行确认",
            "即将执行文件修改。\n\n" + backup + "\n\n【本次操作清单】\n\n"
            + self.format_grouped_file_list(summary) + "\n\n确认执行？",
            self.root,
        )

    def apply(self):
        if self.commits.operation_depth:
            return
        with self.commits.operation(), locked_page(self):
            return self._apply()

    def _apply(self):
        cfg = self.prepare_parameters()
        if cfg is None:
            return

        self.set_status("正在执行 Dry Run 预演...")
        preview = None
        try:
            try:
                self.commits.metrics["business_starts"] += 1
                preview = self.run_preview(cfg)
            except (OSError, ValueError) as error:
                self.preview_failure(error, cfg)
                return

            self.set_status("Dry Run 预演完成")
            self.log("Dry Run 预演完成")
            report = self.result_section("Dry Run 预演", preview.preview_text, cfg)
            history = self.cfg["last_result_text"]
            self.display_result(history + "\n\n" + report if history else report)
            if preview.failed_count:
                self.finish_result(report)
                safe_show_error(
                    "Dry Run 未通过，整包禁止执行",
                    self.failure_message(preview), self.root,
                )
                return
            if not self.confirm_apply_execution(cfg, preview):
                invalidate_preview(preview)
                self.set_status("本次操作已取消，任务已作废")
                self.finish_result(
                    report,
                    outcome="本次操作已取消，任务已作废，工作目标未修改",
                )
                return

            try:
                self.set_status("正在执行修改包...")
                self.commits.metrics["business_starts"] += 1
                result = apply_patch(preview)
            except (OSError, ValueError) as error:
                title = publication_failure_title(error) or "执行失败"
                content = str(error) + "\n\n" + traceback.format_exc()
                self.finish_result(
                    report + "\n\n" + self.result_section(title, content, cfg),
                    outcome="执行流程已停止；以下保存失败仅针对本次结果记录",
                )
                self.set_status(title)
                protection = preview.execution.protection
                if (
                    protection is not None
                    and preview.execution.backup_completed
                    and protection.directory_results
                ):
                    self.last_backup_root = str(protection.task_root)
                safe_show_error(
                    title,
                    str(error)
                    + "\n\n请以上述失败处理结果判断当前状态。"
                    "\n最后清理或回滚失败时，请人工处理。"
                    "\n旧任务已失效，不会自动再次执行修改包。",
                    self.root,
                )
                return

            self.last_backup_root = result.backup_root
            saved = self.finish_result(
                report + "\n\n" + self.result_section("执行结果", result.log_text, cfg),
                outcome="文件修改已经完成",
            )
            self.set_status("修改包执行完成")
            self.log(f"文件修改已经完成，备份目录：{result.backup_root}")
            if saved.persisted:
                safe_show_info("执行完成", "业务已完成，请查看执行结果。", self.root)
            if cfg["open_backup_after_done"] and result.backup_root:
                open_path_with_default_app(result.backup_root, self.root)
        finally:
            if preview is not None:
                invalidate_preview(preview)

    def open_last_backup_dir(self):
        return open_last_backup_folder(self)

    def format_protocol_doc_source_desc(self, doc):
        text = "当前显示：内置修改包协议规范"
        if doc.updated_at:
            text += f"    更新时间：{doc.updated_at}"
        if doc.source_sha256:
            text += f"    指纹：{doc.source_sha256[:12]}..."
        return text

    def show_protocol_doc(self):
        doc = get_patch_protocol_doc()
        dialog = tk.Toplevel(self.root)
        dialog.title("修改包协议规范")
        dialog.configure(bg=THEME["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        center_window(dialog, 980, 740)
        description = tk.StringVar(value=self.format_protocol_doc_source_desc(doc))
        tk.Label(
            dialog, textvariable=description, bg=THEME["bg"], fg=THEME["fg_dim"],
            font=THEME["font_main"], anchor="w", wraplength=930,
        ).pack(fill="x", padx=16, pady=(12, 4))
        body = styled_frame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        text = create_managed_text_box(
            body, "协议规范内容", doc.content, height=30, mono=True,
            readonly=True, commits=self.commits, enable_favorites=False,
            enable_clear=False, wrap_config_key="patch.protocol_doc_text",
        )
        bottom = styled_frame(dialog)
        bottom.pack(fill="x", padx=16, pady=(4, 14))

        def close():
            if self.commits.operation_depth:
                return False
            unregister_popup(dialog)
            dialog.destroy()
            return True

        if is_protocol_doc_update_available():
            styled_button(
                bottom, "从 Markdown 更新",
                lambda: self.update_protocol_doc_from_markdown(dialog, text, description),
                width=16,
            ).pack(side="left")
        styled_button(bottom, "关闭", close, width=10).pack(side="right")
        register_popup(dialog, close)

    def update_protocol_doc_from_markdown(self, dialog, text, description):
        if self.commits.operation_depth:
            return
        with self.commits.operation():
            return self._update_protocol_doc_from_markdown(dialog, text, description)

    def _update_protocol_doc_from_markdown(self, dialog, text, description):
        if is_frozen_app():
            safe_show_error("当前环境不支持更新", "请在源码环境更新后重新封装。", dialog)
            return
        current = self.cfg["protocol_doc_source_path"]
        selected = filedialog.askopenfilename(
            title="选择修改包协议规范 Markdown 源文档",
            initialdir=get_initial_dir_from_path(current) or str(get_app_dir()),
            initialfile=Path(current).name if current else "Zipatch_V3_修改包协议规范.md",
            filetypes=[("Markdown Documents", "*.md"), ("All Files", "*.*")],
            parent=dialog,
        )
        if not selected:
            return
        selected = str(Path(selected).expanduser())
        self.commits.manager.accept(
            self.cfg, {"protocol_doc_source_path": selected},
        )
        if not self.commits.finish(dialog, include_session=True).persisted:
            return

        preview = None
        publication = None
        stage = "确认前固定来源、目标及完整模块成果准备"
        try:
            preview = preview_protocol_doc_update(selected)
            if not preview.has_difference:
                safe_show_info("无需更新", "所选文档与当前内置文档内容一致。", dialog)
                return
            message = (
                f"当前内置字符数：{preview.embedded_length}\n"
                f"当前指纹：{preview.embedded_sha256}\n\n"
                f"源文档：{preview.source_path}\n"
                f"编码：{preview.source_encoding}\n"
                f"字符数：{preview.source_length}\n"
                f"指纹：{preview.source_sha256}\n\n"
                "完整模块已生成并完成非执行式语法检查。\n"
                "确认后只发布本次固定成果；来源或目标变化则停止。\n\n"
                "是否更新内置协议文档？"
            )
            stage = "当次更新确认"
            if not safe_ask_yes_no("发现协议文档差异", message, dialog):
                return
            stage = "固定模块成果正式发布"
            publication = apply_protocol_doc_update(preview)

            stage = "正式模块加载"
            refreshed = get_patch_protocol_doc()
            stage = "协议正文显示刷新"
            text.configure(state="normal")
            try:
                set_text_value(text, refreshed.content)
            finally:
                text.configure(state="disabled")
            stage = "协议来源描述刷新"
            description.set(self.format_protocol_doc_source_desc(refreshed))
            stage = "更新完成日志"
            self.log(f"内置修改包协议规范已更新：{publication.target}")
            stage = "更新完成状态"
            self.set_status("内置协议文档已更新")
            stage = "更新完成提示"
            safe_show_info(
                "更新完成",
                "内置协议文档已更新；发布 exe 时请重新封装。",
                dialog,
            )
        except ContentPublicationError as error:
            receipt = error.result
            title = (
                "模块原对象写回未完整结束"
                if receipt.mechanism == "in_place" else
                "模块替换结果未知"
                if receipt.replacement == "unknown" else
                "模块已替换，后续确认未完成"
                if receipt.replacement == "replaced" else
                "模块发布前复核未通过"
            )
            safe_show_error(
                title,
                "本次模块发布没有完整成功，不刷新协议显示。\n"
                "目标改变、正文完成、确认和应急状态如下；"
                "不自动重试或回滚，请重新建立更新任务。\n\n"
                + str(error),
                dialog,
            )
        except (OSError, ValueError, SyntaxError, ImportError, TclError) as error:
            if publication is not None:
                safe_show_error(
                    "模块已提交，后续处理未完成",
                    f"正式模块已经确认发布：{publication.target}\n"
                    f"发布机制：{publication.mechanism}\n"
                    f"失败阶段：{stage}\n"
                    "加载、显示或附属处理未完成，不代表模块未修改。\n"
                    "本次未自动回滚；请重新打开协议窗口或重启后核对。\n\n"
                    f"{error}",
                    dialog,
                )
            else:
                safe_show_error(
                    "协议模块未提交",
                    f"失败阶段：{stage}\n"
                    "本次没有取得模块发布成功结果，"
                    "未通过直接覆盖或旧入口继续更新。\n\n"
                    f"{error}",
                    dialog,
                )
        finally:
            if preview is not None:
                invalidate_protocol_doc_update(preview)
