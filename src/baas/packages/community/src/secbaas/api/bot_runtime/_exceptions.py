"""API-layer exceptions for file transfer operations.

Defines BotServiceError and domain exception classes for the
packages tree (secbaas.* import convention).
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


class BotNotFoundError(BotServiceError):
    error_code: str = "BOT_NOT_FOUND"
    http_status: int = 404

    def __init__(self, bot_id: str = "", bot_status: str | None = None) -> None:
        self.bot_id = bot_id
        self.bot_status = bot_status
        super().__init__(f"Bot not found: {bot_id}")


class NoDevicesFoundError(BotServiceError):
    error_code: str = "NO_DEVICES_FOUND"
    http_status: int = 503

    def __init__(self, bot_uuid: str = "") -> None:
        self.bot_uuid = bot_uuid
        super().__init__(f"No devices found for bot: {bot_uuid}")


class NoActiveDevicesError(BotServiceError):
    error_code: str = "NO_ACTIVE_DEVICES"
    http_status: int = 503

    def __init__(self, bot_uuid: str = "") -> None:
        self.bot_uuid = bot_uuid
        super().__init__(f"No active devices found for bot: {bot_uuid}")


class StagingObjectNotFoundError(BotServiceError):
    """Staging object not found at the staging path.

    Raised when complete upload detects no object at the staging path,
    indicating the caller has not finished uploading.
    """

    error_code: str = "STAGING_OBJECT_NOT_FOUND"
    http_status: int = 404

    def __init__(self, staging_path: str = "") -> None:
        self.staging_path = staging_path
        super().__init__(f"Staging object not found: {staging_path}")


class TransferNotTerminalError(BotServiceError):
    """Transfer is not in a terminal state.

    Raised when an operation requiring a terminal transfer (DONE/FAILED/CANCELLED)
    is attempted on a ticket that is still in progress.
    """

    error_code: str = "TRANSFER_NOT_TERMINAL"
    http_status: int = 409

    def __init__(self, transfer_id: str = "", status: str = "") -> None:
        self.transfer_id = transfer_id
        self.status = status
        super().__init__(f"Transfer {transfer_id} is not in a terminal state: {status}")


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
