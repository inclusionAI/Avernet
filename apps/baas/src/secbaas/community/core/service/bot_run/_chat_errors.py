"""Request rejection shared by chat orchestration and wire adapters."""

from secbaas.community.api.bot_runtime import BotServiceError


class BotSessionError(Exception):
    """The current chat attempt finished in an error state."""

    def __init__(self, message: str, *, ws_error_code: str | None = None) -> None:
        super().__init__(message)
        self.ws_error_code = ws_error_code


class ChatRequestError(Exception):
    """chat.send / chat.inject 响应 ok=False 时抛出。

    当服务器返回的响应中 ok 字段为 False 时（例如会话验证失败、服务不可用），
    由 wire client 或公共编排层抛出此异常，避免静默失败。

    Attributes:
        error_code: 服务器返回的错误码（如 "UNAVAILABLE"），可能为 None
        error_message: 服务器返回的错误信息，可能为 None
        retryable: 服务器指示是否可重试，可能为 None
    """

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool | None = None,
        *,
        ws_error_code: str | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable
        self.ws_error_code = ws_error_code


class NotConnectedError(Exception):
    """No usable transport connection is available for the operation."""


def exception_ws_error_code(error: BaseException) -> str | None:
    """Unwrap only the standard BotService wrapper, not retry/transport chains."""
    if isinstance(error, BotServiceError) and error.__cause__ is not None:
        error = error.__cause__
    if isinstance(error, (ChatRequestError, BotSessionError)):
        return error.ws_error_code
    return None
