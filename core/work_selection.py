# -*- coding: utf-8 -*-

"""固定工作列表的选择结果校验；不读取磁盘或推导业务依赖。"""


def selected_indices(decision, count, *, description):
    if decision is None:
        raise ValueError(f"{description}已取消，未开始正文获取或工作修改")
    if (
        type(decision) is not tuple or len(decision) != count
        or any(
            type(value) is not str or value not in ("authorize", "skip")
            for value in decision
        )
    ):
        raise ValueError(
            f"{description}必须准确对应全部固定项的authorize/skip元组"
        )
    return tuple(
        number for number, value in enumerate(decision)
        if value == "authorize"
    )

def group_related_indices(keys_by_member):
    """共享任一实际关联键的成员共同决定；不读取磁盘或解释业务类型。"""
    keys_by_member = tuple(tuple(keys) for keys in keys_by_member)
    parents = list(range(len(keys_by_member)))
    owners = {}

    def find(number):
        while parents[number] != number:
            parents[number] = parents[parents[number]]
            number = parents[number]
        return number

    for number, keys in enumerate(keys_by_member):
        for key in keys:
            previous = owners.setdefault(key, number)
            left, right = find(number), find(previous)
            if left != right:
                parents[left] = right
    groups = {}
    for number in range(len(keys_by_member)):
        groups.setdefault(find(number), []).append(number)
    return tuple(tuple(numbers) for numbers in groups.values())


def project_group_decisions(
    groups, decision, count, *, fixed_skips=(), description,
):
    """共同组决定投影回固定成员；未展示成员保留，固定跳过不可勾回。"""
    if type(count) is not int or count < 0:
        raise ValueError("固定成员数量必须是非负整数")
    groups = tuple(tuple(numbers) for numbers in groups)
    fixed_skips = tuple(fixed_skips)
    seen = set()
    for numbers in groups:
        if not numbers:
            raise ValueError("共同决定组不得为空")
        for number in numbers:
            if (
                type(number) is not int or not 0 <= number < count
                or number in seen
            ):
                raise ValueError("共同决定成员重复或越出固定范围")
            seen.add(number)
    if any(
        type(number) is not int or not 0 <= number < count
        for number in fixed_skips
    ):
        raise ValueError("固定跳过成员越出固定范围")
    retained = set(selected_indices(
        decision, len(groups), description=description,
    ))
    result = ["authorize"] * count
    for group_number, numbers in enumerate(groups):
        value = "authorize" if group_number in retained else "skip"
        for number in numbers:
            result[number] = value
    for number in fixed_skips:
        result[number] = "skip"
    return tuple(result)

def validate_selection_parents(parents):
    """固定展示树按父先子后排列；只校验关系，不查询文件系统。"""
    parents = tuple(parents)
    for number, parent in enumerate(parents):
        if parent is not None and (
            type(parent) is not int or not 0 <= parent < number
        ):
            raise ValueError("选择树父节点必须是此前的唯一节点编号")
    return parents


def change_hierarchical_selection(states, parents, number, enabled):
    """父项切换覆盖子树；启用子项同时启用全部祖先。

    名义路线是选择关系，不根据实际目录或文件身份合并不同分支。
    """
    parents = validate_selection_parents(parents)
    states = tuple(states)
    if (
        len(states) != len(parents)
        or any(type(value) is not bool for value in states)
        or type(number) is not int or not 0 <= number < len(states)
        or type(enabled) is not bool
    ):
        raise ValueError("层级选择必须提供完整布尔状态及有效节点")
    result = list(states)
    descendants = {number}
    for index in range(number, len(parents)):
        if index == number or parents[index] in descendants:
            descendants.add(index)
            result[index] = enabled
    if enabled:
        parent = parents[number]
        while parent is not None:
            result[parent] = True
            parent = parents[parent]
    return tuple(result)


def validate_hierarchical_decisions(decisions, parents, *, description):
    """消费完整窗口决定；子项不得绕过未选父路线。"""
    parents = validate_selection_parents(parents)
    retained = selected_indices(decisions, len(parents), description=description)
    selected = set(retained)
    if any(
        parents[number] is not None and parents[number] not in selected
        for number in retained
    ):
        raise ValueError("所选子链接缺少父链接许可，禁止读取正文")
    return retained