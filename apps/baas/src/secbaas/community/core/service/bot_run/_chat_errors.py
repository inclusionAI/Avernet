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


class NotConnectedError(Exception):
    """No usable transport connection is available for the operation."""


class RequestNotSentError(Exception):
    """请求帧未写出（连接在 write 前已关闭），可以安全重放一次。

    由 wire client 在 ``send()`` 入口发现连接已非 OPEN 时抛出：websockets
    在该状态下不会写出任何数据，对端没有收到请求。等待响应期间断链等
    结果未知的场景不得使用本异常，调用方不得据此透明重放。
    """
