# -*- coding: utf-8 -*-

"""固定文件成果的一次准备、发布、结果交付和暂存生命周期。

不解释业务输入，不创建必要父目录，不决定保护或回滚政策。
正式文件身份、内容和适用属性由发布核心核验并交付观察；
所属任务只提供明确范围复核、步骤登记及阶段展示能力。
"""

from core.content_publication import (
    ContentPublicationError, ContentPublicationOptions,
    cleanup_content_artifact, prepare_content_artifact,
    prepare_copy_content_artifact, publish_content,
)
from core.file_observation import FileObservation, recheck_file


class FixedContentExecution:
    """拥有本次文件成果执行资格、发布结果和失败暂存现场。"""

    def __init__(
        self, expected, content=None, *, source=None,
        options=ContentPublicationOptions(), preserve_target_mode=True,
        desired_digest=None,
    ):
        if not isinstance(expected, FileObservation):
            raise TypeError("固定文件执行必须消费FileObservation")
        if source is None:
            if not isinstance(content, bytes):
                raise TypeError("固定文件执行必须具有完整不可变bytes")
        elif not isinstance(source, FileObservation) or content is not None:
            raise TypeError("固定复制必须只提供正式来源观察")
        if not isinstance(options, ContentPublicationOptions):
            raise TypeError("固定文件执行必须具有明确发布选项")
        if type(preserve_target_mode) is not bool:
            raise TypeError("保留目标权限开关必须是布尔值")
        if desired_digest is not None and (
            type(desired_digest) is not str
            or len(desired_digest) != 64
            or any(character not in "0123456789abcdef" for character in desired_digest)
        ):
            raise ValueError("固定预期摘要必须是合法SHA-256")

        self.expected = expected
        self.content = content
        self.source = source
        self.options = options
        self.preserve_target_mode = preserve_target_mode
        self.desired_digest = desired_digest
        self.artifact = None
        self.publication = None
        self.observation = None
        self.current = expected.path
        self.phase = "prepared"
        self._lifecycle = "prepared"

    @property
    def consumed(self):
        return self._lifecycle != "prepared"

    def _stage(self, target, phase, stage):
        self.current, self.phase = target, phase
        stage(target, phase)

    def execute(self, *, recheck_context, record_step, stage):
        """一次消费固定成果；正常和失败发布事实均立即交给步骤拥有者。

        范围回调只比较原计划及任务正常变化，不重新授权现场。
        复制来源在发布后的新边界复核；工作正文仍消费固定来源bytes。
        程序缺陷自然传播，执行资格不因异常类型而恢复。
        """
        if self.consumed:
            raise ValueError("固定文件成果已消费，禁止重新执行")
        for callback in (recheck_context, record_step, stage):
            if not callable(callback):
                raise TypeError("固定文件执行必须提供明确协调回调")
        self._lifecycle = "attempted"
        completed = False
        try:
            self._stage(
                self.expected.path, "完整固定文件成果准备", stage,
            )
            self.artifact = (
                prepare_content_artifact(
                    self.expected, self.content, options=self.options,
                )
                if self.source is None else
                prepare_copy_content_artifact(
                    self.source, self.expected,
                    preserve_target_mode=self.preserve_target_mode,
                    options=self.options,
                )
            )
            if (
                self.desired_digest is not None
                and self.artifact.digest != self.desired_digest
            ):
                raise ValueError("完整文件成果不对应固定具体预期")

            self._stage(
                self.expected.path, "成果准备后所属固定范围复核", stage,
            )
            recheck_context()
            self._stage(
                self.expected.path, "固定完整文件成果发布", stage,
            )
            try:
                self.publication = publish_content(self.artifact)
            except ContentPublicationError as error:
                self.publication = error.result
                self.current, self.phase = error.result.target, error.result.phase
                record_step(error.result)
                stage(self.current, self.phase)
                raise
            # 后续来源或所属检查失败不能抹去已经正常结束的发布事实。
            record_step(self.publication)
            actual = self.publication.observation
            if not isinstance(actual, FileObservation):
                raise RuntimeError("正常内容发布缺少正式成果观察")
            self.observation = actual

            if self.source is not None:
                self._stage(
                    self.source.path, "发布后固定复制来源复核", stage,
                )
                recheck_file(self.source, check_mtime=True)

            completed = True
            self._lifecycle = "completed"
            self._stage(self.expected.path, "completed", stage)
            return actual
        finally:
            cleanup_content_artifact(self.artifact)
            # 任务已经结束，不额外永久持有输入bytes；
            # artifact和来源观察可能仍由诊断或其他拥有者引用。
            self.content = None
            self.source = None
            if not completed:
                self._lifecycle = "failed"
