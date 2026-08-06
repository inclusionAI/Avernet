"""Exceptions raised by the service Bot publish domain.

Kept in a dependency-free module so publish service mixins can import the
exceptions without importing ``bot_publish_service`` back and creating a
circular reference for IDEs and static analyzers.
"""


class BotPublishServiceError(Exception):
    """Bot publish service error."""


class BotNotFoundError(BotPublishServiceError):
    """Bot 不存在错误。"""


class BotNotServiceTypeError(BotPublishServiceError):
    """Bot 不是服务型错误。"""


class PublishAlreadyExistsError(BotPublishServiceError):
    """发布单已存在错误。"""


class PublishNotFoundError(BotPublishServiceError):
    """发布单不存在错误。"""


class PublishStatusInvalidError(BotPublishServiceError):
    """发布单状态无效错误。"""


class PublishNotDeletableError(BotPublishServiceError):
    """发布单不满足删除条件错误（非草稿，或已有发布成功的版本）。

    A distinct type rather than the bare base: "you may not delete this yet" is
    a caller-visible refusal, while the base is also raised for genuine internal
    failures (missing dependency, persistence error). A surface that maps the
    two onto one status would report either an internal fault as the caller's
    mistake, or the caller's mistake as an internal fault.
    """


class BotAlreadyServiceTypeError(BotPublishServiceError):
    """Bot 已经是服务型错误。"""


class BotTypeNotSupportedError(BotPublishServiceError):
    """Bot 类型不支持升级错误（如 aicoding 类型）。"""
