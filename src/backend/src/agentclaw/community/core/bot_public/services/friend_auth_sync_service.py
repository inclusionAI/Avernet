"""Sync human→bot friend relationships to AceAgent on behalf of BCS.

Reuses ``resolve_agent_code`` (bot_management.utils) to resolve the bot's
agent_code internally, then delegates to ``AuthRelationshipPlugin``.
Router maps these exceptions to HTTP status; service stays HTTP-free.
"""
from __future__ import annotations


class FriendAuthSyncError(Exception):
    """Base class for friend-auth-sync service errors."""


class BotNotFoundError(FriendAuthSyncError):
    def __init__(self, bot_id: str) -> None:
        super().__init__(f"bot not found: {bot_id}")
        self.bot_id = bot_id


class AgentCodeUnavailableError(FriendAuthSyncError):
    def __init__(self, bot_id: str) -> None:
        super().__init__(f"agent_code unavailable for bot: {bot_id}")
        self.bot_id = bot_id


class AuthRelationshipSyncError(FriendAuthSyncError):
    """AceAgent call ultimately failed (after plugin-internal retries)."""
