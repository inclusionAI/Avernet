"""Owner-scoped link management over the existing resource and Passport services."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.resources.service import (
    ResourceNotFoundError,
    DuplicateResourceError,
)
from agentclaw.community.core.repository.protocols.platform import (
    ResourceRepositoryProtocol,
)
from agentclaw.community.core.resources.dependencies.service_dep import (
    sync_yuque_permissions,
)
from agentclaw.community.core.resources.models import Resource, ResourceType
from agentclaw.community.core.resources.resource_service_protocol import (
    ResourceServiceFactoryProtocol,
)
from agentclaw.community.core.resources.yuque_resolve import resolve_yuque_url
from agentclaw.community.plugin_api.passport import PassportPlugin


@runtime_checkable
class LinkWorkflowProtocol(Protocol):
    def list(self, bot_id: str, owner_id: str) -> list[Resource]: ...
    async def create(
        self, bot_id: str, owner_id: str, links: list[dict[str, Any]]
    ) -> list[Resource]: ...
    async def update(
        self, bot_id: str, owner_id: str, resource_id: str, changes: dict[str, Any]
    ) -> Resource: ...
    async def delete(self, bot_id: str, owner_id: str, resource_id: str) -> None: ...


class LinkWorkflow(LinkWorkflowProtocol):
    def __init__(
        self,
        factory: ResourceServiceFactoryProtocol,
        repository: ResourceRepositoryProtocol,
        resolver: DeviceContextResolver,
        passport: PassportPlugin,
    ):
        self._factory, self._repo = factory, repository
        self._resolver, self._passport = resolver, passport

    def list(self, bot_id: str, owner_id: str) -> list[Resource]:
        return self._factory.create(bot_id=bot_id).list_resources(
            resource_type=ResourceType.LINK, user_id=owner_id
        )

    def _owned(self, bot_id: str, owner_id: str, resource_id: str) -> Resource:
        resource = self._factory.create(bot_id=bot_id).get_resource(resource_id)
        if (
            resource is None
            or resource.bolt_id != bot_id
            or resource.user_id != owner_id
            or resource.resource_type != ResourceType.LINK
        ):
            raise ResourceNotFoundError("Link resource not found")
        return resource

    def _resolve(
        self, bot_id: str, owner_id: str, link: dict[str, Any]
    ) -> dict[str, Any]:
        if link["link_type"] != "yuque":
            return {}
        resolved = resolve_yuque_url(
            link["url"], bot_id=bot_id, user_id=owner_id, resolver=self._resolver
        )
        if not link.get("name"):
            link["name"] = resolved.get("title") or link["url"]
        return {
            "doc_id": resolved["doc_id"],
            "book_id": resolved["book_id"],
            "yuque_type": resolved["type"],
            "access_modes": link.get("access_modes", ["READ"]),
        }

    def _sync(self, bot_id: str, owner_id: str) -> None:
        sync_yuque_permissions(
            bot_id, owner_id, self._repo, self._passport, strict=True
        )

    async def create(
        self, bot_id: str, owner_id: str, links: list[dict[str, Any]]
    ) -> list[Resource]:
        service = self._factory.create(bot_id=bot_id)
        urls = [link["url"] for link in links]
        if not links or len(set(urls)) != len(urls):
            raise DuplicateResourceError(
                "Links must be nonempty and contain no duplicate URLs"
            )
        resolved = []
        for link in links:
            if await service.check_link_url_exists(url=link["url"], user_id=owner_id):
                raise DuplicateResourceError("Link URL already exists")
            resolved.append(self._resolve(bot_id, owner_id, link))
        items = []
        for link, attrs in zip(links, resolved, strict=True):
            items.append(
                await service.create_link_resource(
                    name=link.get("name") or link["url"],
                    url=link["url"],
                    link_type=link["link_type"],
                    user_id=owner_id,
                    created_by=owner_id,
                    extra_attrs=attrs,
                )
            )
        self._sync(bot_id, owner_id)
        return items

    async def update(
        self, bot_id: str, owner_id: str, resource_id: str, changes: dict[str, Any]
    ) -> Resource:
        resource = self._owned(bot_id, owner_id, resource_id)
        merged = {**resource.attributes, **changes}
        attrs = (
            self._resolve(bot_id, owner_id, merged)
            if "url" in changes or "link_type" in changes
            else {}
        )
        service = self._factory.create(bot_id=bot_id)
        resource = await service.update_link_resource(
            resource_id=resource_id,
            url=changes.get("url"),
            link_type=changes.get("link_type"),
            name=changes.get("name"),
        )
        if "access_modes" in changes:
            attrs["access_modes"] = changes["access_modes"]
        if attrs:
            resource.attributes.update(attrs)
            if not self._repo.update(resource_id, {"attributes": resource.attributes}):
                raise RuntimeError("Link permission metadata write failed")
        self._sync(bot_id, owner_id)
        return resource

    async def delete(self, bot_id: str, owner_id: str, resource_id: str) -> None:
        self._owned(bot_id, owner_id, resource_id)
        if not await self._factory.create(bot_id=bot_id).delete_resource(resource_id):
            raise RuntimeError("Link delete failed")
        self._sync(bot_id, owner_id)
