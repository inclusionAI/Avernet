"""Administrative correction of the installed-expiry ledger, without token installation."""
from __future__ import annotations

from datetime import UTC, datetime
from agentclaw.community.core.caller_identity.contracts import CallerExpiryCorrectionError
from agentclaw.community.core.caller_identity.protocols import CallerExpiryCorrectionServiceProtocol

from agentclaw.community.core.access import admin_scopes
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.chat import ExpertChatInstanceRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class CallerExpiryCorrectionService(CallerExpiryCorrectionServiceProtocol):
    def __init__(self, *, bots: BotRepository, bindings: DeviceBindingRepository,
                 instances: ExpertChatInstanceRepository):
        self._bots, self._bindings, self._instances = bots, bindings, instances

    def correct(self, *, actor_id: str, bot_id: str, entity_id: str, caller_user_id: str,
                binding_id: int, expires_at: datetime, expected_expires_at: datetime | None,
                reason: str) -> dict:
        # COSEC: administrative authority belongs to the verified actor, never the target Caller.
        if actor_id not in admin_scopes.super_admin():
            raise CallerExpiryCorrectionError("caller_expiry_forbidden")
        bot = self._bots.get_by_id_and_entity(bot_id, entity_id)
        if not bot:
            raise CallerExpiryCorrectionError("caller_expiry_not_found")
        if (bot.get("bot_type") != "service" or bot.get("call_type") != "caller"
                or bot.get("active_engine") == "teclaw"):
            raise CallerExpiryCorrectionError("caller_expiry_unsupported")
        binding = self._bindings.get_by_id(binding_id)
        # COSEC: a correction names only one existing, active dedicated Caller in this environment.
        if (binding is None or binding.status != "ACTIVE" or binding.env != get_current_env()
                or binding.entity_id != entity_id or binding.applied_by != caller_user_id
                or binding.entity_type != "staff" or binding.apply_reason != f"caller_instance:{bot_id}"
                or binding.device_provider not in {"arca", "baas"}):
            raise CallerExpiryCorrectionError("caller_expiry_binding_changed")
        corrected = self._instances.correct_caller_token_expiry(
            user_id=caller_user_id, bot_id=bot_id, owner_id=entity_id,
            binding_id=binding_id, expires_at=expires_at, expected_expires_at=expected_expires_at,
        )
        result = {"bot_id": bot_id, "entity_id": entity_id, "caller_user_id": caller_user_id,
                  "binding_id": binding_id, **corrected,
                  "expires_at": expires_at.astimezone(UTC).isoformat()}
        logger.info("caller_expiry_corrected actor_id=%s result=%s reason=%s", actor_id, result, reason)
        return result
