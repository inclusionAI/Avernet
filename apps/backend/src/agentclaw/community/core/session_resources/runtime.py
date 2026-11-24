"""File-operation adaptation of the shared runtime container locator."""

from dataclasses import dataclass

from injector import inject

from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.core.runtime_binding.container_locator import RuntimeContainerLocator
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingNotFoundError
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest, RuntimeBindingSource, RuntimeContainerRequest
from agentclaw.community.log import get_logger

log = get_logger()


@dataclass(frozen=True)
class SessionFileRuntimeTarget:
    binding_id: int
    owner_id: str
    affinity_id: str
    device_uuid: str | None


class SessionFileRuntimeResolver:
    @inject
    def __init__(self, containers: RuntimeContainerLocator) -> None:
        self._containers = containers

    def resolve(
        self, request: RuntimeBindingRequest, *, session_user_id: str | None = None,
        device_uuid: str | None = None,
    ) -> SessionFileRuntimeTarget:
        """Resolve one target after operation and friend-session authorization."""
        runtime = self._containers.resolve_binding(RuntimeContainerRequest(
            bot_id=request.bot_id, entity_id=request.entity_id or request.owner_id,
            actor_user_id=request.actor_user_id, stage=request.stage,
            f_user_id=session_user_id, device_uuid=device_uuid, environment=request.environment,
        ))
        # COSEC: only the canonical owner may inspect another friend's runtime.
        if session_user_id and session_user_id != request.actor_user_id and request.actor_user_id != runtime.owner_id:
            raise EngineResourceNotFoundError("session file not found")
        if runtime.binding.device_provider == "local":
            if device_uuid:
                raise RuntimeBindingNotFoundError("local runtime has no device UUID")
            selected_uuid = None
        else:
            try:
                targets = self._containers.resolve_targets(runtime, device_uuid=device_uuid or None)
            except ValueError as error:
                raise RuntimeBindingNotFoundError("session file device unavailable") from error
            if len(targets) != 1:
                log.warning("session_files.runtime.rejected binding_id=%s target_count=%s", runtime.binding.id, len(targets))
                raise RuntimeBindingNotFoundError("session file runtime must have one selected device")
            selected_uuid = targets[0].device_uuid
        affinity = session_user_id if runtime.source is RuntimeBindingSource.CALLER_INSTANCE else request.actor_user_id
        log.info(
            "session_files.runtime.selected bot_id=%s owner_id=%s actor_user_id=%s "
            "session_user_id=%s stage=%s binding_id=%s device_uuid=%s source=%s",
            request.bot_id, runtime.owner_id, request.actor_user_id, session_user_id,
            request.stage, runtime.binding.id, selected_uuid, runtime.source.value,
        )
        return SessionFileRuntimeTarget(runtime.binding.id, runtime.owner_id, affinity or request.actor_user_id, selected_uuid)
