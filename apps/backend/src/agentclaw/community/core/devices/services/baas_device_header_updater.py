"""BaaS physical-device outbound-header updates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.devices.protocols import BotQueryProtocol
    from agentclaw.community.core.devices.services.baas_device_service import (
        TemplateConfigReader,
    )
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    from agentclaw.community.plugin_api.secret_resolver import SecretResolver


logger = get_logger()


class BaasDeviceHeaderUpdateError(DeviceServiceError):
    """Internal failure surfaced by the BaaS header-update module."""


class BaasDeviceHeaderUpdater:
    """Build and install outbound rules for one BaaS logical Bot."""

    def __init__(
        self,
        baas_service: BaasService,
        *,
        bot_query: "BotQueryProtocol | None" = None,
        template_service: "TemplateConfigReader | None" = None,
        secret_resolver: "SecretResolver | None" = None,
        theta_master_key_secret: str = "",
    ) -> None:
        self._baas_service = baas_service
        self._bot_query = bot_query
        self._template_service = template_service
        self._secret_resolver = secret_resolver
        self._theta_master_key_secret = theta_master_key_secret

    def update(
        self,
        *,
        device: AllocatedDevice,
        agent_pass_token: str = "",
        agent_code: str = "",
        active_only: bool = False,
    ) -> list[dict]:
        """Update one physical device or every eligible device under a Bot.

        ``device_props.device_uuid`` selects single-device mode. Otherwise the
        updater lists physical devices by the BaaS ``bot_uuid``. When
        ``active_only`` is true, only devices whose BaaS status is exactly
        ``ACTIVE`` are eligible.
        """
        bolt_id = device.device_props.get("bolt_id", "")
        owner_id = device.device_props.get("entity_id", "")
        device_uuid = device.device_props.get("device_uuid", "")

        logger.info(
            f"[BaasDeviceHeaderUpdater.update] Start: "
            f"device_id={device.device_id}, bolt_id={bolt_id}, owner_id={owner_id}, "
            f"has_device_uuid={'yes' if device_uuid else 'no'}, active_only={active_only}"
        )

        try:
            if device.device_provider == TECLAW_DEVICE_PROVIDER:
                outbound_rule = (
                    self._baas_service._build_teclaw_outbound_operation_rule(
                        agent_pass_token=agent_pass_token,
                    )
                )
            else:
                # 引擎解耦：复用创建链路同一套 provisioning 协议解析自定义 egress-key
                # envelope，避免 bootstrap REPLACE 用部署默认凭证覆盖创建/重启写入的值。
                from agentclaw.community.core.bot_management.engines import (
                    resolve_outbound_rule_envelope,
                )

                extra_properties = resolve_outbound_rule_envelope(
                    bot_id=bolt_id,
                    owner_id=owner_id,
                    bot_query=self._bot_query,
                    template_service=self._template_service,
                    secret_resolver=self._secret_resolver,
                    theta_master_key_secret=self._theta_master_key_secret,
                )
                logger.info(
                    "[BaasDeviceHeaderUpdater.update] outbound envelope: "
                    "device_id=%s, bolt_id=%s, owner_id=%s, "
                    "custom_outbound_key_resolved=%s",
                    device.device_id,
                    bolt_id,
                    owner_id,
                    bool(
                        isinstance(extra_properties, dict)
                        and extra_properties.get("outbound_api_key")
                    ),
                )
                outbound_rule = self._baas_service._build_outbound_operation_rule(
                    bot_id=bolt_id,
                    owner_id=owner_id,
                    agent_pass_token=agent_pass_token,
                    agent_code=agent_code,
                    extra_properties=extra_properties,
                )
        except Exception as e:
            logger.error(
                f"[BaasDeviceHeaderUpdater.update] Build rule failed: "
                f"device_id={device.device_id}, bolt_id={bolt_id}, owner_id={owner_id}, error={e}"
            )
            raise BaasDeviceHeaderUpdateError(f"构建 outbound rule 失败: {e}") from e

        if outbound_rule is None:
            return []

        try:
            if device_uuid:
                updated_devices = self._update_single_device(
                    device=device,
                    device_uuid=device_uuid,
                    outbound_rule=outbound_rule,
                    active_only=active_only,
                )
            else:
                updated_devices = self._update_bot_devices(
                    device=device,
                    outbound_rule=outbound_rule,
                    active_only=active_only,
                )
            logger.info(
                f"[BaasDeviceHeaderUpdater.update] Done: "
                f"device_id={device.device_id}, bolt_id={bolt_id}, owner_id={owner_id}, "
                f"updated_count={len(updated_devices)}"
            )
            return updated_devices
        except Exception as e:
            logger.error(
                f"[BaasDeviceHeaderUpdater.update] BaaS API error: "
                f"device_id={device.device_id}, bolt_id={bolt_id}, owner_id={owner_id}, error={e}"
            )
            raise BaasDeviceHeaderUpdateError(f"BaaS 设备 header 更新失败: {e}") from e

    def _update_single_device(
        self,
        *,
        device: AllocatedDevice,
        device_uuid: str,
        outbound_rule: object,
        active_only: bool,
    ) -> list[dict]:
        device_info = self._baas_service.get_device_by_uuid(device_uuid)
        if active_only and not self._is_active(device_info):
            logger.info(
                f"[BaasDeviceHeaderUpdater.update] Skip non-active device: "
                f"device_id={device.device_id}, device_uuid={device_uuid}, "
                f"status={device_info.get('status', '')}"
            )
            return []

        paas_device_id = device_info.get("provider_device_id")
        if not paas_device_id:
            raise BaasDeviceHeaderUpdateError(
                f"BaaS 设备缺少 provider_device_id: device_uuid={device_uuid}"
            )
        self._baas_service.update_device_outbound_rule(
            paas_device_id,
            outbound_rule,
        )
        logger.info(
            f"[BaasDeviceHeaderUpdater.update] Single device updated: "
            f"device_id={device.device_id}, device_uuid={device_uuid}, "
            f"paas_device_id={paas_device_id}"
        )
        return [
            {
                "baas_device_uuid": device_uuid,
                "paas_device_id": paas_device_id,
            }
        ]

    def _update_bot_devices(
        self,
        *,
        device: AllocatedDevice,
        outbound_rule: object,
        active_only: bool,
    ) -> list[dict]:
        bot_uuid = device.device_props.get("bot_uuid") or device.device_id
        devices = self._baas_service.list_devices_by_bot_uuid(bot_uuid)
        if active_only:
            devices = [dev for dev in devices if self._is_active(dev)]
        if not devices:
            logger.warning(
                f"[BaasDeviceHeaderUpdater.update] No devices found: "
                f"device_id={device.device_id}, bot_uuid={bot_uuid}, "
                f"active_only={active_only}"
            )
            return []

        updated_devices: list[dict] = []
        for physical_device in devices:
            paas_device_id = physical_device.get("provider_device_id")
            device_uuid = physical_device.get("device_uuid", "")
            if not paas_device_id:
                logger.warning(
                    f"[BaasDeviceHeaderUpdater.update] Skip device without provider_device_id: "
                    f"device_id={device.device_id}, device_uuid={device_uuid}"
                )
                continue
            self._baas_service.update_device_outbound_rule(
                paas_device_id,
                outbound_rule,
            )
            updated_devices.append(
                {
                    "device_uuid": device_uuid,
                    "paas_device_id": paas_device_id,
                }
            )
            logger.info(
                f"[BaasDeviceHeaderUpdater.update] Device updated: "
                f"device_id={device.device_id}, device_uuid={device_uuid}, "
                f"paas_device_id={paas_device_id}"
            )
        return updated_devices

    @staticmethod
    def _is_active(device: dict) -> bool:
        return str(device.get("status", "")).upper() == "ACTIVE"
