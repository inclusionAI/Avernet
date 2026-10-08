"""Stable transport-neutral file count failures shared across boundaries."""

from contextvars import ContextVar

file_count_request_id: ContextVar[str] = ContextVar("file_count_request_id", default="")


def redact_fields(value):
    """Copy log data and recursively remove reusable credential fields."""
    # 以下为安全注释COSEC：嵌套字典/列表中的凭据按字段名递归脱敏，禁止打印原始异常。
    if isinstance(value, dict):
        sensitive = ("token", "authorization", "cookie", "password", "secret", "key", "credential", "session")
        return {
            name: "[REDACTED]" if any(part in str(name).lower() for part in sensitive) else redact_fields(item)
            for name, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_fields(item) for item in value]
    return value


class FileCountError(Exception):
    """A safe error identifier; never includes operating-system exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
