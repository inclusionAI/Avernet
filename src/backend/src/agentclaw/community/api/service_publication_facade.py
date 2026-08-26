"""Service API seam for the public service-Bot publication facade."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ServicePublicationFacadeProtocol(Protocol):
    """Resolve, authorize and orchestrate service-Bot publication operations."""

    def list_publications(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]: ...

    def convert_to_service(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]: ...

    def upgrade_publication(
        self,
        bot_id: str,
        publication_id: int,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    def get_service_config(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]: ...

    def update_service_config(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
        should_approval: bool,
    ) -> dict[str, Any]: ...

    async def advance(
        self,
        bot_id: str,
        stage: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    def restart(
        self,
        bot_id: str,
        stage: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    async def cancel_staging(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    async def offline(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    async def retry(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    def delete_initial_draft(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> bool: ...

    def acquire_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> Any: ...

    def release_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> bool: ...

    def steal_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> Any: ...

    def get_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> Any: ...

    def list_containers(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]: ...

    def restart_container(
        self,
        bot_id: str,
        instance_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...
