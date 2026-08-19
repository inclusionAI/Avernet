"""Owner/collaborator authorization implementation for SkillSet commands."""

from __future__ import annotations

from injector import inject

from agentclaw.community.api.skill_set_access import SkillSetAccessProtocol
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import CollaboratorServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError


class SkillSetAccess(SkillSetAccessProtocol):
    """One authorization seam for human and delegated-App actors.

    The public admission layer resolves a delegated App to the user that
    granted it.  This hook then applies the exact same owner/collaborator ACL
    used for a direct human request, while retaining the true owner in the
    returned Bot projection for all persistence and runtime work.
    """

    @inject
    def __init__(
        self, bot_repo: BotRepository, collaborators: CollaboratorServiceProtocol
    ) -> None:
        self._bot_repo = bot_repo
        self._collaborators = collaborators

    def resolve_bot(self, *, bot_id: str, actor_id: str) -> dict:
        bot = self._bot_repo.get_by_id(bot_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        owner_id = str(bot.get("owner_id") or "")
        if not owner_id:
            raise LocalSkillNotFoundError()
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()
        return bot
