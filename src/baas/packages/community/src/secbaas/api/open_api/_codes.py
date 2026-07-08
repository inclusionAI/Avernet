"""Open API error codes.

Error code ranges (modeled after HTTP status):
- 0       : Success (HTTP 200)
- 40xxx   : Client parameter error (HTTP 400)
- 401xx   : Authentication failure (HTTP 401)
- 403xx   : Authorization failure (HTTP 403)
- 50xxx   : Server internal error (HTTP 500)
- 60xxx   : Business-defined error (HTTP 4xx/5xx)
"""


class OpenAPICode:
    """Error code constants."""

    SUCCESS = 0
    INVALID_PARAM = 40001
    PARAM_MISSING = 40002
    TOKEN_MISSING = 40101
    TOKEN_EXPIRED = 40102
    TOKEN_INVALID = 40103
    FORBIDDEN = 40301
    INTERNAL_ERROR = 50001
    BUSINESS_ERROR = 60001
    RESULT_NOT_READY = 60101


OPEN_API_CODE_MESSAGES: dict[int, str] = {
    0: "Success",
    40001: "Invalid parameter",
    40002: "Missing parameter",
    40101: "Token missing",
    40102: "Token expired",
    40103: "Token invalid",
    40301: "Forbidden",
    50001: "Internal error",
    60001: "Business error",
    60101: "Result not ready",
}


def get_code_message(code: int) -> str:
    """Get the message for an error code."""
    return OPEN_API_CODE_MESSAGES.get(code, "Unknown error")
