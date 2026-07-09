"""Open API domain types — error codes and protocol-level value objects."""

from ._codes import OPEN_API_CODE_MESSAGES, OpenAPICode, get_code_message

__all__ = [
    "OPEN_API_CODE_MESSAGES",
    "OpenAPICode",
    "get_code_message",
]
