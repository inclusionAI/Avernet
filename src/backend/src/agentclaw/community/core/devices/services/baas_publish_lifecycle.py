"""BaaS publish lifecycle helpers used by ``BaasDeviceService``."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from agentclaw.community.core.devices.models import AllocatedDevice, DeviceBindingStatus
from agentclaw.community.core.devices.services.device_service import BAAS_DEVICE_PROVIDER
from agentclaw.community.log import get_logger


logger = get_logger()

_PUBLISH_POLL_INTERVAL_SECONDS = 5
_PUBLISH_POLL_TIMEOUT_SECONDS = 600


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


def run_start_service_polling(
    *,
    baas_service: Any,
    device: AllocatedDevice,
    run_container_init: Callable[..., None],
    report_device_alive: Callable[..., None],
    engine: str,
    bot_type: str,
    bot_id: str | None,
    owner_id: str | None,
    admins: list[str] | None,
    codefuse_token: str | None,
) -> tuple[bool, str]:
    """Block until BaaS publish reaches SUCCESS, run init, then mark alive."""
    publish_id_raw = device.device_props.get("publish_id")
    callback_token = device.device_props.get("callback_token", "")
    bot_uuid = device.device_props.get("bot_uuid", "")
    if not publish_id_raw:
        return False, "missing publish_id in device_props"
    try:
        publish_id = int(publish_id_raw)
    except (TypeError, ValueError):
        return False, f"invalid publish_id: {publish_id_raw!r}"

    start = time.monotonic()
    last_status = ""
    from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

    logger.info(
        "[_start_service] BaaS device polling start: device_id=%s publish_id=%s",
        device.device_id,
        publish_id,
    )

    while (time.monotonic() - start) < _PUBLISH_POLL_TIMEOUT_SECONDS:
        time.sleep(_PUBLISH_POLL_INTERVAL_SECONDS)
        try:
            progress = baas_service.get_publish_progress(
                publish_id=publish_id,
                include_devices=False,
            )
        except BaasServiceError as exc:
            logger.warning(
                "[_start_service] get_publish_progress transient error "
                "(will retry): publish_id=%s error=%s",
                publish_id,
                exc,
            )
            continue

        status = (progress or {}).get("status", "")
        last_status = status
        logger.info(
            "[_start_service] publish progress: device_id=%s publish_id=%s status=%s",
            device.device_id,
            publish_id,
            status,
        )

        if status == "SUCCESS":
            if not bot_uuid:
                return False, "publish SUCCESS but missing bot_uuid in device_props"

            try:
                run_container_init(
                    bot_uuid=bot_uuid,
                    device=device,
                    engine=engine,
                    bot_type=bot_type,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    callback_token=callback_token,
                    admins=admins,
                    codefuse_token=codefuse_token,
                )
            except Exception as exc:
                logger.exception(
                    "[_start_service] container init failed: device_id=%s error=%s",
                    device.device_id,
                    exc,
                )
                return False, f"container init failed: {exc}"

            try:
                report_device_alive(device_id=device.device_id, token=callback_token)
            except Exception as exc:
                logger.exception(
                    "[_start_service] report_device_alive failed after SUCCESS: %s",
                    exc,
                )
                return False, f"report_device_alive failed: {exc}"
            return True, "BaaS publish SUCCESS, init done, device active"

        if status == "FAILED":
            return False, f"BaaS publish FAILED: publish_id={publish_id}"

    return False, (
        f"BaaS publish polling timeout after {_PUBLISH_POLL_TIMEOUT_SECONDS}s "
        f"(last_status={last_status!r}, publish_id={publish_id})"
    )


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
