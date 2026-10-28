"""Published-only stage policy for Human-to-Bot friend conversations."""

from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.engine_runtime.stage import published_stage_binding, require_known_stage
from agentclaw.community.core.expert_chat.errors import BotNotFoundError
from agentclaw.community.utils.env_utils import get_current_env


class ExpertChatStageMixin:
    def _online_chat_binding(self, bot: dict) -> int:
        env = get_current_env()
        records = self._publish_repository.list_by_source_bot(int(bot["id"]), env)
        return published_stage_binding(
            records, bot_id=bot["bot_id"], stage="online", env=env,
            binding_is_active=lambda _: False,
        )

    def resolve_chat_stage(self, bot_id: str, owner_id: str, stage: str) -> str:
        """Called only after friend authorization; never grant operator access.

        Legacy draft friend requests still mean published service chat, not
        draft editing. Personal Bots retain their sole workspace runtime.
        """
        require_known_stage(stage)
        bot = self._find_chat_bot(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError("friend chat bot not found")
        if bot.get("bot_type") == "service":
            if stage == "verify":
                raise EngineStageNotLiveError("friend chat cannot address verify runtimes")
            self._online_chat_binding(bot)
            return "online"
        if stage != "draft":
            raise EngineStageNotLiveError("personal friend Bot has only its draft runtime")
        return "draft"
