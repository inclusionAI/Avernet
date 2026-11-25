"""Recheck risk inputs and the original applicant's current permission."""
import json

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import resolve_explicit_permission_level


class RiskEligibility:
    def __init__(self, bots, collaborators, inputs):
        self.bots, self.collaborators, self.inputs = bots, collaborators, inputs

    def __call__(self, row) -> bool:
        bot = self.bots.get_by_pk(row.bot_pk)
        if not bot or str(bot.get("owner_id")) != row.owner_id or str(bot.get("env")) != row.env:
            return False
        level = resolve_explicit_permission_level(
            self.collaborators, bot=bot, user_id=row.operator_id, owner_id=row.owner_id, env=row.env
        )
        if level < PermissionLevel.ADMIN:
            return False
        _, digest = self.inputs.build(bot=bot, target=json.loads(row.target_json))
        return digest == row.input_hash
