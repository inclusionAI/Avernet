"""Provider selection and delegation shared by ``DeviceServiceRouter``."""

from __future__ import annotations

from typing import Protocol

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
    ArcaBotCreateBaasRolloutPolicy,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.log import get_logger


logger = get_logger()


class _ProviderRouter(Protocol):
    _repo: DeviceBindingRepository
    _providers: dict[str, DeviceService]
    _default_service: DeviceService
    _arca_baas_rollout_policy: ArcaBotCreateBaasRolloutPolicy


class DeviceProviderRouterMixin:
    """Own binding/device/create-time provider selection and narrow dispatch."""

    def _get_provider_for_binding(
        self: _ProviderRouter, binding_id: int
    ) -> DeviceService:
        record = self._repo.get_by_id(binding_id)
        if record is None:
            logger.warning(
                "binding %s not found; using default device provider", binding_id
            )
            return self._default_service
        if record.device_provider in self._providers:
            return self._providers[record.device_provider]
        logger.warning(
            "unknown device provider %s; using default", record.device_provider
        )
        return self._default_service

    def _get_provider_for_device_id(
        self: _ProviderRouter, device_id: str
    ) -> DeviceService:
        record = self._repo.get_by_device_id(device_id)
        if record is None:
            logger.warning(
                "device %s not found; using default device provider", device_id
            )
            return self._default_service
        if record.device_provider in self._providers:
            return self._providers[record.device_provider]
        logger.warning(
            "unknown device provider %s; using default", record.device_provider
        )
        return self._default_service

    def _get_provider_for_new_device(
        self: _ProviderRouter,
        staff_id: str,
        *,
        engine_type: str | None = None,
        template_type: str | None = None,
        bot_type: str | None = None,
    ) -> DeviceService:
        decision = self._arca_baas_rollout_policy.decide(
            user_id=staff_id,
            bot_type=bot_type or "",
            engine_type=engine_type or "openclaw",
            template_type=template_type or "",
        )
        provider_name = decision.target_provider
        if provider_name in self._providers:
            logger.info(
                "create provider selected: staff_id=%s provider=%s reason=%s",
                staff_id,
                provider_name,
                decision.reason,
            )
            return self._providers[provider_name]
        raise DeviceServiceError(
            f"unknown create provider {provider_name!r}; "
            f"reason={decision.reason}; registered={list(self._providers.keys())!r}"
        )

    def trigger_data_init_on_device_ready(
        self: _ProviderRouter,
        *,
        device_id: str,
        binding_id: int,
        require_pool_confirmation: bool = False,
    ) -> None:
        service = self._get_provider_for_binding(binding_id)
        service.trigger_data_init_on_device_ready(
            device_id=device_id,
            binding_id=binding_id,
            require_pool_confirmation=require_pool_confirmation,
        )
