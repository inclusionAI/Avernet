"""Read-only resolution of an existing Bot runtime binding."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentclaw.community.core.engine_runtime.stage import (
    SERVICE_BOT_TYPE,
    STAGE_DRAFT,
    STAGE_VERIFY,
    require_known_stage,
    resolve_stage_bind_id,
)
from agentclaw.community.core.runtime_binding.errors import (
    CallerInstanceNotReadyError,
    RuntimeBindingNotFoundError,
    RuntimeBotNotFoundError,
)
from agentclaw.community.core.runtime_binding.models import (
    ResolvedRuntimeBinding,
    RuntimeBindingRequest,
    RuntimeBindingSource,
    RuntimeBindingTarget,
)
from agentclaw.community.utils.env_utils import get_current_env

_CALLER_INSTANCE_STATUS = "success"


class RuntimeBindingResolutionService:
    """Resolve a binding id without provisioning or selecting a device target."""

    def __init__(
        self,
        *,
        bot_repository: Any,
        publish_repository: Any,
        binding_repository: Any,
        caller_instance_repository: Any,
        environment_provider: Callable[[], str] = get_current_env,
    ) -> None:
        self._bot_repository = bot_repository
        self._publish_repository = publish_repository
        self._binding_repository = binding_repository
        self._caller_instance_repository = caller_instance_repository
        self._environment_provider = environment_provider

    def resolve(self, request: RuntimeBindingRequest) -> ResolvedRuntimeBinding:
        """Return the current authorized binding for one trusted request."""
        bot = self._bot_repository.get_by_id_and_owner(
            request.bot_id,
            request.owner_id,
        )
        if not bot:
            raise RuntimeBotNotFoundError("runtime bot is unavailable")

        source = self._source(bot, request)
        if source is RuntimeBindingSource.CALLER_INSTANCE:
            binding_id = self._resolve_caller_instance_binding(
                bot,
                request,
                environment=request.environment or self._environment_provider(),
            )
        else:
            binding_id = self._resolve_shared_binding(
                bot=bot,
                request=request,
                source=source,
                environment=request.environment or self._environment_provider(),
            )
        self._require_active_binding(binding_id)
        return ResolvedRuntimeBinding(binding_id=binding_id, source=source)

    def _source(
        self,
        bot: dict[str, Any],
        request: RuntimeBindingRequest,
    ) -> RuntimeBindingSource:
        require_known_stage(request.stage)
        bot_type = self._value(bot.get("bot_type"))
        if request.target is RuntimeBindingTarget.CALLER_INSTANCE:
            return RuntimeBindingSource.CALLER_INSTANCE
        if request.target is RuntimeBindingTarget.CALLER_SERVICE:
            if bot_type != SERVICE_BOT_TYPE:
                raise RuntimeBindingNotFoundError(
                    "personal bot has no Caller Service runtime binding"
                )
            return self._service_source(request.stage)
        if bot_type == SERVICE_BOT_TYPE and self._value(bot.get("call_type")) == "caller":
            return RuntimeBindingSource.CALLER_INSTANCE
        if bot_type != SERVICE_BOT_TYPE:
            if request.stage != STAGE_DRAFT:
                raise RuntimeBindingNotFoundError(
                    "personal bot has no published runtime binding"
                )
            return RuntimeBindingSource.PERSONAL
        if request.stage == STAGE_DRAFT:
            return RuntimeBindingSource.SERVICE_DRAFT
        if request.stage == STAGE_VERIFY:
            return RuntimeBindingSource.SERVICE_VERIFY
        return RuntimeBindingSource.SERVICE_ONLINE

    @staticmethod
    def _service_source(stage: str) -> RuntimeBindingSource:
        if stage == STAGE_DRAFT:
            return RuntimeBindingSource.SERVICE_DRAFT
        if stage == STAGE_VERIFY:
            return RuntimeBindingSource.SERVICE_VERIFY
        return RuntimeBindingSource.SERVICE_ONLINE

    def _resolve_shared_binding(
        self,
        *,
        bot: dict[str, Any],
        request: RuntimeBindingRequest,
        source: RuntimeBindingSource,
        environment: str,
    ) -> int:
        if source in {
            RuntimeBindingSource.PERSONAL,
            RuntimeBindingSource.SERVICE_DRAFT,
        }:
            binding_id = self._positive_int(bot.get("binding_id"))
            if binding_id is None:
                raise RuntimeBindingNotFoundError("runtime binding is unavailable")
            return binding_id
        bot_pk = self._positive_int(bot.get("id"))
        if bot_pk is None:
            raise RuntimeBotNotFoundError("runtime bot identity is unavailable")
        return resolve_stage_bind_id(
            self._publish_repository,
            self._binding_repository,
            bot_pk=bot_pk,
            bot_id=request.bot_id,
            stage=request.stage,
            env=environment,
        )

    def _resolve_caller_instance_binding(
        self,
        bot: dict[str, Any],
        request: RuntimeBindingRequest,
        *,
        environment: str,
    ) -> int:
        instance = self._caller_instance_repository.get_instance(
            request.actor_user_id,
            request.bot_id,
            request.owner_id,
        )
        if not instance:
            raise CallerInstanceNotReadyError("Caller instance is not ready")
        ext = instance.get("ext")
        binding_id = self._positive_int(
            ext.get("binding_id") if isinstance(ext, dict) else None
        )
        if binding_id is None:
            raise CallerInstanceNotReadyError("Caller instance binding is unavailable")
        status = self._value(instance.get("status"))
        initializing_binding_id = self._positive_int(
            request.allow_initializing_caller_binding_id
        )
        if status != _CALLER_INSTANCE_STATUS and not (
            status == "init" and initializing_binding_id == binding_id
        ):
            raise CallerInstanceNotReadyError("Caller instance is not ready")
        binding = self._require_active_binding(binding_id)
        if (
            self._value(getattr(binding, "env", "")) != environment
            or self._value(getattr(binding, "entity_id", "")) != request.owner_id
            or self._value(getattr(binding, "apply_reason", ""))
            != f"caller_instance:{request.bot_id}"
            or self._value(getattr(binding, "applied_by", ""))
            != request.actor_user_id
            or self._value(getattr(binding, "device_provider", "")) != "baas"
        ):
            raise RuntimeBindingNotFoundError("Caller binding scope is invalid")
        return binding_id

    def _require_active_binding(self, binding_id: int) -> Any:
        binding = self._binding_repository.get_by_id(binding_id)
        if binding is None or self._value(getattr(binding, "status", "")).upper() != "ACTIVE":
            raise RuntimeBindingNotFoundError("runtime binding is inactive")
        return binding

    @staticmethod
    def _positive_int(value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

    @staticmethod
    def _value(value: object) -> str:
        return str(getattr(value, "value", value) or "").lower()


__all__ = ["RuntimeBindingResolutionService"]
