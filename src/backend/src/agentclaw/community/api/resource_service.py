"""Service API Protocols for resource management and factory."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ResourceServiceProtocol(Protocol):
    """Service API for per-bot resource CRUD."""

    async def check_name_exists(self, *args: Any, **kwargs: Any) -> Any: ...

    def check_link_url_exists(self, *args: Any, **kwargs: Any) -> Any: ...

    def list_resources(self, *args: Any, **kwargs: Any) -> Any: ...

    def count_children(self, parent_path: str) -> int: ...

    async def create_link_resource(self, *args: Any, **kwargs: Any) -> Any: ...

    async def create_node_resource(self, *args: Any, **kwargs: Any) -> Any: ...

    async def create_url_resource(self, *args: Any, **kwargs: Any) -> Any: ...

    def update_link_resource(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_file_path(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class ResourceServiceFactoryProtocol(Protocol):
    """Service API for per-bot ResourceService factory."""

    def create(self, *, bot_id: str) -> ResourceServiceProtocol: ...
