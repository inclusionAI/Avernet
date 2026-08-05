"""Repository protocol for session resources."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.session_resources.types import SessionResourceRecord


@runtime_checkable
class SessionResourceRepositoryProtocol(Protocol):
    def create(self, record: SessionResourceRecord) -> SessionResourceRecord: ...

    def get_by_resource_id(self, resource_id: str) -> SessionResourceRecord | None: ...

    def get_owned(
        self,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> SessionResourceRecord | None: ...
    def list_owned(
        self,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> list[SessionResourceRecord]: ...

    def cas_start_materialization(self, **kwargs) -> SessionResourceRecord | None: ...

    def cas_finish_materialization(self, **kwargs) -> SessionResourceRecord | None: ...

    def soft_delete(
        self,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> SessionResourceRecord | None: ...
