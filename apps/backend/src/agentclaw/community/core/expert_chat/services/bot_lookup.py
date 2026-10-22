"""Bot lookup shared by Expert Chat and caller instance orchestration."""

from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError


class ExpertChatBotLookupMixin:
    def _find_chat_bot(self, bot_id: str, locator: str) -> dict | None:
        """Resolve a legacy address; caller access is checked separately."""
        try:
            return (
                BotAddressResolver(self._bot_repo)
                .resolve(bot_id=bot_id, legacy_owner_id=locator)
                .bot
            )
        except BotAddressNotFoundError:
            return None
