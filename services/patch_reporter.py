# -*- coding: utf-8 -*-

from core.constants import PATCH_PROTOCOL_DESCRIPTION


def format_patch_preview(
    root,
    patch,
    check_results,
    success_count,
    failed_count,
    has_global_errors,
    backup_enabled,
    backup_root,
):
    """
    格式化修改包 Dry Run 预演报告。

    报告明确区分：
    - 单项校验：每个 OP 独立执行校验；
    - 全局校验：修改包路径互斥和内容互斥校验；
    - 最终可执行 OP：综合两级校验后的实际可执行数量。
    """
    ops = patch.get("operations", [])

    single_check_results = [
        item
        for item in check_results
        if item.op_type != "global_contract"
    ]
    global_check_results = [
        item
        for item in check_results
        if item.op_type == "global_contract"
    ]

    single_success_results = [
        item
        for item in single_check_results
        if item.ok
    ]
    single_failed_results = [
        item
        for item in single_check_results
        if not item.ok
    ]

    lines = []
    lines.append("【Dry Run 预演结果】")
    lines.append(f"协议版本：{PATCH_PROTOCOL_DESCRIPTION}")
    lines.append(f"项目根目录：{root}")
    lines.append(f"boundary：{patch.get('boundary')}")
    lines.append(f"操作总数：{len(ops)}")
    lines.append(f"单项校验通过：{len(single_success_results)}")
    lines.append(f"单项校验失败：{len(single_failed_results)}")
    lines.append(f"全局校验：{'不通过' if has_global_errors else '通过'}")
    lines.append(f"最终可执行 OP：{success_count}")

    if backup_enabled:
        lines.append(f"备份目录：{backup_root}")
    else:
        lines.append("备份状态：未启用自动备份")

    lines.append("")
    lines.append("=" * 60)
    lines.append("【单项校验通过】")
    lines.append("=" * 60)

    if not single_success_results:
        lines.append("无")
    else:
        for item in single_success_results:
            lines.append(
                f'[通过] id="{item.op_id}" {item.op_type} path="{item.path}"'
            )

    lines.append("")
    lines.append("=" * 60)
    lines.append("【单项校验失败】")
    lines.append("=" * 60)

    if not single_failed_results:
        lines.append("无")
    else:
        for item in single_failed_results:
            lines.append("")
            lines.append(
                f'[失败] id="{item.op_id}" {item.op_type} path="{item.path}"'
            )
            lines.append("")
            lines.append("失败原因：")
            lines.append(item.message)
            lines.append("")
            lines.append("修改包定位：")
            lines.append("请在修改包中搜索：")
            lines.append(item.locator)
            lines.append("-" * 60)

    lines.append("")
    lines.append("=" * 60)
    lines.append("【全局校验】")
    lines.append("=" * 60)

    if not has_global_errors:
        lines.append("通过：未发现路径互斥或内容互斥冲突。")
    else:
        lines.append("不通过：修改包存在全局冲突，禁止执行任何 OP。")

        if single_success_results:
            lines.append("单项校验通过的 OP 也因全局冲突而不可执行。")

        for item in global_check_results:
            lines.append("")
            lines.append("冲突详情：")
            lines.append(item.message)

            if item.locator:
                lines.append("")
                lines.append("相关 OP 定位：")
                lines.append("请在修改包中搜索：")
                lines.append(item.locator)

            lines.append("-" * 60)

    lines.append("")

    if failed_count == 0:
        lines.append("Dry Run 全部校验通过。可以执行完整修改包。")
    elif has_global_errors:
        lines.append("Dry Run 存在全局冲突。为避免互相踩踏，当前禁止执行任何 OP，请修正修改包后重新预演。")
    elif success_count > 0:
        lines.append("Dry Run 存在单项校验失败。执行时将只允许执行校验通过的 OP，失败 OP 会被跳过。")
    else:
        lines.append("Dry Run 单项校验全部失败。没有可执行的 OP。")

    return "\n".join(lines)