"""Router-local dependency onto the existing Session Resource state machine."""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.api.engine_session_files import EngineSessionFilesProtocol

from agentclaw.community.api.session_resource_service import (
    SessionResourceServiceProtocol,
)
from agentclaw.community.api.tc_resource_ready_observer import (
    TcResourceReadyObserverProtocol,
)
from agentclaw.community.core.runtime_binding.errors import (
    RuntimeBindingResolutionError,
)
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService


class OpenApiSessionFileAdapter:
    """Resolve a trusted OpenAPI upload binding, then preserve the legacy flow."""

    @inject
    def __init__(
        self,
        session_resources: SessionResourceServiceProtocol,
        runtime_bindings: RuntimeBindingResolutionService,
        resource_ready_observer: TcResourceReadyObserverProtocol,
        engine_files: EngineSessionFilesProtocol,
    ) -> None:
        self._session_resources = session_resources
        self._runtime_bindings = runtime_bindings
        self._resource_ready_observer = resource_ready_observer
        self._engine_files = engine_files

    def create_upload_intents(
        self,
        *,
        actor_user_id: str,
        owner_id: str,
        bot_id: str,
        session_key: str,
        stage: str,
        engine_type: str,
        files: list[tuple[str, int | None, str | None]],
    ) -> list[Any]:
        """Resolve one binding before creating legacy resource records."""
        try:
            binding_id = self._runtime_bindings.resolve(
                RuntimeBindingRequest(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_user_id=actor_user_id,
                    stage=stage,
                )
            ).binding_id
        except RuntimeBindingResolutionError as exc:
            raise ValueError("session_file_binding_unavailable") from exc

        return [
            self._session_resources.create_upload_intent(
                owner_id=owner_id,
                bot_id=bot_id,
                session_key=session_key,
                scope_type="openapi_session",
                engine_type=engine_type,
                filename=filename,
                binding_id=binding_id,
                size_bytes=size_bytes,
                content_hash=content_hash,
            )
            for filename, size_bytes, content_hash in files
        ]

    def complete_upload(self, **kwargs: Any) -> Any:
        record = self._session_resources.complete_upload(**kwargs)
        self._resource_ready_observer.notify_in_background(record)
        return record

    def get_status(self, **kwargs: Any) -> Any:
        record = self._session_resources.get_status(**kwargs)
        self._resource_ready_observer.notify_in_background(record)
        return record

    async def list_ready(
        self, *, target: RuntimeBindingRequest, session_key: str, session_user_id: str | None = None,
    ) -> Any:
        return await self._engine_files.list_files(target, session_key, session_user_id=session_user_id)

    async def open_content(
        self, *, target: RuntimeBindingRequest, session_key: str,
        resource_id: str, disposition: str,
        session_user_id: str | None = None,
    ) -> Any:
        return await self._engine_files.open_content(
            target, session_key, resource_id, disposition, session_user_id=session_user_id,
        )

    async def delete(
        self, *, target: RuntimeBindingRequest, session_key: str, resource_id: str,
    ) -> Any:
        return await self._engine_files.delete_file(target, session_key, resource_id)


__all__ = ["OpenApiSessionFileAdapter"]
