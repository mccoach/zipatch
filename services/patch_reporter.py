# -*- coding: utf-8 -*-


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

    PatchExecutor 负责校验和生成结果对象；
    本模块只负责把结果对象转换成人可读文本，避免执行器同时承担展示职责。
    """
    ops = patch.get("operations", [])

    lines = []
    lines.append("【Dry Run 预演结果】")
    lines.append("协议版本：AI_FILE_PATCH_V2 动态 boundary 原文块协议")
    lines.append(f"项目根目录：{root}")
    lines.append(f"boundary：{patch.get('boundary')}")
    lines.append(f"操作数量：{len(ops)}")
    lines.append(f"校验成功：{success_count}")
    lines.append(f"校验失败：{failed_count}")

    if has_global_errors:
        lines.append("全局冲突：存在，禁止执行任何 OP")
    else:
        lines.append("全局冲突：无")

    if backup_enabled:
        lines.append(f"备份目录：{backup_root}")
    else:
        lines.append("备份状态：未启用自动备份")

    lines.append("")
    lines.append("=" * 60)
    lines.append("【校验成功】")
    lines.append("=" * 60)

    for item in check_results:
        if item.ok:
            lines.append(
                f'[通过] id="{item.op_id}" {item.op_type} path="{item.path}"'
            )

    lines.append("")
    lines.append("=" * 60)
    lines.append("【校验失败】")
    lines.append("=" * 60)

    if failed_count == 0:
        lines.append("无")
    else:
        for item in check_results:
            if item.ok:
                continue

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

    if failed_count == 0:
        lines.append("Dry Run 全部校验通过。可以执行完整修改包。")
    elif has_global_errors:
        lines.append("Dry Run 存在全局冲突。为避免互相踩踏，当前禁止执行任何 OP，请修正修改包后重新预演。")
    elif success_count > 0:
        lines.append("Dry Run 存在失败项。执行时将只允许执行校验成功的 OP，失败 OP 会被跳过。")
    else:
        lines.append("Dry Run 全部失败。没有可执行的 OP。")

    return "\n".join(lines)