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
# How long a "device not ready" wait stays the *expected* path after the publish
# reported SUCCESS. Measured from the same create timestamp as the poll window.
# Past it the delivery keeps retrying (up to the task deadline) but records the
# reason on the task instead of rescheduling quietly.
_DELIVERY_READY_TIMEOUT_SECONDS = 2 * _PUBLISH_POLL_TIMEOUT_SECONDS
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

        if binding is None:
            return Complete()
        if binding.device_provider != TECLAW_DEVICE_PROVIDER:
            return Complete()
        current_publish_id = (binding.device_props or {}).get("publish_id")
        if current_publish_id is None or str(current_publish_id) != str(publish_id):
            return Complete()
        if binding.status == DeviceBindingStatus.ACTIVE.value:
            # Crash resume: an earlier attempt committed the terminal status but
            # may have died before (or while) delivering the token — the status
            # write and the delivery are two separate writes. Completing here on
            # the status alone would strand the container without a token for
            # good, so replay the delivery — the push is idempotent.
            return self._deliver_outbound_rule(
                bot_id=bot_id,
                binding=binding,
                publish_id=publish_id,
                started_at_epoch_s=started_at_epoch_s,
            )
        if binding.status != DeviceBindingStatus.PENDING.value:
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
                started_at_epoch_s=started_at_epoch_s,
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
        started_at_epoch_s: int | float,
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
            return self._deliver_outbound_rule(
                bot_id=bot_id,
                binding=binding,
                publish_id=publish_id,
                started_at_epoch_s=started_at_epoch_s,
            )

        return Complete()

    def _deliver_outbound_rule(
        self,
        *,
        bot_id: str,
        binding: DeviceBindingRecord,
        publish_id: int,
        started_at_epoch_s: int | float,
    ) -> TaskOutcome:
        """Deliver the bot's passport token to the just-started teclaw container.

        The container's PaaS device only exists once BaaS has executed the create
        publish (``start_device`` is what fills ``provider_device_id``), so this
        is the earliest point at which BaaS can answer with a device to write the
        outbound rule onto. Under all-auto approval (#197) the create response and
        the (now-ignored) client approve both return before that happens, which is
        why provision cannot push the rule inline.

        The delivery is a *second* write after the terminal status write, so it
        carries its own durability: nothing but a completed push may complete the
        task. A transient failure returns ``Retry`` (the queue re-drives it with
        backoff, bounded by the task deadline), and a task reclaimed after a
        crash between the two writes re-enters here from the ACTIVE binding and
        simply pushes again — the push is a rule REPLACE, so replaying it is
        idempotent and needs no delivery bookkeeping of its own.

        The updater's three states drive the outcome: ``None`` (this provider
        writes no outbound rules) completes, ``[]`` (BaaS has no ready device
        yet — publish SUCCESS does not guarantee ``provider_device_id`` is
        visible to the next read) reschedules, and a non-empty list is a
        delivery to every device of the bot.
        """
        bot_uuid = binding.device_id
        owner_id = binding.entity_id
        if not bot_uuid or not owner_id:
            # A teclaw binding without these is malformed — no retry can fix it.
            return Fail(
                "Teclaw outbound rule delivery has no target: "
                f"bot_id={bot_id} bot_uuid={bot_uuid!r} owner_id={owner_id!r}"
            )

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
            return Retry(f"query passport token failed: {exc}")

        if not agent_pass_token:
            # The passport may still be provisioning; retrying is bounded by the
            # task deadline, and giving up here would strand the container.
            logger.warning(
                "[TeclawPublishTaskHandler] queryToken empty: bot_id=%s owner_id=%s",
                bot_id,
                owner_id,
            )
            return Retry("passport token not available yet")

        try:
            updated = self._baas.update_teclaw_outbound_rule_by_bot_uuid(
                bot_uuid,
                agent_pass_token=agent_pass_token,
            )
        except Exception as exc:
            logger.warning(
                "[TeclawPublishTaskHandler] Teclaw outbound rule update failed: "
                "bot_id=%s bot_uuid=%s error=%s",
                bot_id,
                bot_uuid,
                exc,
            )
            return Retry(f"update Teclaw outbound rule failed: {exc}")

        if updated is None:
            # This provider applies no egress mutation — nothing to deliver, and
            # nothing to come back for.
            return Complete()

        if not updated:
            # Devices aren't ready yet. Wait on the same durable task rather than
            # blocking in-handler. Inside the readiness window this is the normal
            # path (Reschedule, no error noise); past it the wait is abnormal, so
            # it switches to Retry — same recoverability up to the task deadline,
            # but the reason is recorded on the task and the backoff widens. The
            # bot is already ACTIVE, so completing here would strand it silently.
            if (
                self._clock() - started_at_epoch_s
            ) >= _DELIVERY_READY_TIMEOUT_SECONDS:
                logger.warning(
                    "[TeclawPublishTaskHandler] Still no ready device past the "
                    "readiness window: bot_id=%s bot_uuid=%s publish_id=%s",
                    bot_id,
                    bot_uuid,
                    publish_id,
                )
                return Retry(
                    "Teclaw outbound rule has no ready device after publish "
                    f"SUCCESS: bot_id={bot_id} bot_uuid={bot_uuid} "
                    f"publish_id={publish_id}"
                )
            logger.info(
                "[TeclawPublishTaskHandler] No ready device for outbound rule yet, "
                "waiting: bot_id=%s bot_uuid=%s publish_id=%s",
                bot_id,
                bot_uuid,
                publish_id,
            )
            return Reschedule(self._poll_delay_seconds)

        logger.info(
            "[TeclawPublishTaskHandler] Teclaw outbound rule updated: "
            "bot_id=%s bot_uuid=%s publish_id=%s updated_count=%s",
            bot_id,
            bot_uuid,
            publish_id,
            len(updated),
        )
        return Complete()



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
