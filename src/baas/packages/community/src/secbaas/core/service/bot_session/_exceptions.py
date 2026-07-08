"""
Bot session error types.
"""

from secbaas.api import DomainError


class SessionError(DomainError):
    error_code = "SESSION_ERROR"
    http_status = 500

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


class SessionNotFoundError(SessionError):
    error_code = "SESSION_NOT_FOUND"
    http_status = 404

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class SessionClosedError(SessionError):
    error_code = "SESSION_CLOSED"
    http_status = 409

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        super().__init__(f"Session closed: {session_id}")
