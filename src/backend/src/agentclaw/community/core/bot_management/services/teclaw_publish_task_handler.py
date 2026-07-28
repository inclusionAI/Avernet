from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    Retry,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.repository.protocol import (
        DeviceBindingRepository,
    )
    from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    from agentclaw.community.plugin_api.passport import PassportPlugin

TECLAW_CREATE_PUBLISH_POLL_TASK = "teclaw.create.publish_poll"
TECLAW_PUBLISH_TASK_DEADLINE_SECONDS = 86400
_PUBLISH_POLL_TIMEOUT_SECONDS = 600.0
_PUBLISH_PROGRESS_TRANSIENT_ERROR = "get_publish_progress transient error"

logger = get_logger()


def build_teclaw_publish_poll_payload(
    *,
    binding_id: int,
    bot_id: str,
    owner_id: str,
    publish_id: int,
    started_at_epoch_s: float,
) -> dict:
    return {
        "binding_id": binding_id,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "publish_id": publish_id,
        "started_at_epoch_s": started_at_epoch_s,
    }


def map_publish_status(publish_status: str | None) -> str:
    normalized = (publish_status or "").strip().upper()
    if normalized == "SUCCESS":
        return DeviceBindingStatus.ACTIVE.value
    if normalized in {"FAILED", "REJECTED", "REVOKED"}:
        return DeviceBindingStatus.FAILED.value
    return DeviceBindingStatus.PENDING.value


def _require_int(payload: dict | None, key: str) -> int:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"field {key} must be int")
    return value


def _require_str(payload: dict | None, key: str) -> str:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"field {key} must be str")
    return value


def _require_number(payload: dict | None, key: str) -> int | float:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"field {key} must be int or float")
    return value


class TeclawPublishTaskHandler:
    def __init__(
        self,
        *,
        baas_service: BaasService,
        device_binding_repo: DeviceBindingRepository,
        passport_plugin: PassportPlugin,
        poll_delay_seconds: float = 5.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._baas = baas_service
        self._device_binding_repo = device_binding_repo
        self._passport_plugin = passport_plugin
        self._poll_delay_seconds = poll_delay_seconds
        self._clock = clock

    @property
    def task_type(self) -> str:
        return TECLAW_CREATE_PUBLISH_POLL_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        try:
            binding_id = _require_int(payload, "binding_id")
            bot_id = _require_str(payload, "bot_id")
            owner_id = _require_str(payload, "owner_id")
            publish_id = _require_int(payload, "publish_id")
            started_at_epoch_s = _require_number(payload, "started_at_epoch_s")
        except ValueError as exc:
            return Fail(f"invalid payload: {exc}")

        try:
            binding = self._device_binding_repo.get_by_id(binding_id)
        except Exception as exc:
            return Retry(f"load Teclaw binding failed: {exc}")

        if binding is None or binding.status in {
            DeviceBindingStatus.ACTIVE.value,
            DeviceBindingStatus.FAILED.value,
            DeviceBindingStatus.RELEASED.value,
        }:
            return Complete()
        if binding.device_provider != TECLAW_DEVICE_PROVIDER:
            return Complete()
        current_publish_id = (binding.device_props or {}).get("publish_id")
        if current_publish_id is None or str(current_publish_id) != str(publish_id):
            return Complete()

        try:
            progress = self._baas.get_publish_progress(publish_id)
        except Exception as exc:
            logger.warning(
                "[TeclawPublishTaskHandler] publish query failed: "
                "publish_id=%s error=%s",
                publish_id,
                exc,
            )
            return Retry(_PUBLISH_PROGRESS_TRANSIENT_ERROR)

        status = map_publish_status((progress or {}).get("status"))
        if status in {
            DeviceBindingStatus.ACTIVE.value,
            DeviceBindingStatus.FAILED.value,
        }:
            return self._persist_terminal(
                bot_id=bot_id,
                owner_id=owner_id,
                binding_id=binding_id,
                publish_id=publish_id,
                status=status,
                binding=binding,
            )
        if (self._clock() - started_at_epoch_s) >= _PUBLISH_POLL_TIMEOUT_SECONDS:
            return Complete()
        return Reschedule(self._poll_delay_seconds)

    def _persist_terminal(
        self,
        *,
        bot_id: str,
        owner_id: str,
        binding_id: int,
        publish_id: int,
        status: str,
        binding: DeviceBindingRecord,
    ) -> TaskOutcome:
        try:
            applied = self._device_binding_repo.transition_teclaw_publish_terminal(
                binding_id=binding_id,
                bot_id=bot_id,
                owner_id=owner_id,
                publish_id=publish_id,
                status=status,
            )
        except Exception as exc:
            return Retry(f"persist Teclaw terminal status failed: {exc}")

        if applied and status == DeviceBindingStatus.ACTIVE.value:
            self._push_outbound_rule(bot_id=bot_id, binding=binding)

        return Complete()

    def _push_outbound_rule(
        self, *, bot_id: str, binding: DeviceBindingRecord
    ) -> None:
        """Deliver the bot's passport token to the just-started teclaw container.

        The container's PaaS device only exists once BaaS has executed the create
        publish (``start_device`` is what fills ``provider_device_id``), so this
        is the earliest point at which BaaS can answer with a device to write the
        outbound rule onto. Under all-auto approval (#197) the create response and
        the (now-ignored) client approve both return before that happens, which is
        why provision cannot push the rule inline.

        Best-effort, mirroring the publish path's poll-success refresh
        (``BotBuildService.refresh_teclaw_mcp_outbound_rule``): a failure is logged
        and never fails the task — the binding status is already persisted.
        """
        bot_uuid = binding.device_id
        owner_id = binding.entity_id
        if not bot_uuid or not owner_id:
            logger.warning(
                "[TeclawPublishTaskHandler] outbound rule skipped, missing context: "
                "bot_id=%s bot_uuid=%s owner_id=%s",
                bot_id,
                bot_uuid,
                owner_id,
            )
            return

        try:
            agent_pass_token = self._passport_plugin.query_token(bot_id, owner_id) or ""
        except Exception as exc:
            logger.warning(
                "[TeclawPublishTaskHandler] queryToken failed: "
                "bot_id=%s owner_id=%s error=%s",
                bot_id,
                owner_id,
                exc,
            )
            return

        if not agent_pass_token:
            logger.warning(
                "[TeclawPublishTaskHandler] queryToken empty: bot_id=%s owner_id=%s",
                bot_id,
                owner_id,
            )
            return

        try:
            updated = self._baas.update_teclaw_outbound_rule_by_bot_uuid(
                bot_uuid,
                agent_pass_token=agent_pass_token,
            )
            logger.info(
                "[TeclawPublishTaskHandler] Teclaw outbound rule updated: "
                "bot_id=%s bot_uuid=%s updated_count=%s",
                bot_id,
                bot_uuid,
                len(updated or []),
            )
        except Exception as exc:
            logger.warning(
                "[TeclawPublishTaskHandler] Teclaw outbound rule update failed: "
                "bot_id=%s bot_uuid=%s error=%s",
                bot_id,
                bot_uuid,
                exc,
            )


class TeclawPublishTaskLifecycle(LifecycleBase):
    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        baas_service: BaasService,
        device_binding_repo: DeviceBindingRepository,
        passport_plugin: PassportPlugin,
    ) -> None:
        self._registry = registry
        self._baas_service = baas_service
        self._device_binding_repo = device_binding_repo
        self._passport_plugin = passport_plugin

    async def bootstrap(self) -> None:
        self._registry.register(
            TeclawPublishTaskHandler(
                baas_service=self._baas_service,
                device_binding_repo=self._device_binding_repo,
                passport_plugin=self._passport_plugin,
            )
        )
