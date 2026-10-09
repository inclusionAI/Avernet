"""Bot session lifecycle management service package."""

from ._enums import SessionStatus
from ._exceptions import SessionClosedError, SessionError, SessionNotFoundError
from ._models import BotSession, PaginatedResult
from ._protocols import SessionService
from ._session_service import DefaultSessionService

__all__ = [
    "BotSession",
    "DefaultSessionService",
    "PaginatedResult",
    "SessionClosedError",
    "SessionError",
    "SessionNotFoundError",
    "SessionService",
    "SessionStatus",
]
