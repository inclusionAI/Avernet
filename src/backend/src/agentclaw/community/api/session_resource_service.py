"""Service API for session-scoped uploaded resources."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class SessionResourceServiceProtocol(Protocol):
    def create_upload_intent(self, *args: Any, **kwargs: Any) -> Any: ...

    def complete_upload(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_status(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
    ) -> Any: ...

    def list_resources(self, *args: Any, **kwargs: Any) -> Any: ...

    def create_download_grant(self, *args: Any, **kwargs: Any) -> Any: ...

    def reference(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
    ) -> dict[str, object]: ...

    def delete(
        self,
        *,
        owner_id: str,
        bot_id: str,
        session_key: str,
        resource_id: str,
    ) -> Any: ...

    def materialized_callback(self, *args: Any, **kwargs: Any) -> Any: ...
