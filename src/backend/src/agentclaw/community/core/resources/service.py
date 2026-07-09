"""Resource service — business logic for resource management.

Only the surface actually consumed by `api/resources/router.py` is
implemented here. File upload / directory sync / hard delete / etc.
remain in the legacy `services/openclawserver/` until their own routers
are migrated.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from agentclaw.community.core.resources.models import (
    Resource,
    ResourceType,
    create_link_resource,
    create_node_resource,
    create_url_resource,
)
from agentclaw.community.core.resources.repository.protocol import ResourceRepositoryProtocol
from agentclaw.community.log import get_logger

logger = get_logger()


class DuplicateResourceError(ValueError):
    """Raised when a resource with the same (name, type, parent_path, user) exists."""


class ResourceService:
    """Manage resources backed by `ResourceRepositoryProtocol`."""

    def __init__(self, *, bot_id: str, repository: ResourceRepositoryProtocol) -> None:
        self._bot_id = bot_id or "default"
        self._repo = repository

    # -- Queries -----------------------------------------------------------
    async def check_name_exists(
        self,
        *,
        name: str,
        resource_type: ResourceType,
        parent_path: Optional[str],
        user_id: Optional[str],
        exclude_id: Optional[str] = None,
    ) -> bool:
        """Return True if a resource with the same name already exists."""
        existing = self._repo.list_resources(
            resource_type=resource_type.value,
            parent_path=parent_path,
            user_id=user_id,
            bolt_id=self._bot_id,
        )
        for item in existing:
            if item.get("name") != name:
                continue
            if exclude_id and str(item.get("id")) == str(exclude_id):
                continue
            return True
        return False

    async def check_link_url_exists(
        self,
        *,
        url: str,
        user_id: Optional[str],
        exclude_id: Optional[str] = None,
    ) -> bool:
        """Return True if a LINK resource with the same URL already exists."""
        existing = self._repo.list_resources(
            resource_type=ResourceType.LINK.value,
            parent_path=None,
            user_id=user_id,
            bolt_id=self._bot_id,
        )
        for item in existing:
            attrs = item.get("attributes") or {}
            if attrs.get("url") != url:
                continue
            if exclude_id and str(item.get("id")) == str(exclude_id):
                continue
            return True
        return False

    def list_resources(
        self,
        *,
        resource_type: Optional[ResourceType] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Resource]:
        """Return resources matching the filters, as `Resource` instances."""
        items = self._repo.list_resources(
            resource_type=resource_type.value if resource_type else None,
            parent_path=parent_path,
            user_id=user_id,
            bolt_id=self._bot_id,
        )
        resources = [_dict_to_resource(d) for d in items]
        if limit:
            resources = resources[offset:offset + limit]
        return resources

    def count_children(self, parent_path: str) -> int:
        """Count children under a directory path."""
        return self._repo.count_resources(parent_path=parent_path, bolt_id=self._bot_id)

    # -- Mutations ---------------------------------------------------------
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
    ) -> Resource:
        """Create a URL resource; raises DuplicateResourceError on name clash."""
        if await self.check_name_exists(
            name=name,
            resource_type=ResourceType.URL,
            parent_path=parent_path,
            user_id=user_id,
        ):
            raise DuplicateResourceError(f"Resource '{name}' already exists")

        resource = create_url_resource(
            name=name,
            url=url,
            method=method,
            headers=headers,
            parent_path=parent_path,
            user_id=user_id,
            created_by=created_by,
            source="manual",
            bolt_id=self._bot_id,
        )
        stored = self._repo.create(resource.to_dict())
        resource.id = stored.get("id")
        return resource

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
    ) -> Resource:
        """Create a LINK resource; raises DuplicateResourceError on URL clash."""
        if await self.check_link_url_exists(
            url=url,
            user_id=user_id,
        ):
            raise DuplicateResourceError(f"LINK resource with URL '{url}' already exists")

        resource = create_link_resource(
            name=name,
            url=url,
            link_type=link_type,
            description=description,
            user_id=user_id,
            created_by=created_by,
            source="manual",
            bolt_id=self._bot_id,
        )
        if extra_attrs:
            resource.attributes.update(extra_attrs)
        stored = self._repo.create(resource.to_dict())
        resource.id = stored.get("id")
        return resource

    async def update_link_resource(
        self,
        *,
        resource_id: str,
        link_type: Optional[str] = None,
        url: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Resource:
        """Update a LINK resource's type, URL, and/or name.

        Raises ValueError if not found or URL clash.
        """
        stored = self._repo.get_by_id(resource_id)
        if not stored:
            raise ValueError(f"LINK resource '{resource_id}' not found")

        # Check bolt ownership
        stored_bolt = stored.get("bolt_id", "default")
        if stored_bolt != self._bot_id:
            raise ValueError(f"Resource '{resource_id}' does not belong to this bot")

        attrs = dict(stored.get("attributes", {}))
        update_data: dict[str, object] = {
            "gmt_modified": Resource.model_fields["gmt_modified"].default_factory(),  # type: ignore[attr-defined]
            "attributes": attrs,
        }

        if name is not None:
            update_data["name"] = name

        if url is not None and url != attrs.get("url"):
            if await self.check_link_url_exists(
                url=url,
                user_id=stored.get("user_id"),
                exclude_id=str(resource_id),
            ):
                raise ValueError(f"LINK resource with URL '{url}' already exists")
            attrs["url"] = url

        if link_type is not None and link_type != attrs.get("link_type"):
            attrs["link_type"] = link_type

        # Clear stale yuque resolved data when URL changes
        if url is not None and url != stored.get("attributes", {}).get("url"):
            attrs.pop("doc_id", None)
            attrs.pop("book_id", None)
            attrs.pop("yuque_type", None)

        if name is not None:
            update_data["name"] = name

        updated = self._repo.update(resource_id, update_data)
        if not updated:
            raise ValueError(f"Failed to update LINK resource '{resource_id}'")
        return _dict_to_resource(updated)

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
    ) -> Resource:
        """Create a Node resource; raises DuplicateResourceError on name clash."""
        if await self.check_name_exists(
            name=name,
            resource_type=ResourceType.NODE,
            parent_path=parent_path,
            user_id=user_id,
        ):
            raise DuplicateResourceError(f"Resource '{name}' already exists")

        resource = create_node_resource(
            name=name,
            node_address=node_address,
            path_alias=path_alias or name,
            scan_recursive=scan_recursive,
            parent_path=parent_path,
            user_id=user_id,
            created_by=created_by,
            source="manual",
            bolt_id=self._bot_id,
        )
        stored = self._repo.create(resource.to_dict())
        resource.id = stored.get("id")
        return resource


def _dict_to_resource(data: dict) -> Resource:
    """Coerce repository dict into the new `Resource` pydantic model.

    The stored dict may carry SQLAlchemy-derived fields we don't want in the
    domain model (e.g. `env`, `bolt_id` as 'default' string); we drop unknown
    keys so pydantic doesn't reject them.
    """
    allowed = {
        "id",
        "name",
        "resource_type",
        "status",
        "gmt_created",
        "gmt_modified",
        "attributes",
        "metadata",
        "user_id",
        "created_by",
        "source",
        "bolt_id",
    }
    cleaned = {k: v for k, v in data.items() if k in allowed}
    return Resource(**cleaned)
