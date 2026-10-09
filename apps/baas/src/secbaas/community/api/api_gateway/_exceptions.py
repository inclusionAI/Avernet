"""API Gateway exception definitions."""


class APIKeyError(Exception):
    """API Key 业务异常"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
