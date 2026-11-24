"""Locate existing provider containers without creating or changing runtimes."""
from __future__ import annotations

from time import monotonic

from agentclaw.community.core.bot_management.engines.registry import resolve_provisioning
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingNotFoundError, RuntimeBotNotFoundError
from agentclaw.community.core.runtime_binding.models import (
    ResolvedRuntimeContainers,
    RuntimeBindingRequest,
    RuntimeBindingTarget,
    RuntimeContainerBinding,
    RuntimeContainerRequest,
    RuntimeContainerTarget,
)
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger

logger = get_logger()


class RuntimeContainerLocator:
    """Resolve authorized selectors; authentication and operation grants stay upstream.

    ``f_user_id`` names the friend-chat subject, never an authentication proof.
    Callers installing credentials must additionally authorize that subject and
    shared-runtime writes. No provisioning or fallback to another target occurs.
    """

    def __init__(
        self, *, bots: BotRepository, runtime_bindings: RuntimeBindingResolutionService,
        binding_repository: DeviceBindingRepository, baas_service: BaasService,
    ) -> None:
        self._bots = bots
        self._runtime_bindings = runtime_bindings
        self._binding_repository = binding_repository
        self._baas_service = baas_service

    def resolve_binding(self, request: RuntimeContainerRequest) -> RuntimeContainerBinding:
        """Select an existing binding without querying its physical replicas."""
        started = monotonic()
        logger.info(
            "runtime_container_binding_started bot_id=%s entity_id=%s stage=%s f_user_id=%s actor_user_id=%s",
            request.bot_id, request.entity_id, request.stage, request.f_user_id, request.actor_user_id,
        )
        try:
            # COSEC: resource entity and canonical owner can differ after transfer.
            bot = self._bots.get_by_id_and_entity(request.bot_id, request.entity_id)
            if not bot or not bot.get("owner_id"):
                raise RuntimeBotNotFoundError("runtime bot is unavailable")
            owner = str(bot["owner_id"])
            service = bot.get("bot_type") == "service"
            if service and request.stage is None:
                raise RuntimeBindingNotFoundError("service runtime stage is required")
            stage = request.stage if service else "draft"
            engine = str(bot.get("active_engine") or "openclaw").lower()
            target = RuntimeBindingTarget.CALLER_SERVICE if service else RuntimeBindingTarget.AUTO
            if service and bot.get("call_type") == "caller" and request.f_user_id:
                ctx, strategy = resolve_provisioning(
                    bot_id=request.bot_id, owner_id=owner, bot_type="service", active_engine=engine,
                    template_type=bot.get("template_type") or None,
                    template_config=bot.get("template_config") if isinstance(bot.get("template_config"), dict) else None,
                )
                if strategy.requires_caller_chat_instance(ctx):
                    target = RuntimeBindingTarget.CALLER_INSTANCE
            resolved = self._runtime_bindings.resolve(RuntimeBindingRequest(
                bot_id=request.bot_id, owner_id=owner,
                actor_user_id=request.f_user_id or request.actor_user_id,
                stage=stage, environment=request.environment, target=target,
                entity_id=request.entity_id,
            ))
            binding = self._binding_repository.get_by_id(resolved.binding_id)
            if binding is None or str(binding.status).upper() != "ACTIVE":
                raise RuntimeBindingNotFoundError("runtime binding is inactive")
        except Exception as exc:
            logger.warning(
                "runtime_container_binding_failed bot_id=%s entity_id=%s error_type=%s duration_ms=%.1f",
                request.bot_id, request.entity_id, type(exc).__name__, (monotonic() - started) * 1000,
            )
            raise
        logger.info(
            "runtime_container_binding_succeeded bot_id=%s binding_id=%s source=%s provider=%s engine=%s duration_ms=%.1f",
            request.bot_id, binding.id, resolved.source.value, binding.device_provider, engine,
            (monotonic() - started) * 1000,
        )
        return RuntimeContainerBinding(binding=binding, source=resolved.source, owner_id=owner, engine_type=engine)

    def resolve_targets(
        self, runtime: RuntimeContainerBinding, *, device_uuid: str | None = None,
    ) -> tuple[RuntimeContainerTarget, ...]:
        """Enumerate the current binding's targets, or one exact UUID match."""
        devices = self._baas_service.list_runtime_devices(runtime.binding, device_uuid=device_uuid)
        return tuple(RuntimeContainerTarget(
            provider=runtime.binding.device_provider,
            provider_device_id=device["provider_device_id"], device_uuid=device.get("device_uuid"),
        ) for device in devices)

    def resolve(self, request: RuntimeContainerRequest) -> ResolvedRuntimeContainers:
        runtime = self.resolve_binding(request)
        return ResolvedRuntimeContainers(runtime=runtime, targets=self.resolve_targets(runtime, device_uuid=request.device_uuid))
