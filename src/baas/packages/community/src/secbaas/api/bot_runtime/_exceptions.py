"""API-layer exceptions for file transfer operations.

Defines the BotServiceError base and TransferStateConflictError for the
packages tree (secbaas.* import convention).  These classes exist so the
repo-layer TransferStateConflictError can inherit from the API-layer
definition via diamond inheritance — otherwise dispatcher except clauses
catching the API class cannot handle repo-originated instances.
"""


class BotServiceError(Exception):
    """Base exception for bot service errors.

    All bot service API exceptions inherit from this class.
    """

    error_code: str = "BOT_SERVICE_ERROR"
    http_status: int = 400

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


class TransferStateConflictError(BotServiceError):
    """Raised when an invalid state transition is attempted on a file transfer.

    This is the API-layer definition imported by adapters (routers).
    The repo-layer equivalent inherits from this class so that except
    clauses catching the API class also handle repo-originated instances.
    """

    error_code: str = "TRANSFER_STATE_CONFLICT"
    http_status: int = 409

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)
