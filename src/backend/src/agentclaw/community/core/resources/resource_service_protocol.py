"""Service API Protocols for resource management and factory.

R8 layer: this file holds ONLY ``@runtime_checkable Protocol`` classes —
real signatures, no ``*args``/``**kwargs`` (round-2 review #4). The previous
loose ``*args`` form masked the bug surfaced by review #1 (handler passing
the openapi enum's ``.value`` string where the slim service expects the
legacy ``ResourceType`` enum) — declaring real signatures lets a type
checker catch that kind of drift the next time it's reintroduced.

``Resource`` / ``ResourceType`` are imported from the slim domain models
under ``core/resources/models.py`` so there's exactly one source of truth
(matching the established pattern in ``api/user_service.py`` and
``api/baas_service.py``). Concrete ``ResourceService`` lives under
``core/resources/service.py`` and conforms structurally — it does NOT
inherit the Protocol (the layering rule forbids ``core → api`` imports).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from agentclaw.community.core.resources.models import Resource, ResourceType


@runtime_checkable
class ResourceServiceProtocol(Protocol):
    """Service API for per-bot resource CRUD.

    Signatures mirror the slim ``core/resources/service.py`` ``ResourceService``
    verbatim — every method the openapi_v1 router and the legacy resources
    router call through ``ResourceServiceFactory.create`` is here. ``async`` /
    ``sync`` is load-bearing: ``check_link_url_exists`` and
    ``update_link_resource`` are ASYNC on the concrete service (the prior
    ``*args`` form wrongly declared them sync; runtime parity happened to
    hold because both router sides ``await`` them — the type-level contract
    was lying, now corrected). ``get_file_path`` is gone: it lives on a
    different service (``ResourceFileService``) and was never called through
    this Protocol seam.
    """

    async def check_name_exists(
        self,
        *,
        name: str,
        resource_type: ResourceType,
        parent_path: Optional[str],
        user_id: Optional[str],
        exclude_id: Optional[str] = None,
    ) -> bool: ...

    async def check_link_url_exists(
        self,
        *,
        url: str,
        user_id: Optional[str],
        exclude_id: Optional[str] = None,
    ) -> bool: ...

    def list_resources(
        self,
        *,
        resource_type: Optional[ResourceType] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Resource]: ...

    def count_children(self, parent_path: str) -> int: ...

    def count_resources(
        self, *, resource_type: Optional[ResourceType] = None
    ) -> int: ...

    async def create_url_resource(
        self,
        *,
        name: str,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Resource: ...

    async def create_link_resource(
        self,
        *,
        name: str,
        url: str,
        link_type: str,
        description: Optional[str] = None,
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
        extra_attrs: Optional[dict] = None,
    ) -> Resource: ...

    async def update_link_resource(
        self,
        *,
        resource_id: str,
        link_type: Optional[str] = None,
        url: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Resource: ...

    async def create_node_resource(
        self,
        *,
        name: str,
        node_address: str,
        path_alias: Optional[str] = None,
        scan_recursive: bool = True,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Resource: ...

    def get_resource(self, resource_id: str) -> Optional[Resource]: ...

    async def delete_resource(
        self,
        resource_id: str,
        *,
        device_fs: Any = None,
    ) -> bool: ...

    async def upload_file(
        self,
        *,
        data: bytes,
        filename: str,
        parent_path: str = "",
        user_id: Optional[str] = None,
        device_fs: Any = None,
    ) -> Resource: ...

    async def download_resource(
        self,
        resource_id: str,
        *,
        device_fs: Any = None,
    ) -> Optional[tuple[bytes, str]]: ...

    async def preview_resource(
        self,
        resource_id: str,
        *,
        device_fs: Any = None,
        max_size: int = 1_048_576,
    ) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class ResourceServiceFactoryProtocol(Protocol):
    """Service API for per-bot ResourceService factory."""

    def create(self, *, bot_id: str) -> ResourceServiceProtocol: ...
