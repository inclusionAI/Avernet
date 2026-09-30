"""Shared BaaS lifecycle executor for device services.

This module owns the mechanical BaaS lifecycle steps:

- create a BaaS bot through the BaaS HTTP facade
- approve the returned publish when creation needs approval
- destroy a BaaS bot and approve the destroy publish when BaaS returns one

It deliberately does not decide *which* provider should be used and does not
build business-specific create payloads. Provider routing stays in
DeviceServiceRouter; payload construction stays in BaasService/callers; OCB
binding writes stay in the concrete DeviceService template method.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.log import get_logger


logger = get_logger()


@dataclass(frozen=True)
class BaasLifecycleResult:
    """Normalized BaaS lifecycle result used by device services."""

    bot_uuid: str
    publish_id: str
    request_id: str
    raw_response: dict[str, Any]


class BaasDeviceLifecycleError(DeviceServiceError):
    """Raised when the BaaS lifecycle backend cannot complete a step."""


class BaasDeviceLifecycleExecutor:
    """Execute BaaS create/destroy steps without provider-routing decisions."""

    def __init__(self, baas_service: Any) -> None:
        self._baas_service = baas_service

    def create_bot_from_payload(
        self,
        *,
        payload: dict[str, Any],
        owner_id: str,
        request_id: str,
        action: str = "create_bot",
        approve_comment: str = "自动审批",
        approve_publish: bool = True,
    ) -> BaasLifecycleResult:
        """Create a BaaS bot from a caller-provided payload.

        This executor intentionally does not construct the payload. Different
        BaaS products can have different payload schemas, while the lifecycle
        mechanics here stay the same: POST create, validate ``bot_uuid``, and
        approve the returned publish when the caller has not asked BaaS to do
        it through ``config.auto_approve_publish``.
        """

        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        try:
            result = self._baas_service.post_bots_api(
                path="/api/v1/bots",
                payload=payload,
                action=action,
            )
        except BaasServiceError as e:
            logger.error(
                "[BaasDeviceLifecycleExecutor.create_bot_from_payload] "
                "BaaS create failed: action=%s owner_id=%s error=%s",
                action,
                owner_id,
                e,
            )
            raise BaasDeviceLifecycleError(f"BaaS create failed: {e}") from e

        bot_uuid = (result or {}).get("bot_uuid", "")
        publish_id = (result or {}).get("publish_id")
        if not bot_uuid:
            raise BaasDeviceLifecycleError(
                f"BaaS create returned without bot_uuid: {result}"
            )

        if publish_id and approve_publish:
            try:
                self._baas_service.approve_publish(
                    publish_id=publish_id,
                    operator=owner_id,
                    request_id=request_id,
                    comment=approve_comment,
                )
            except BaasServiceError as e:
                logger.error(
                    "[BaasDeviceLifecycleExecutor.create_bot_from_payload] approve_publish failed: "
                    "publish_id=%s error=%s",
                    publish_id,
                    e,
                )
                raise BaasDeviceLifecycleError(
                    f"BaaS approve_publish failed: publish_id={publish_id} error={e}"
                ) from e

        return BaasLifecycleResult(
            bot_uuid=bot_uuid,
            publish_id=str(publish_id) if publish_id is not None else "",
            request_id=request_id,
            raw_response=dict(result or {}),
        )

    def destroy_bot(
        self,
        *,
        bot_uuid: str,
        operator: str,
        request_id: str,
    ) -> BaasLifecycleResult:
        """Destroy a BaaS bot and best-effort approve its destroy publish."""

        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        try:
            result = self._baas_service.destroy_bot(
                bot_uuid=bot_uuid,
                operator=operator,
                request_id=request_id,
            )
        except BaasServiceError as e:
            logger.error(
                "[BaasDeviceLifecycleExecutor.destroy] BaaS destroy failed: "
                "bot_uuid=%s error=%s",
                bot_uuid,
                e,
            )
            raise BaasDeviceLifecycleError(f"BaaS destroy failed: {e}") from e

        publish_id = (result or {}).get("publish_id")
        if publish_id:
            try:
                self._baas_service.approve_publish(
                    publish_id=publish_id,
                    operator=operator,
                    request_id=request_id,
                    comment="自动审批销毁",
                )
            except BaasServiceError as e:
                logger.warning(
                    "[BaasDeviceLifecycleExecutor.destroy] approve destroy publish "
                    "failed (non-blocking): publish_id=%s error=%s",
                    publish_id,
                    e,
                )

        return BaasLifecycleResult(
            bot_uuid=(result or {}).get("bot_uuid") or bot_uuid,
            publish_id=str(publish_id) if publish_id is not None else "",
            request_id=request_id,
            raw_response=dict(result or {}),
        )
