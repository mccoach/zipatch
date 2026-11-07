# -*- coding: utf-8 -*-

"""扫描合并的固定目录、完整流式成果及唯一发布协调。

来源选择和正文生成归所属业务。生成错误自然传播，
目录和发布的明确失败保留正式结果；不自动重试或回滚。
"""

import sys
from dataclasses import dataclass


from core.content_publication import (
    ContentPublicationError, ContentPublicationResult,
    dispose_content_artifact, prepare_stream_content_artifact, publish_content,
)
from core.file_observation import FileObservation
from core.filesystem_changes import (
    FilesystemChangeError, execute_filesystem_change,
    prepare_directory_creation,
)


@dataclass(frozen=True)
class StreamOutputResult:
    expected: FileObservation
    directory_results: tuple
    publication: ContentPublicationResult | None
    phase: str
    error: OSError | ValueError | None = None

    @property
    def target(self):
        return self.expected.path

    @property
    def succeeded(self):
        return (
            self.error is None
            and all(result.succeeded for result in self.directory_results)
            and self.publication is not None
            and self.publication.succeeded
        )


class StreamOutputError(OSError):
    """消费所属输出的目录或发布结果，不从异常原因猜副作用。"""

    def __init__(self, result):
        self.result = result
        publication = result.publication
        lines = [
            f"成果输出未完整结束：{result.target}",
            f"流程阶段：{result.phase}",
        ]
        if publication is None:
            lines.extend((
                "尚未发起成果目标发布；正文未由发布流程写入正式目标。",
                "必要目录可能已有副作用，目录步骤与文件发布分别判断。",
            ))
        else:
            lines.extend((
                f"发布机制：{publication.mechanism}",
                f"发布阶段：{publication.phase}",
                "目标改变调用已发起："
                + ("是" if publication.mutation_attempted else "否"),
                f"名称替换状态：{publication.replacement}",
                "完整正文已确认："
                + ("是" if publication.content_completed else "否"),
                "本次确认已完成："
                + ("是" if publication.confirmation_completed else "否"),
                f"应急成果状态：{publication.emergency_state}",
            ))
            if publication.emergency_path is not None:
                lines.append(f"应急成果位置：{publication.emergency_path}")
            lines.append(
                "没有名称替换不代表没有原地改变；"
                "unknown不认定成功，也不证明旧目标未变。"
            )
        for directory in result.directory_results:
            lines.append(
                f"必要目录：{directory.target}；"
                f"改变调用已发起={'是' if directory.attempted else '否'}；"
                f"正常完成={'是' if directory.succeeded else '否'}"
            )
        lines.extend((
            "流程停止，不自动重试、回滚或删除工作目录。",
            str(result.error),
        ))
        super().__init__("\n".join(lines))


class StreamOutputPostError(OSError):
    """输出正常完成，附属记录失败仍持有正式业务结果。"""

    def __init__(self, message, business_result):
        self.business_result = business_result
        super().__init__(message)


def publish_stream_output(expected, chunks):
    """来源名单形成后调用；完整生成并关闭后才发布。

    生成、编码、安全拒绝及程序缺陷保留原异常类型和上下文。
    必要目录可能先行创建，准备失败不承诺整个流程零目录副作用。
    """
    if not isinstance(expected, FileObservation):
        raise TypeError("成果输出必须消费固定FileObservation")
    if expected.kind not in ("absent", "file"):
        raise ValueError("成果输出目标必须是不存在或普通文件")

    directories = []
    for index, (directory, kind) in enumerate(expected.chain[:-1]):
        if kind != "absent":
            continue
        absent = FileObservation(
            directory, expected.chain[:index + 1], "absent",
            None, None, None, None, None, None,
        )
        try:
            result = execute_filesystem_change(
                prepare_directory_creation(absent),
            )
        except FilesystemChangeError as error:
            directories.append(error.result)
            raise StreamOutputError(StreamOutputResult(
                expected, tuple(directories), None,
                "固定必要输出目录创建", error,
            )) from error
        directories.append(result)

    artifact = None
    try:
        artifact = prepare_stream_content_artifact(expected, chunks)
        try:
            publication = publish_content(artifact)
        except ContentPublicationError as error:
            raise StreamOutputError(StreamOutputResult(
                expected, tuple(directories), error.result,
                "完整成果正式发布", error,
            )) from error
        return StreamOutputResult(
            expected, tuple(directories), publication, "completed",
        )
    finally:
        if artifact is not None and artifact._lifecycle not in (
            "completed", "disposed",
        ):
            if not (
                artifact.mechanism == "in_place"
                and artifact._lifecycle == "attempted"
            ):
                try:
                    dispose_content_artifact(artifact)
                except (OSError, ValueError) as cleanup_error:
                    print(
                        f"[成果自有暂存清理失败] {artifact.path}：{cleanup_error}",
                        file=sys.stderr,
                    )
