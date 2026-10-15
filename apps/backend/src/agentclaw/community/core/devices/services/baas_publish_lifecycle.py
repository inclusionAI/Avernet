"""BaaS publish lifecycle helpers used by ``BaasDeviceService``."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from agentclaw.community.core.devices.models import AllocatedDevice, DeviceBindingStatus
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_service import BAAS_DEVICE_PROVIDER
from agentclaw.community.core.devices.startup_identity import resolve_startup_identity
from agentclaw.community.log import get_logger


logger = get_logger()


def create_publish_poll_idempotency_key(*, binding_id: int, publish_id: int) -> str:
    """Return the stable key for one live create-publish poll lifecycle."""
    return f"baas-create-poll:{binding_id}:{publish_id}"


def enqueue_create_publish_poll(
    task_queue_service: Any | None,
    *,
    binding_id: int,
    bot_id: str,
    owner_id: str,
    publish_id: int,
) -> bool:
    """Persist the durable BaaS create publish poll task."""
    if task_queue_service is None:
        return False

    from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
        BAAS_CREATE_PUBLISH_POLL_TASK,
        build_create_publish_poll_payload,
    )

    task_queue_service.enqueue(
        BAAS_CREATE_PUBLISH_POLL_TASK,
        build_create_publish_poll_payload(
            binding_id=binding_id,
            bot_id=bot_id,
            owner_id=owner_id,
            publish_id=publish_id,
            started_at_epoch_s=time.time(),
        ),
        deadline_seconds=86400,
        idempotency_key=create_publish_poll_idempotency_key(
            binding_id=binding_id,
            publish_id=publish_id,
        ),
    )
    return True


def handle_after_binding_persisted(
    *,
    task_queue_service: Any | None,
    mark_service_start_failed: Callable[..., None],
    binding_id: int,
    allocated: AllocatedDevice,
    bot_id: str,
    owner_id: str,
    device_props: dict,
) -> bool:
    """Claim BaaS create lifecycle and enqueue the durable publish poll task."""
    if allocated.device_provider != BAAS_DEVICE_PROVIDER:
        return False

    try:
        publish_id_raw = device_props.get("publish_id")
        if publish_id_raw is None:
            raise ValueError("invalid publish_id: None")
        try:
            publish_id = int(publish_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid publish_id: {publish_id_raw!r}") from exc
        if not enqueue_create_publish_poll(
            task_queue_service,
            binding_id=binding_id,
            bot_id=bot_id,
            owner_id=owner_id,
            publish_id=publish_id,
        ):
            raise RuntimeError("task queue service unavailable")
    except Exception as exc:
        logger.exception(
            "[baas_device_create] enqueue BaaS create publish poll failed: "
            "binding_id=%s bot_id=%s error=%s",
            binding_id,
            bot_id,
            exc,
        )
        mark_service_start_failed(
            binding_id=binding_id,
            error=f"enqueue BaaS create publish poll failed: {exc}",
        )
    return True


def dispatch_pool_layout_confirmation(
    *,
    binding: DeviceBindingRecord,
    startup_identity: str,
    dispatch_watchdog: Callable[..., None],
) -> tuple[bool, str]:
    """Reuse the authenticated startup callback after create recovery or restart."""
    props = binding.device_props or {}
    envs = props.get("envs")
    if not isinstance(envs, dict) or envs.get("AGENTCLAW_SKILLS_LAYOUT") != "pool":
        return True, "layout confirmation not required"
    if envs.get("AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION") != "skills-pool-p3-v1":
        return False, "unsupported Skills Pool startup contract"
    if resolve_startup_identity(props) != startup_identity:
        return False, "stale Skills Pool startup identity"
    bot_uuid = str(props.get("bot_uuid") or "")
    client_id = str(props.get("client_id") or binding.device_id)
    callback_token = str(props.get("callback_token") or "")
    if not bot_uuid or not client_id or not callback_token:
        return False, "incomplete Skills Pool startup callback context"
    try:
        dispatch_watchdog(
            bot_uuid=bot_uuid,
            client_id=client_id,
            token=callback_token,
            startup_identity=startup_identity,
        )
    except Exception as exc:
        return False, f"Skills Pool startup callback dispatch failed: {exc}"
    return True, "Skills Pool startup callback dispatched"


def poll_publish_once(*, baas_service: Any, publish_id: int) -> str | None:
    """Poll BaaS publish once and map into local ACTIVE/FAILED/PENDING."""
    from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

    try:
        progress = baas_service.get_publish_progress(
            publish_id=publish_id,
            include_devices=False,
        )
    except BaasServiceError as exc:
        logger.warning(
            "[poll_publish_once] get_publish_progress transient error: "
            "publish_id=%s error=%s",
            publish_id,
            exc,
        )
        return None

    status = str((progress or {}).get("status") or "").upper()
    if status == "SUCCESS":
        return DeviceBindingStatus.ACTIVE.value
    if status in ("FAILED", "REJECTED", "REVOKED"):
        return DeviceBindingStatus.FAILED.value
    return DeviceBindingStatus.PENDING.value


def refresh_codefuse_token_on_publish_success(
    *,
    baas_service: Any,
    vault: Any,
    bot_uuid: str | None,
    codefuse_token: str | None,
) -> str | None:
    """Refresh codefuse.json after BaaS restart publish succeeds."""
    if not codefuse_token or not bot_uuid:
        return None
    try:
        plaintext = vault.decrypt_or_passthrough(codefuse_token)
    except Exception as exc:
        return f"decrypt failed: {exc}"
    try:
        from agentclaw.community.core.devices.services.baas_codefuse_writer import (
            write_codefuse_token_baas,
        )

        write_codefuse_token_baas(baas_service, bot_uuid, plaintext)
        logger.info(
            "[BaasDeviceService] codefuse.json refreshed on restart: bot_uuid=%s",
            bot_uuid,
        )
        return None
    except Exception as exc:
        return f"write failed: {exc}"
