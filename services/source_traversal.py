# -*- coding: utf-8 -*-

"""扫描合并的元信息预扫描、名义路线选择和任务内缓存。

有选择能力的正式来源任务允许预先沿链接枚举目录元信息，
不读取普通文件正文。后台没有选择能力，仍在首个链接处停止。
全部预扫描结束后只决定一次；选择完成后不再发现或授权新范围。
"""

from dataclasses import dataclass
from pathlib import Path

from core.file_observation import ObjectIdentity
from core.link_paths import inspect_link_path, recheck_link_path
from core.recovery_paths import contains, identity, validate_component
from core.work_selection import validate_hierarchical_decisions
from services.source_enumeration import SourceEnumeration


class SourceAuthorizationRequired(ValueError):
    """后台检查或缺少选择能力，未进入待授权链接目标。"""


class SourceSelectionCancelled(ValueError):
    """取消整次来源任务，不转换为名单错误或局部跳过。"""


@dataclass(frozen=True)
class SourceLinkNode:
    definition: object
    nominal_path: Path
    parent_index: int | None = None


class SourceTraversal:
    """本次预扫描事实、名义路线及一次选择的唯一拥有者。

    实际目录名称由SourceEnumeration共享；不同名义路线不共享开关。
    链接定义按实际入口比较，节点按实际入口和名义断点共同定位。
    """

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
        self._definitions = {}
        self._nodes = []
        self._node_indices = {}
        self._current_route = None
        self._routes = {}
        self._accesses = {}
        self._lifecycle = "collecting"
        self.ignored = []

    @property
    def accesses(self):
        return tuple(self._accesses.values())

    @property
    def nodes(self):
        return tuple(self._nodes)

    def check(self):
        if self.check_cancel is not None:
            self.check_cancel()

    def _require_collecting(self):
        if self._lifecycle != "collecting":
            raise ValueError("来源范围已固定或任务已失效，禁止继续探查")

    def _decide(self, definition, nominal):
        """预扫描许可只允许取得元信息；最终正文许可在finalize形成。"""
        self._require_collecting()
        self.check()
        if self.selector is None and self.authorize is None:
            raise SourceAuthorizationRequired(
                f"待本次任务授权：{nominal}；原定义={definition.raw_target!r}"
            )
        actual_key = identity(definition.entry_path)
        original = self._definitions.setdefault(actual_key, definition)
        if original != definition:
            raise ValueError("来源链接在本次预扫描内变化，禁止接受新指向")
        key = (actual_key, identity(nominal))
        number = self._node_indices.get(key)
        route = self._current_route
        if route is None:
            # 明确文件列表的末端目录分类仍使用原探查能力；
            # 其父路线已由resolve固定，不允许分类过程新增授权节点。
            if number is None:
                raise ValueError("链接分类缺少本次已固定的名义路线")
            return True
        parent = route[-1] if route else None
        if number is None:
            number = len(self._nodes)
            self._node_indices[key] = number
            self._nodes.append(SourceLinkNode(definition, nominal, parent))
        elif self._nodes[number].parent_index != parent:
            raise ValueError("同一名义链接节点具有矛盾的父路线")
        route.append(number)
        return True

    def ignore(self, path, kind, reason):
        member = (str(path), kind, reason)
        if member not in self.ignored:
            self.ignored.append(member)

    def resolve(self, path):
        """固定名义来源；正式预扫描沿链接取得元信息，不打开普通正文。"""
        self._require_collecting()
        self.check()
        from core.link_paths import _nominal_path
        from services.source_recheck import recheck_source_paths

        nominal = _nominal_path(path)
        key = identity(nominal)
        previous = self._accesses.get(key)
        dependencies = tuple(
            observed for observed in self._accesses.values()
            if contains(observed.nominal_path, nominal)
        )
        recheck_source_paths(dependencies)
        if previous is not None:
            self._validate_output(previous)
            return previous
        if self._current_route is not None:
            raise RuntimeError("来源路径探查不得重入")
        route = []
        self._current_route = route
        try:
            access = inspect_link_path(
                nominal, follow_final_link=True, authorize_link=self._decide,
            )
        finally:
            self._current_route = None
        self.check()
        if access.kind == "absent":
            raise FileNotFoundError(
                f"来源或链接目标不存在：{access.nominal_path}"
            )
        if access.kind not in ("file", "directory"):
            raise ValueError(f"来源未定位到普通文件或文件夹：{access.nominal_path}")
        self._validate_output(access)
        accepted = self._accesses.setdefault(key, access)
        if accepted != access:
            raise ValueError("同一名义来源的固定路线或对象变化")
        self._routes[key] = tuple(route)
        return access

    def _recheck_preflight(self):
        """交互前后比较同一来源快照，不重新扫描、解析或改变基线。"""
        from services.source_recheck import recheck_source_paths

        self.check()
        recheck_source_paths(self.accesses)
        self.enumeration.recheck()
        self.check()

    def finalize(self):
        """预扫描结束后一次选择，裁剪路线及执行依赖。

        失败或取消永久消费本次资格。普通来源静默保留。
        返回保留名义位置集合，业务直接过滤原计划，不重新选文件。
        """
        self._require_collecting()
        completed = False
        self._lifecycle = "selecting"
        try:
            self._recheck_preflight()
            nodes = self.nodes
            if not nodes:
                decisions = ()
            elif self.selector is not None:
                decisions = self.selector(nodes)
            elif self.authorize is not None:
                lines = [
                    "本次已完成链接目标目录元信息预扫描，尚未读取普通文件正文。",
                    "以下是全部已发现的固定链接路线；许可不适用于未来任务。",
                ]
                for number, node in enumerate(nodes):
                    lines.extend((
                        f"{number + 1}. 名义节点：{node.nominal_path}",
                        f"   父链接编号：{None if node.parent_index is None else node.parent_index + 1}",
                        f"   实际入口：{node.definition.entry_path}",
                        f"   原定义：{node.definition.raw_target!r}",
                    ))
                accepted = self.authorize("确认本次全部来源链接", "\n".join(lines))
                if type(accepted) is not bool:
                    raise TypeError("来源整批授权必须返回明确布尔值")
                decisions = tuple(
                    "authorize" if accepted else "skip" for _ in nodes
                )
            else:
                raise SourceAuthorizationRequired("来源链接缺少本次选择能力")
            if decisions is None:
                raise SourceSelectionCancelled("来源链接选择已取消，整次任务停止")
            retained = set(validate_hierarchical_decisions(
                decisions, tuple(node.parent_index for node in nodes),
                description="来源链接树选择",
            ))
            # 用户交互已经跨越新边界，先验证展示快照，再裁剪。
            # 未选目标的目录预扫描已经发生，不将取消伪称为从未访问。
            self._recheck_preflight()
            for number, node in enumerate(nodes):
                if number in retained:
                    continue
                if node.parent_index is not None and node.parent_index not in retained:
                    continue
                self.ignore(
                    node.nominal_path,
                    "file" if node.definition.kind == "file_symlink" else "directory",
                    "链接路线未选",
                )
            self._accesses = {
                key: access for key, access in self._accesses.items()
                if all(number in retained for number in self._routes[key])
            }
            self._routes = {
                key: route for key, route in self._routes.items()
                if key in self._accesses
            }
            self._lifecycle = "selected"
            completed = True
            return frozenset(self._accesses)
        finally:
            if not completed:
                self._lifecycle = "failed"

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
            raise ValueError("输出位置包含来源实际文件夹")
        if (
            access.kind == "directory" and access.has_links
            and contains(access.actual_path, output.path)
        ):
            raise ValueError("输出位于链接实际来源文件夹内，请选择该范围之外的位置")
        for definition in access.followed_links:
            if contains(output.path, definition.entry_path):
                raise ValueError("输出会改变来源链接访问依赖")
        for directory in access.directories:
            if contains(output.path, directory.path):
                raise ValueError("输出会改变来源父文件夹身份")

    def children(self, access, *, skip_output=True):
        """复用已固定目录名称；不保存DirEntry或读取普通文件正文。"""
        self._require_collecting()
        if type(skip_output) is not bool:
            raise TypeError("来源输出文件处理政策必须是布尔值")
        self.check()
        recheck_link_path(access)
        if access.kind != "directory":
            raise ValueError("来源名称枚举要求普通文件夹访问")
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
        """排除分支不进入；实际目录祖先身份阻止递归循环。"""
        self._require_collecting()
        first = root if hasattr(root, "followed_links") else self.resolve(root)
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
                raise ValueError(f"来源文件夹递归循环：{access.nominal_path}")
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
                        "文件夹排除名单" if directory else "文件排除名单",
                    )
                    continue
                child = self.resolve(nominal)
                if not child.has_links and child.object_identity != ObjectIdentity(
                    attributes.st_dev, attributes.st_ino,
                ):
                    raise ValueError("来源名称枚举至对象定位期间身份变化")
                children.append((child, relative, next_ancestors, True))
            pending.extend(reversed(children))
        recheck_link_path(first)