"""Read-only Bot-shared knowledge after native conversation admission."""

from collections.abc import Mapping

from agentclaw.community.core.bot_chat.human_bot_friendship_service_protocol import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.knowledge_management.ports import (
    KnowledgeCatalog,
    KnowledgeDisplaySource,
)
from agentclaw.community.core.knowledge_management.services.service import (
    page_size,
    require_id,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError


class ConversationKnowledgeService:
    def __init__(
        self,
        source: KnowledgeDisplaySource,
        catalog: KnowledgeCatalog,
        collaborators: CollaboratorServiceProtocol,
        friendships: HumanBotFriendshipServiceProtocol,
    ):
        self.source, self.catalog = source, catalog
        self.collaborators, self.friendships = collaborators, friendships

    def _authorize(self, bot, actor_id, request_headers):
        if actor_id == bot["owner_id"] or bot.get("public") == "1":
            return
        try:
            permission = self.collaborators.check_collaborator_permission(
                bot_id=bot["bot_id"],
                owner_id=bot["owner_id"],
                user_id=actor_id,
                required_level=PermissionLevel.MEMBER,
                env=bot["env"],
            )
            if permission.get("has_permission") is True:
                return
            if (
                self.friendships.is_friend(
                    human_id=actor_id,
                    bot_id=bot["bot_id"],
                    owner_id=bot["owner_id"],
                    request_headers=request_headers,
                )
                is True
            ):
                return
        except Exception:
            raise KnowledgeManagementError(
                "KNOWLEDGE_CHAT_ACCESS_UNAVAILABLE"
            ) from None
        raise KnowledgeManagementError("KNOWLEDGE_CHAT_ACCESS_DENIED")

    def list_for_bot(
        self,
        *,
        scope: BotKnowledgeScope,
        actor_id: str,
        request_headers: Mapping[str, str],
        cursor: int = 0,
        limit: int = 20,
    ) -> dict:
        page_size(limit, cursor)
        if limit > 20:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        require_id(actor_id)
        bot = self.source.conversation_bot(scope)
        self._authorize(bot, actor_id, request_headers)
        # Do not expose draft selections for published/service Bot conversations.
        if bot.get("bot_type") != "personal":
            raise KnowledgeManagementError("KNOWLEDGE_CONVERSATION_UNSUPPORTED")
        space_id = bot["space_id"]
        page = self.catalog.page_authorized(
            scope=scope,
            space_id=space_id,
            cursor=cursor,
            limit=limit,
        )
        saved = self.source.selections(bot=bot, kb_ids=page["kb_ids"])
        enabled = [
            kb_id
            for kb_id in page["kb_ids"]
            if saved.get(kb_id, {}).get("enabled") is True
        ]
        # Only the resolved Bot Owner supplies the provider context. A friend need
        # not hold a KB ACL; this does not confer direct asset or management access.
        facts = (
            self.catalog.details(
                space_id=space_id,
                kb_ids=enabled,
                actor_id=bot["owner_id"],
            )
            if enabled
            else []
        )
        if not isinstance(facts, list) or len(facts) != len(enabled):
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        items = []
        for kb_id, value in zip(enabled, facts, strict=True):
            if (
                not isinstance(value, dict)
                or value.get("kb_id") != kb_id
                or value.get("space_id") != str(space_id)
                or not isinstance(value.get("name"), str)
                or value.get("kgbase_status") not in {"ready", "creating", "failed"}
            ):
                raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
            if value["kgbase_status"] == "ready":
                items.append({"kb_id": kb_id, "name": value["name"]})
        # A filtered page may be empty but nonterminal: never walk more grant pages
        # or replace the provider's opaque cursor to fill it server-side.
        return {"items": items, "next_cursor": page["next_cursor"]}
