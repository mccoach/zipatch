# -*- coding: utf-8 -*-

"""修改包预演后的整包执行授权；不裁剪OP、不读取或扫描现场。"""

from ui.source_scope import select_source_tree


LINK_GROUPS = (
    ("file_hardlink", "文件硬链接"),
    ("file_symlink", "文件软链接"),
    ("directory_symlink", "文件夹软链接"),
    ("junction", "文件夹联接（Junction）"),
)


def make_patch_scope_authorizer(parent, commits):
    def authorize(nodes, root):
        nodes = tuple(nodes)
        if not nodes:
            return True
        decision = select_source_tree(
            parent, commits, nodes, selectable=False,
            title="修改包整包链接执行授权",
            introduction=(
                f"项目根文件夹：{root}\n"
                "完整预演已经通过，必要正文已在预演中读取。\n"
                "下面列出本包涉及的链接对象；不提供逐项勾选，不裁剪OP。\n"
                "授权整包后仍复核固定现场、完成全部必要备份，再开始工作。\n"
                "拒绝、关闭或Esc立即停止整包，不备份、不执行任何工作修改。\n"
                "硬链接正文写回共同影响其他名称；目录树操作不执行子集。"
                "未搜索范围外的关联名称或反向引用。"
            ),
        )
        return decision is not None and all(
            value == "authorize" for value in decision
        )
    return authorize