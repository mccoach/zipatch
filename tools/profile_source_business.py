# -*- coding: utf-8 -*-

"""隔离新素材上的正式扫描／合并阶段测量；不读取用户项目素材。

阶段含嵌套调用，耗时不可直接相加。
不提供旧版业务旁路，不将本次测量冒充优化前后对照。
"""

import json
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch


PROJECT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(PROJECT))

from core.constants import CODE_HEADER_LINE, CODE_FOOTER_LINE
from core.exclusion_rules import prepare_exclusions
from services import merge_service, scan_scope, scan_service, source_recheck
from services.source_enumeration import SourceEnumeration


def measured(operation, counters, name):
    def run(*arguments, **keywords):
        started = perf_counter()
        try:
            return operation(*arguments, **keywords)
        finally:
            count, seconds = counters.get(name, (0, 0.0))
            counters[name] = (count + 1, seconds + perf_counter() - started)
    return run


def main():
    if os.name != "nt":
        raise SystemExit("Windows only")
    with tempfile.TemporaryDirectory(prefix="zipatch_business_profile_") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        # 全部来源归本测量新建；输出不在来源范围内。
        for folder_number in range(5):
            folder = source / f"folder{folder_number}"
            folder.mkdir()
            for number in range(10):
                (folder / f"body{number}.py").write_bytes(
                    (b"print('profile')\n" * 100)
                )
                (folder / f"note{number}.txt").write_bytes(b"note\n" * 100)
        exclusions = prepare_exclusions("", "")
        for mode in ("scan", "directory", "combined"):
            counters = {}
            with ExitStack() as stack:
                for module, attribute, label in (
                    (scan_service, "prepare_scan_scope", "scan_scope_inclusive"),
                    (scan_scope, "recheck_scan_scope", "scan_recheck_inclusive"),
                    (merge_service, "prepare_demand_inspection", "list_prepare"),
                    (merge_service, "inspect_prepared_demand_rules", "list_match_inclusive"),
                    (merge_service, "build_regular_merge_plan", "directory_plan_inclusive"),
                    (merge_service, "read_text_content_for_merge", "body_read_decode"),
                    (source_recheck, "recheck_source_paths", "path_recheck"),
                    (merge_service, "publish_stream_output", "merge_publish_inclusive"),
                    (scan_service, "publish_stream_output", "scan_publish_inclusive"),
                ):
                    original = getattr(module, attribute)
                    stack.enter_context(patch.object(
                        module, attribute, new=measured(original, counters, label),
                    ))
                original_entries = SourceEnumeration.entries
                original_attributes = SourceEnumeration.attributes
                stack.enter_context(patch.object(
                    SourceEnumeration, "entries",
                    new=measured(original_entries, counters, "enumeration_request_inclusive"),
                ))
                stack.enter_context(patch.object(
                    SourceEnumeration, "attributes",
                    new=measured(original_attributes, counters, "entry_attribute_request"),
                ))
                started = perf_counter()
                if mode == "scan":
                    result = scan_service.panoramic_scan(
                        source, root / "scan.txt", exclusions, "", "",
                    )
                    output_count = result.file_count
                else:
                    result = merge_service.merge_files(
                        mode, str(source),
                        "**/*.py\n**/*.txt" if mode == "combined" else "",
                        str(root / (mode + ".txt")), exclusions, "", "",
                        CODE_HEADER_LINE, CODE_FOOTER_LINE,
                        record_file_mtime=False,
                    )
                    output_count = result.total_files
                elapsed = perf_counter() - started
            print("SOURCE_BUSINESS_PHASE " + json.dumps({
                "mode": mode,
                "new_fixture_files": 100,
                "output_entries": output_count,
                "total_seconds": round(elapsed, 6),
                "phases": {
                    name: {"calls": count, "inclusive_seconds": round(seconds, 6)}
                    for name, (count, seconds) in counters.items()
                },
                "limits": "nested phases; not additive; instrumentation overhead; no before-after claim",
            }, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())