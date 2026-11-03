"""Request rejection shared by chat orchestration and wire adapters."""


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
    ):
        super().__init__(message)
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable
