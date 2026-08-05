"""Bot 领域异常定义

定义 Bot 服务相关的领域异常层次结构。
"""

from secbaas.community.api import DomainError

# ==============================================================================
# Bot Domain Exceptions
# ==============================================================================


class BotServiceError(DomainError):
    """Bot 服务基础异常

    所有 Bot 服务相关异常的基类。
    """

    error_code = "BOT_SERVICE_ERROR"
    http_status = 400

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


class SessionError(BotServiceError):
    """会话异常基类

    所有会话相关异常的基类。
    """

    error_code = "SESSION_ERROR"
    http_status = 400

    pass


class SessionNotFoundError(SessionError):
    """会话不存在异常

    当请求的会话不存在时抛出。
    """

    error_code = "SESSION_NOT_FOUND"
    http_status = 404

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class SessionClosedError(SessionError):
    """会话已关闭异常

    当向已关闭的会话发送消息时抛出。
    """

    error_code = "SESSION_CLOSED"
    http_status = 400

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        super().__init__(f"Session closed: {session_id}")


class BotNotFoundError(BotServiceError):
    error_code = "BOT_NOT_FOUND"
    http_status = 404

    def __init__(self, bot_id: str = "", bot_status: str | None = None):
        self.bot_id = bot_id
        self.bot_status = bot_status
        super().__init__(f"Bot not found: {bot_id}")


class BotNotAvailableError(BotServiceError):
    error_code = "BOT_NOT_AVAILABLE"
    http_status = 409

    def __init__(self, bot_id: str = "", status: str = ""):
        self.bot_id = bot_id
        self.status = status
        super().__init__(f"Bot not available: {bot_id}, status: {status}")


class NoDevicesFoundError(BotServiceError):
    error_code = "NO_DEVICES_FOUND"
    http_status = (
        503  # Retryable — overridden by _map_start_progress_error with Retry-After
    )

    def __init__(self, bot_uuid: str = ""):
        self.bot_uuid = bot_uuid
        super().__init__(f"No devices found for bot: {bot_uuid}")


class NoActiveDevicesError(BotServiceError):
    error_code = "NO_ACTIVE_DEVICES"
    http_status = 404

    def __init__(self, bot_uuid: str = ""):
        self.bot_uuid = bot_uuid
        super().__init__(f"No active devices found for bot: {bot_uuid}")


class BotBindingNotFoundError(BotServiceError):
    """Bot binding not found error."""

    error_code = "BOT_BINDING_NOT_FOUND"
    http_status = 404

    def __init__(self, bot_id: str = ""):
        self.bot_id = bot_id
        super().__init__(f"Bot binding not found: {bot_id}")


class TooManyRequestsError(BotServiceError):
    """任务并发数超限

    当 TaskConcurrencyPool 策略为 reject 且全局或 per-key
    信号量无可用槽位时抛出。
    """

    error_code = "TOO_MANY_REQUESTS"
    http_status = 429

    def __init__(self, bot_id: str = "", active: int = 0, limit: int = 0) -> None:
        self.bot_id = bot_id
        self.active = active
        self.limit = limit
        super().__init__(
            f"Too many concurrent tasks for bot_id={bot_id}: "
            f"active={active}, limit={limit}"
        )


class TransferNotTerminalError(BotServiceError):
    """Transfer is not in a terminal state.

    Raised when an operation requiring a terminal transfer (DONE/FAILED/CANCELLED)
    is attempted on a ticket that is still in progress.
    """

    error_code = "TRANSFER_NOT_TERMINAL"
    http_status = 409

    def __init__(self, transfer_id: str = "", status: str = ""):
        self.transfer_id = transfer_id
        self.status = status
        super().__init__(f"Transfer {transfer_id} is not in a terminal state: {status}")


class StagingObjectNotFoundError(BotServiceError):
    """Staging object not found at the staging path.

    Raised when complete upload detects no object at the staging path,
    indicating the caller has not finished uploading.
    """

    error_code = "STAGING_OBJECT_NOT_FOUND"
    http_status = 404

    def __init__(self, staging_path: str = ""):
        self.staging_path = staging_path
        super().__init__(f"Staging object not found: {staging_path}")


class DirectoryNotEmptyError(BotServiceError):
    """Staging directory is not empty.

    Raised when DELETE staging is called for a prefix that still contains objects.

    TODO(phase-future): Implement directory-not-empty check in delete staging
    flow to prevent accidental bulk deletion of non-empty prefixes.
    """

    error_code = "DIRECTORY_NOT_EMPTY"
    http_status = 409

    def __init__(self, key: str = ""):
        self.key = key
        super().__init__(f"Directory not empty: {key}")


class TransferStateConflictError(BotServiceError):
    """Raised when an invalid state transition is attempted on a file transfer.

    This is the API-layer definition imported by adapters (routers).
    The repo-layer equivalent inherits from this class so that except
    clauses catching the API class also handle repo-originated instances.
    """

    error_code = "TRANSFER_STATE_CONFLICT"
    http_status = 409

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)
