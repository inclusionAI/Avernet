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


from typing import Any

from injector import inject

from agentclaw.community.core.bot_management.utils import resolve_agent_code
from agentclaw.community.core.repository.protocols.bot.bot import BotRepository
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin

_HUMAN_PREFIX = "human_"
_GRANT_DESCRIPTION = "Human-bot friend authorized by BCS edge grant"


def _strip_human(work_no: str) -> str:
    return work_no[len(_HUMAN_PREFIX):] if work_no.startswith(_HUMAN_PREFIX) else work_no


class FriendAuthSyncService:
    """Implements FriendAuthSyncServiceProtocol; agent_code resolved internally."""

    @inject
    def __init__(
        self,
        bot_repo: BotRepository,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
    ) -> None:
        self._bot_repo = bot_repo
        self._passport_plugin = passport_plugin
        self._auth_rel = auth_relationship_plugin

    def sync(
        self,
        *,
        bot_id: str,
        owner_work_no: str,
        human_work_no: str,
        action: str,
    ) -> dict[str, Any]:
        owner_work_no = _strip_human(owner_work_no)
        human_work_no = _strip_human(human_work_no)

        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_work_no)
        if not bot:
            raise BotNotFoundError(bot_id)

        agent_code = resolve_agent_code(bot=bot, passport_plugin=self._passport_plugin)
        if not agent_code:
            raise AgentCodeUnavailableError(bot_id)

        owner_name = bot.get("owner_name") or owner_work_no

        if action == "grant":
            result = self._auth_rel.create_relationship(
                work_no=human_work_no,
                agent_code=agent_code,
                operator_work_no=owner_work_no,
                operator_name=owner_name,
                description=_GRANT_DESCRIPTION,
            )
            if not result:
                raise AuthRelationshipSyncError("create_relationship returned None")
            if result.get("already_exists"):
                return {"synced": True, "reason": "already_exists", "auth_id": None}
            return {"synced": True, "reason": "created", "auth_id": result.get("auth_id")}

        if action == "revoke":
            rels = self._auth_rel.query_relationships(
                agent_code=agent_code, work_no=human_work_no
            )
            deleted = False
            for rel in rels:
                auth_id = rel.get("auth_id") or rel.get("authId")
                if auth_id is not None:
                    if not self._auth_rel.delete_relationship(int(auth_id)):
                        raise AuthRelationshipSyncError("delete_relationship returned False")
                    deleted = True
            return {
                "synced": True,
                "reason": "deleted" if deleted else "not_found",
                "auth_id": None,
            }

        raise FriendAuthSyncError(f"unknown action: {action}")
