# -*- coding: utf-8 -*-

"""预演报告只表达本次检查事实，不把所有整包失败统称为冲突。"""

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
    单项检查、整包检查及成果准备具有不同含义。

    单项通过不代表整包取得执行资格；
    整包级失败可能来自互斥、成果准备或保护规划。
    具体原因直接消费检查结果，不从报告或异常文字重新分类。
    """
    ops = patch.get("operations", [])
    single_results = [
        check for check in check_results
        if check.op_type != "global_contract"
    ]
    package_results = [
        check for check in check_results
        if check.op_type == "global_contract"
    ]
    single_passed = [check for check in single_results if check.ok]
    single_failed = [check for check in single_results if not check.ok]

    lines = [
        "【Dry Run 预演结果】",
        f"协议版本：{PATCH_PROTOCOL_DESCRIPTION}",
        f"项目根目录：{root}",
        f"boundary：{patch.get('boundary') or '内部任务未提供 boundary'}",
        f"操作总数：{len(ops)}",
        f"单项校验通过：{len(single_passed)}",
        f"单项校验失败：{len(single_failed)}",
        f"全局校验：{'不通过' if has_global_errors else '通过'}",
        "全局校验范围：整包规则、文件成果准备及适用的保护规划。",
        f"最终可执行 OP：{success_count}",
    ]

    if not backup_enabled:
        lines.append("备份状态：未启用自动备份")
    elif backup_root is not None:
        lines.extend((
            f"计划备份目录：{backup_root}",
            "备份状态：尚未启动；执行时按需保护原状态。",
        ))
    elif failed_count:
        lines.append("备份状态：本次预演未通过，未启动本任务备份。")
    else:
        lines.append("备份状态：自动备份已开启，本次计划无需保护对象。")

    lines.extend((
        "",
        "=" * 60,
        "【单项校验通过】",
        "=" * 60,
    ))
    if not single_passed:
        lines.append("无")
    else:
        for check in single_passed:
            lines.append(
                f'[通过] id="{check.op_id}" {check.op_type} path="{check.path}"'
            )

    lines.extend((
        "",
        "=" * 60,
        "【单项校验失败】",
        "=" * 60,
    ))
    if not single_failed:
        lines.append("无")
    else:
        for check in single_failed:
            lines.extend((
                "",
                f'[失败] id="{check.op_id}" {check.op_type} path="{check.path}"',
                "",
                "失败原因：",
                check.message,
                "",
                "修改包定位：",
                "请在修改包中搜索：",
                check.locator,
                "-" * 60,
            ))

    lines.extend((
        "",
        "=" * 60,
        "【整包检查与准备】",
        "=" * 60,
    ))
    if has_global_errors:
        lines.append("不通过：整包检查或准备失败，整包禁止执行。")
        if single_passed:
            lines.append(
                "单项校验通过的操作也不会执行，不能只执行通过的部分。"
            )
        for check in package_results:
            lines.extend((
                "",
                "失败详情：",
                check.message,
            ))
            if check.locator:
                lines.extend((
                    "",
                    "相关 OP 定位：",
                    "请在修改包中搜索：",
                    check.locator,
                ))
            lines.append("-" * 60)
    else:
        lines.append("通过：本次整包检查与准备未报告失败。")

    lines.append("")
    if failed_count:
        lines.extend((
            "【本次结论】",
            "预演未通过，整包禁止执行。",
            "本次没有开始工作目标修改，也没有启动本任务的原状态保护。",
            "单项通过不表示可以部分执行。",
            "请根据上方具体原因修正修改包或排除故障，再重新预演。",
            "历史报告和配置保存重试不会使本次失败任务获得执行资格。",
        ))
    else:
        lines.extend((
            "【本次结论】",
            "预演通过，可以核对操作范围并确认执行。",
            "固定跳过的操作仍按计划跳过，不会写入未使用的内容。",
            "本次预演没有修改工作目标；执行时仍须通过安全及原字节复核。",
            "预演通过不保证后续备份或文件发布必然成功，不提供自动整包回滚。",
        ))

    return "\n".join(lines)
