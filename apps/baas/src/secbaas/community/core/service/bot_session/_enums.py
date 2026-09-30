"""
Bot session status enumeration for baas_bot_session table.
"""

from enum import StrEnum


class SessionStatus(StrEnum):
    """Bot session status enumeration."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
