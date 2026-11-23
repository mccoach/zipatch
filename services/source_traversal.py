# -*- coding: utf-8 -*-

"""扫描与合并共用的来源节点授权、名义遍历及任务内决定。

只读来源；不拥有发布、保护、拆分或入口改变权限。
路径解析复用正式链接核心，名称枚举复用任务枚举状态。
"""

from dataclasses import dataclass
from pathlib import Path

from core.file_observation import ObjectIdentity
from core.link_paths import (
    LinkAccessDenied, inspect_link_path, recheck_link_path,
)
from core.recovery_paths import contains, identity, validate_component
from core.work_selection import selected_indices
from services.source_enumeration import SourceEnumeration


class SourceAuthorizationRequired(ValueError):
    """后台检查遇到尚未授权的节点，不进入其目标。"""


class SourceSelectionCancelled(ValueError):
    """取消整次来源任务，不转换成某条名单的错误或局部跳过。"""


@dataclass(frozen=True)
class SourceLinkNode:
    definition: object
    nominal_path: Path


class SourceTraversal:
    """同次来源任务的唯一节点决定及固定访问事实拥有者。"""

    def __init__(
        self, *, output=None, selector=None, authorize=None,
        enumeration=None, check_cancel=None,
    ):
        for callback in (selector, authorize, check_cancel):
            if callback is not None and not callable(callback):
                raise TypeError("来源协调能力必须可调用")
        if enumeration is not None and not isinstance(enumeration, SourceEnumeration):
            raise TypeError("来源必须使用正式任务枚举状态")
        self.output = output
        self.selector = selector
        self.authorize = authorize
        self.enumeration = enumeration or SourceEnumeration()
        self.check_cancel = check_cancel
        self._decisions = {}
        self._accesses = {}
        self.ignored = []

    @property
    def accesses(self):
        return tuple(self._accesses.values())

    def check(self):
        if self.check_cancel is not None:
            self.check_cancel()

    def _decide(self, definition, nominal):
        self.check()
        key = identity(definition.entry_path)
        previous = self._decisions.get(key)
        if previous is not None:
            original, decision = previous
            if original != definition:
                raise ValueError("来源链接在本次任务内改变，禁止重新授权新指向")
            return decision
        node = SourceLinkNode(definition, nominal)
        if self.selector is not None:
            selected = self.selector((node,))
            if selected is None:
                raise SourceSelectionCancelled("来源链接选择已取消，整次任务停止")
            retained = selected_indices(
                selected, 1, description="来源链接节点选择",
            )
            decision = bool(retained)
        elif self.authorize is not None:
            description = (
                f"名义节点：{nominal}\n"
                f"实际链接入口：{definition.entry_path}\n"
                f"类型：{definition.kind}\n"
                f"原定义：{definition.raw_target!r}\n"
                f"直接引用：{definition.direct_target}\n\n"
                "选中允许沿此节点访问；目录可枚举普通后代，文件可作为正文来源。"
                "\n途中其他链接仍须分别授权；不修改来源或链接入口。"
            )
            decision = self.authorize("来源链接节点授权", description)
            if type(decision) is not bool:
                raise TypeError("来源节点授权必须返回明确布尔值")
        else:
            raise SourceAuthorizationRequired(
                f"待本次任务授权：{nominal}；原定义={definition.raw_target!r}"
            )
        self.check()
        self._decisions[key] = (definition, decision)
        return decision

    def ignore(self, path, kind, reason):
        member = (str(path), kind, reason)
        if member not in self.ignored:
            self.ignored.append(member)

    def resolve(self, path):
        """每个链接先决定再跟随；断点只记录入口，不保留下游依赖。"""
        self.check()
        # 先检查已固定的同名入口及名义祖先，再允许解析下一成员。
        # 不沿被替换的普通父目录或变化后的链接路线探查新对象。
        from core.link_paths import _nominal_path
        from services.source_recheck import recheck_source_paths

        nominal = _nominal_path(path)
        previous = self._accesses.get(identity(nominal))
        dependencies = tuple(
            observed for observed in self._accesses.values()
            if contains(observed.nominal_path, nominal)
        )
        recheck_source_paths(dependencies)
        if previous is not None:
            self._validate_output(previous)
            return previous
        try:
            access = inspect_link_path(
                nominal, follow_final_link=True, authorize_link=self._decide,
            )
        except LinkAccessDenied as error:
            kind = (
                "file" if error.definition.kind == "file_symlink"
                else "directory"
            )
            self.ignore(error.nominal_path, kind, "链接节点未授权")
            return None
        self.check()
        if access.kind == "absent":
            raise FileNotFoundError(
                f"来源或已授权链接目标不存在：{access.nominal_path}"
            )
        if access.kind not in ("file", "directory"):
            raise ValueError(f"来源未定位到既有普通文件或目录：{access.nominal_path}")
        self._validate_output(access)
        key = identity(access.nominal_path)
        previous = self._accesses.setdefault(key, access)
        if previous != access:
            raise ValueError("同一名义来源的固定路线或对象变化")
        return access

    def _validate_output(self, access):
        if self.output is None:
            return
        output = self.output
        if identity(access.actual_path) == identity(output.path):
            raise ValueError("来源链接或明确来源定位到本次输出文件")
        if (
            access.kind == "file" and output.kind == "file"
            and access.object_identity == output.object_identity
        ):
            raise ValueError("来源与输出共享硬链接对象，禁止覆盖")
        if access.kind == "directory" and contains(output.path, access.actual_path):
            raise ValueError("输出位置包含来源实际目录")
        if (
            access.kind == "directory" and access.has_links
            and contains(access.actual_path, output.path)
        ):
            raise ValueError("输出位于链接实际来源目录内，请选择该目标范围之外的位置")
        for definition in access.followed_links:
            if contains(output.path, definition.entry_path):
                raise ValueError("输出会改变来源链接访问依赖")
        for directory in access.directories:
            if contains(output.path, directory.path):
                raise ValueError("输出会改变来源父目录身份")

    def children(self, access, *, skip_output=True):
        """已授权普通目录的一次名称快照；返回名义成员和不跟随属性。"""
        if type(skip_output) is not bool:
            raise TypeError("来源输出文件处理政策必须是布尔值")
        self.check()
        recheck_link_path(access)
        if access.kind != "directory":
            raise ValueError("来源名称枚举要求普通目录访问")
        members = self.enumeration.entries(
            access.actual_path, check_cancel=self.check,
        )
        for number, (name, spelling, _) in enumerate(members):
            self.check()
            validate_component(name)
            actual = access.actual_path / name
            if (
                skip_output and self.output is not None
                and identity(actual) == identity(self.output.path)
            ):
                continue
            attributes = self.enumeration.attributes(members, number)
            yield access.nominal_path / name, attributes
        recheck_link_path(access)

    def walk(
        self, root, exclusions=None, *, include_root=False, skip_output=True,
    ):
        """名义路径保持不变；实际目录祖先身份负责阻止递归循环。"""
        first = root if hasattr(root, "followed_links") else self.resolve(root)
        if first is None:
            return
        pending = [(first, (), frozenset(), include_root)]
        while pending:
            self.check()
            access, parts, ancestors, emit = pending.pop()
            recheck_link_path(access)
            if access.kind == "file":
                if emit:
                    yield access, parts
                continue
            identifier = access.object_identity
            if identifier in ancestors:
                raise ValueError(f"来源目录递归循环：{access.nominal_path}")
            if emit:
                yield access, parts
            next_ancestors = ancestors | {identifier}
            children = []
            for nominal, attributes in self.children(access, skip_output=skip_output):
                relative = parts + (nominal.name,)
                import stat

                directory = (
                    stat.S_ISDIR(attributes.st_mode)
                    or bool(getattr(attributes, "st_file_attributes", 0) & 0x10)
                )
                if exclusions is not None and (
                    exclusions.directories.matches(relative) if directory
                    else exclusions.files.matches(relative)
                ):
                    self.ignore(
                        nominal, "directory" if directory else "file",
                        "排除目录名单" if directory else "排除文件名单",
                    )
                    continue
                child = self.resolve(nominal)
                if child is None:
                    continue
                # 名称属性属于不跟随入口，不能与最终目标身份直接比较。
                if not child.has_links and child.object_identity != ObjectIdentity(
                    attributes.st_dev, attributes.st_ino,
                ):
                    raise ValueError("来源名称枚举至对象定位期间身份变化")
                children.append((child, relative, next_ancestors, True))
            pending.extend(reversed(children))
        recheck_link_path(first)