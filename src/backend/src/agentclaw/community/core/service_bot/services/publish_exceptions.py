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


class BotAlreadyServiceTypeError(BotPublishServiceError):
    """Bot 已经是服务型错误。"""


class BotTypeNotSupportedError(BotPublishServiceError):
    """Bot 类型不支持升级错误（如 aicoding 类型）。"""
