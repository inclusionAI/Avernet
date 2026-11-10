"""Read-only checks before a new handoff freezes the Bot or issues credentials."""

from typing import Any

from injector import inject

from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class OwnerHandoffPreflight:
    @inject
    def __init__(
        self,
        passport: PassportPlugin,
        bindings: DeviceBindingRepository,
        publishes: BotPublishRepositoryProtocol,
    ):
        self._passport, self._bindings, self._publishes = passport, bindings, publishes

    def check(self, bot: dict[str, Any]) -> None:
        stage = "passport_capability"
        try:
            address = {
                "bot_id": bot["bot_id"],
                "owner_workno": bot["owner_id"],
                "entity_id": bot["entity_id"],
            }
            if not self._passport.owner_handoff_supported(**address):
                raise HandoffError("PASSPORT_HANDOFF_UNSUPPORTED")
            stage = "passport_snapshot"
            snapshot = self._passport.query_agent_passport(**address) or {}
            if not snapshot.get("credential_id"):
                snapshot = self._passport.query_handoff_passport(**address) or {}
            if not snapshot.get("credential_id"):
                raise HandoffError("CURRENT_PASSPORT_REQUIRED")
            stage = "runtime_bindings"
            self._check_runtime_bindings(bot)
        except HandoffError:
            raise
        except Exception as exc:
            # RPC and storage exceptions can contain credentials. Never expose
            # their message or mistake unavailable checks for a successful plan.
            logger.warning(
                "owner_handoff_preflight_failed bot_pk=%s stage=%s error_type=%s",
                bot["id"], stage, type(exc).__name__,
            )
            raise HandoffError("HANDOFF_PREFLIGHT_UNAVAILABLE") from None

    def _check_runtime_bindings(self, bot: dict[str, Any]) -> None:
        # Mirror the updater's target selection: draft + SUCCESS/online +
        # VALIDATING/verify + ACTIVE caller instances. Never provision, reconnect,
        # refresh a token, or probe by writing headers during preflight.
        env = get_current_env()
        binding_ids = set()
        if bot.get("binding_id"):
            binding_ids.add(bot["binding_id"])
        for status, stage in (
            (PublishStatus.SUCCESS.value, "online"),
            (PublishStatus.VALIDATING.value, "verify"),
        ):
            record = self._publishes.get_by_publish_bot_id(
                publish_bot_id=bot["bot_id"], owner_id=bot["entity_id"],
                env=env, publish_status=status,
            )
            if record is not None:
                binding_id = ((record.ext or {}).get("binding") or {}).get(stage)
                if not binding_id:
                    raise HandoffError("HANDOFF_RUNTIME_TARGET_INVALID")
                binding_ids.add(binding_id)
        callers = self._bindings.list_active_caller_instance_bindings(
            bot_id=bot["bot_id"], owner_id=bot["entity_id"], env=env,
        )
        for binding in callers:
            self._check_binding(binding, env)
        for binding_id in binding_ids:
            binding = self._bindings.get_by_id(binding_id)
            self._check_binding(binding, env)
        if not binding_ids and not callers:
            raise HandoffError("HANDOFF_RUNTIME_TARGET_REQUIRED")

    @staticmethod
    def _check_binding(binding, env):
        if (
            binding is None or not binding.device_id or not binding.device_provider
            or binding.env != env or binding.status != "ACTIVE"
        ):
            raise HandoffError("HANDOFF_RUNTIME_TARGET_INVALID")
