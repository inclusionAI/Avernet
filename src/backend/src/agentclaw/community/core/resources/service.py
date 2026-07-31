"""Resource service — business logic for resource management.

Only the surface actually consumed by `api/resources/router.py` is
implemented here. File upload / directory sync / hard delete / etc.
remain in the legacy `services/openclawserver/` until their own routers
are migrated.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agentclaw.community.core.resources.models import (
    Resource,
    ResourceType,
    create_file_resource,
    create_link_resource,
    create_node_resource,
    create_url_resource,
)
from agentclaw.community.core.resources.repository.protocol import (
    ResourceRepositoryProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class DuplicateResourceError(ValueError):
    """Raised when a resource with the same (name, type, parent_path, user) exists."""


class ResourceNotFoundError(ValueError):
    """Raised when a resource is missing or owned by a different bot.

    Cross-bot access collapses to the same error as not-found so a public caller
    cannot distinguish "exists but not yours / other tenant" from "does not
    exist" (parity with the bots 404 mapping in ``responses.ENVELOPE_ERRORS``).
    """


class FileTooLargeError(ValueError):
    """Raised when a preview's content exceeds the configured max preview size."""


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
            resources = resources[offset : offset + limit]
        return resources

    def count_children(self, parent_path: str) -> int:
        """Count children under a directory path."""
        return self._repo.count_resources(parent_path=parent_path, bolt_id=self._bot_id)

    def count_resources(self, *, resource_type: Optional[ResourceType] = None) -> int:
        """Total resource count for this bot matching ``resource_type``.

        A repo-level count (no Resource materialisation) for pagination
        ``total`` — pairs with ``list_resources(limit, offset)``.
        """
        return self._repo.count_resources(
            resource_type=resource_type.value if resource_type else None,
            bolt_id=self._bot_id,
        )

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
            raise DuplicateResourceError(
                f"LINK resource with URL '{url}' already exists"
            )

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
            raise ResourceNotFoundError("Resource not found")

        # Check bolt ownership
        stored_bolt = stored.get("bolt_id", "default")
        if stored_bolt != self._bot_id:
            raise ResourceNotFoundError("Resource not found")

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
                raise DuplicateResourceError(
                    "LINK resource with this URL already exists"
                )
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

    # -- File / generic resource ops ---------------------------------------
    #
    # device_fs is an opaque duck-typed boundary the adapter resolves
    # (DeviceFilesystemDispatcher) and forwards in. The slim service never
    # holds a dispatcher — it only calls .write_file / .read_file /
    # .delete_file on whatever the adapter passes. This keeps the pure-core
    # service free of device-layer imports. ``device_fs`` defaults to None so
    # the non-file / no-fs paths degrade gracefully (legacy parity: missing
    # → None → handler maps to 404).

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Get any resource by ID, or None if missing.

        Cross-bot access collapses to ``None`` (404) — same semantics as
        ``update_link_resource`` raising on a bolt mismatch, applied to the
        read path so a foreign-bot resource_id never leaks through the
        public ``GET /{resource_id}``.
        """
        item = self._repo.get_by_id(resource_id)
        if not item:
            return None
        if item.get("bolt_id", "default") != self._bot_id:
            return None
        return _dict_to_resource(item)

    async def delete_resource(
        self,
        resource_id: str,
        *,
        device_fs: Any = None,
    ) -> bool:
        """Delete a resource (file → device FS, else DB soft-delete).

        Returns False if the record is missing. Cross-bot access also
        collapses to ``False`` (404 — same ownership invariant as
        ``get_resource``; a foreign-bot resource_id never deletes).
        device_fs delete failures are logged and swallowed — the DB record
        is still soft-deleted so the resource disappears from listings
        (legacy parity: device-side cleanup is best-effort, DB is the
        source of truth for "gone").
        """
        item = self._repo.get_by_id(resource_id)
        if not item:
            return False
        if item.get("bolt_id", "default") != self._bot_id:
            return False
        resource = _dict_to_resource(item)
        if resource.is_file and resource.path and device_fs is not None:
            try:
                await device_fs.delete_file(resource.path)
            except Exception as e:
                logger.warning("[delete_resource] device_fs delete failed: %s", e)
        return self._repo.delete(resource_id)

    async def upload_file(
        self,
        *,
        data: bytes,
        filename: str,
        parent_path: str = "",
        user_id: Optional[str] = None,
        device_fs: Any = None,
    ) -> Resource:
        """Upload a file: device_fs write (if provided) + repo.create.

        Raises DuplicateResourceError on name clash. device_fs write
        failures bubble — the handler maps them to 502 Bad Gateway and
        NO record is created (a half-written file with no DB row would
        leak storage; failing fast keeps DB and device_fs consistent).
        ``write_file`` runs BEFORE ``repo.create`` so the record exists
        only when the bytes are durably on the device.
        """
        if await self.check_name_exists(
            name=filename,
            resource_type=ResourceType.FILE,
            parent_path=parent_path or None,
            user_id=user_id,
        ):
            raise DuplicateResourceError(f"Resource '{filename}' already exists")
        # Simplified: device_fs write at root filename. parent_path joining
        # lives at the device_fs boundary; the service records the path key.
        file_path = filename
        if device_fs is not None:
            # NOT swallowed: a write failure must surface to the handler as
            # an HTTP 502 (storage backend unavailable) and the repo.create
            # below MUST NOT run (otherwise we'd return 201 with a phantom
            # record pointing at a path that has no bytes).
            await device_fs.write_file(file_path, data)
        record = create_file_resource(
            name=filename,
            path=file_path,
            parent_path=parent_path,
            size=len(data),
            user_id=user_id,
            source="upload",
            bolt_id=self._bot_id,
        )
        stored = self._repo.create(record.to_dict())
        record.id = stored.get("id")
        return record

    async def download_resource(
        self,
        resource_id: str,
        *,
        device_fs: Any = None,
    ) -> Optional[tuple[bytes, str]]:
        """Read a FILE resource's bytes.

        Returns ``(bytes, content_type)`` for a downloadable FILE, or None
        when the record is missing, owned by a different bot, not a file,
        is a directory, has no path, no device_fs was supplied, the read
        fails, or the content is empty (the caller maps None → 404).
        Cross-bot access collapses to ``None`` (404) — see ``get_resource``.
        """
        item = self._repo.get_by_id(resource_id)
        if not item:
            return None
        if item.get("bolt_id", "default") != self._bot_id:
            return None
        resource = _dict_to_resource(item)
        if not resource.is_file or resource.is_directory or not resource.path:
            return None
        if device_fs is None:
            return None
        try:
            content = await device_fs.read_file(resource.path)
        except Exception:
            return None
        if not content:
            return None
        return (content, resource.mime_type or "application/octet-stream")

    async def preview_resource(
        self,
        resource_id: str,
        *,
        device_fs: Any = None,
        max_size: int = 1_048_576,  # 1 MB preview cap (legacy parity)
    ) -> Optional[Dict[str, Any]]:
        """Preview a FILE resource's content as text.

        Returns ``{"content": str, "content_type": str, "size": int}`` for
        a previewable non-directory FILE, or None when the record is
        missing, owned by a different bot, not a file, is a directory,
        has no path, no device_fs was supplied, the read fails, or the
        content is empty (the caller maps None → 404). Cross-bot access
        collapses to ``None`` (404) — see ``get_resource``. Raises
        ``ValueError`` when the content exceeds ``max_size`` — the caller
        maps that to HTTP 413 (legacy parity: "File too large for preview").
        """
        item = self._repo.get_by_id(resource_id)
        if not item:
            return None
        if item.get("bolt_id", "default") != self._bot_id:
            return None
        resource = _dict_to_resource(item)
        if not resource.is_file or resource.is_directory or not resource.path:
            return None
        if device_fs is None:
            return None
        try:
            content = await device_fs.read_file(resource.path)
        except Exception:
            return None
        if not content:
            return None
        if len(content) > max_size:
            raise FileTooLargeError(
                f"File too large for preview (max {max_size} bytes)"
            )
        content_type = resource.mime_type or "application/octet-stream"
        # preview content is text-ified; decode utf-8 best-effort, fall back
        # to latin-1 so binary blobs don't crash the handler (latin-1
        # round-trips any byte).
        try:
            content_str = content.decode("utf-8")
        except UnicodeDecodeError:
            content_str = content.decode("latin-1")
        return {
            "content": content_str,
            "content_type": content_type,
            "size": len(content),
        }


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
