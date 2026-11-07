# -*- coding: utf-8 -*-

"""恢复内容记录的唯一完整性算法；不读取文件系统。"""

import hashlib
import json


TREE_ALGORITHM = "sha256-tree-json-v1"


def content_integrity(kind, records):
    """消费已观察记录；文件时间不参与内容版本比较。"""
    if kind == "absent":
        return None
    if kind == "file":
        return {"algorithm": "sha256", "digest": records[0][2]}
    if kind != "directory":
        raise ValueError("未知恢复内容类型")

    digest = hashlib.sha256()
    for record in records:
        encoded = json.dumps(
            tuple(record), ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return {"algorithm": TREE_ALGORITHM, "digest": digest.hexdigest()}
