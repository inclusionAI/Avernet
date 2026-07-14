"""BCN 下行协议异常类型

每个异常类自带 ``retryable`` 属性，方便 HTTP 层直接映射 BCN 错误响应，
无需 isinstance 分支判断。

异常继承体系::

    DomainError
      └─ BcnError           (retryable 由 http_status 推断: ≥500 → True)
           ├─ BcnProviderIdMismatchError   (403, retryable=False)
           ├─ BcnIdempotencyConflictError  (409, retryable=False)
           ├─ BcnSessionNotFoundError      (404, retryable=False)
           ├─ BcnBotNotFoundError          (404, retryable=False)
           ├─ BcnInvalidRequestError       (400, retryable=False)
           └─ BcnUnsupportedMethodError    (501, retryable=False)
"""

from secbaas.community.api import DomainError


class BcnError(DomainError):
    """BCN 下行协议基础异常

    ``retryable`` 默认规则：5xx → True，其余 → False。
    子类可覆盖 ``retryable`` 类属性来自定义。
    """

    error_code = "BCN_ERROR"
    http_status = 500
    retryable: bool = True  # 5xx 默认可重试

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


class BcnProviderIdMismatchError(BcnError):
    error_code = "provider_id_mismatch"
    http_status = 403
    retryable = False

    def __init__(self, expected: str = "", actual: str = ""):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Provider ID mismatch: expected={expected}, actual={actual}")


class BcnIdempotencyConflictError(BcnError):
    error_code = "conflict"
    http_status = 409
    retryable = False

    def __init__(self, id: str = ""):
        self.id = id
        super().__init__(f"Idempotency key conflict: {id}")


class BcnSessionNotFoundError(BcnError):
    error_code = "session_not_found"
    http_status = 404
    retryable = False

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class BcnBotNotFoundError(BcnError):
    error_code = "bot_not_found"
    http_status = 404
    retryable = False

    def __init__(self, provider_bot_ref: str = ""):
        self.provider_bot_ref = provider_bot_ref
        super().__init__(f"Bot not found: {provider_bot_ref}")


class BcnInvalidRequestError(BcnError):
    error_code = "invalid_request"
    http_status = 400
    retryable = False

    def __init__(self, message: str = ""):
        super().__init__(message)


class BcnUnsupportedMethodError(BcnError):
    error_code = "unsupported_method"
    http_status = 501
    retryable = False

    def __init__(self, method: str = ""):
        self.method = method
        super().__init__(f"Unsupported method: {method}")


class BcnUnauthorizedError(BcnError):
    """BCN 下行认证失败"""

    error_code = "unauthorized"
    http_status = 401
    retryable = False

    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message)
