"""Router-local dependency onto the existing Session Resource state machine."""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.api.session_resource_service import (
    SessionResourceServiceProtocol,
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
    ) -> None:
        self._session_resources = session_resources
        self._runtime_bindings = runtime_bindings

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
        return self._session_resources.complete_upload(**kwargs)

    def get_status(self, **kwargs: Any) -> Any:
        return self._session_resources.get_status(**kwargs)

    def list_ready(self, **kwargs: Any) -> Any:
        return self._session_resources.list_resources(ready_only=True, **kwargs)

    async def open_content(self, **kwargs: Any) -> Any:
        return await self._session_resources.open_session_file_content(**kwargs)

    def delete(self, **kwargs: Any) -> Any:
        return self._session_resources.delete(**kwargs)


__all__ = ["OpenApiSessionFileAdapter"]
