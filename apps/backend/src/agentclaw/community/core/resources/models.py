"""Resource domain models.

Pure pydantic models owned by the new architecture. Do not import from
legacy `services/openclawserver/`.

Supports multiple resource types: file, url, node, database (future), api (future).
Type-specific fields live under `attributes` so new types don't require schema changes.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ResourceType(str, Enum):
    FILE = "file"
    URL = "url"
    NODE = "node"
    LINK = "link"
    DATABASE = "database"
    API = "api"


class ResourceStatus(str, Enum):
    ACTIVE = "active"
    PENDING = "pending"
    ERROR = "error"
    DELETED = "deleted"


class Resource(BaseModel):
    id: Optional[Any] = Field(default=None, description="Unique resource identifier")
    name: str = Field(..., description="Resource display name")
    resource_type: ResourceType = Field(..., description="Type of resource")
    status: ResourceStatus = Field(default=ResourceStatus.ACTIVE, description="Resource status")
    gmt_created: datetime = Field(default_factory=datetime.now, description="Creation time")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="Last update time")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Type-specific attributes")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Custom metadata")
    user_id: Optional[str] = Field(default=None, description="Owner user identifier")
    created_by: Optional[str] = Field(default=None, description="Creator identifier")
    source: Optional[str] = Field(default=None, description="Source identifier")
    bolt_id: Optional[str] = Field(default="default", description="Bot identifier")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}

    @property
    def is_file(self) -> bool:
        return self.resource_type == ResourceType.FILE

    @property
    def is_url(self) -> bool:
        return self.resource_type == ResourceType.URL

    @property
    def is_node(self) -> bool:
        return self.resource_type == ResourceType.NODE

    @property
    def is_link(self) -> bool:
        return self.resource_type == ResourceType.LINK

    # FILE attributes -------------------------------------------------------
    @property
    def path(self) -> Optional[str]:
        return self.attributes.get("path") if self.is_file else None

    @path.setter
    def path(self, value: Optional[str]) -> None:
        if self.is_file:
            self.attributes["path"] = value

    @property
    def parent_path(self) -> Optional[str]:
        return self.attributes.get("parent_path")

    @parent_path.setter
    def parent_path(self, value: Optional[str]) -> None:
        self.attributes["parent_path"] = value

    @property
    def size(self) -> int:
        return self.attributes.get("size", 0) if self.is_file else 0

    @size.setter
    def size(self, value: int) -> None:
        if self.is_file:
            self.attributes["size"] = value

    @property
    def mime_type(self) -> Optional[str]:
        return self.attributes.get("mime_type") if self.is_file else None

    @mime_type.setter
    def mime_type(self, value: Optional[str]) -> None:
        if self.is_file:
            self.attributes["mime_type"] = value

    @property
    def extension(self) -> Optional[str]:
        return self.attributes.get("extension") if self.is_file else None

    @extension.setter
    def extension(self, value: Optional[str]) -> None:
        if self.is_file:
            self.attributes["extension"] = value

    @property
    def is_directory(self) -> bool:
        return self.attributes.get("is_directory", False) if self.is_file else False

    @is_directory.setter
    def is_directory(self, value: bool) -> None:
        if self.is_file:
            self.attributes["is_directory"] = value

    @property
    def content_hash(self) -> Optional[str]:
        return self.attributes.get("content_hash") if self.is_file else None

    @content_hash.setter
    def content_hash(self, value: Optional[str]) -> None:
        if self.is_file:
            self.attributes["content_hash"] = value

    @property
    def preview_available(self) -> bool:
        return self.attributes.get("preview_available", False) if self.is_file else False

    @preview_available.setter
    def preview_available(self, value: bool) -> None:
        if self.is_file:
            self.attributes["preview_available"] = value

    # URL attributes --------------------------------------------------------
    @property
    def url(self) -> Optional[str]:
        """URL address (for URL and LINK types)"""
        if self.is_url or self.is_link:
            return self.attributes.get("url")
        return None

    @url.setter
    def url(self, value: Optional[str]) -> None:
        if self.is_url or self.is_link:
            self.attributes["url"] = value

    @property
    def method(self) -> str:
        return self.attributes.get("method", "GET") if self.is_url else "GET"

    @method.setter
    def method(self, value: str) -> None:
        if self.is_url:
            self.attributes["method"] = value

    @property
    def headers(self) -> Optional[Dict[str, str]]:
        return self.attributes.get("headers") if self.is_url else None

    @headers.setter
    def headers(self, value: Optional[Dict[str, str]]) -> None:
        if self.is_url:
            self.attributes["headers"] = value

    @property
    def last_fetch_status(self) -> Optional[int]:
        return self.attributes.get("last_fetch_status") if self.is_url else None

    @last_fetch_status.setter
    def last_fetch_status(self, value: Optional[int]) -> None:
        if self.is_url:
            self.attributes["last_fetch_status"] = value

    @property
    def last_fetch_time(self) -> Optional[datetime]:
        val = self.attributes.get("last_fetch_time") if self.is_url else None
        if isinstance(val, str):
            return datetime.fromisoformat(val)
        return val

    @last_fetch_time.setter
    def last_fetch_time(self, value: Optional[datetime]) -> None:
        if self.is_url:
            self.attributes["last_fetch_time"] = value.isoformat() if value else None

    @property
    def content_type(self) -> Optional[str]:
        return self.attributes.get("content_type") if self.is_url else None

    @content_type.setter
    def content_type(self, value: Optional[str]) -> None:
        if self.is_url:
            self.attributes["content_type"] = value

    @property
    def cached_content_path(self) -> Optional[str]:
        return self.attributes.get("cached_content_path") if self.is_url else None

    @cached_content_path.setter
    def cached_content_path(self, value: Optional[str]) -> None:
        if self.is_url:
            self.attributes["cached_content_path"] = value

    # LINK attributes -------------------------------------------------------
    @property
    def link_type(self) -> Optional[str]:
        return self.attributes.get("link_type") if self.is_link else None

    @link_type.setter
    def link_type(self, value: Optional[str]) -> None:
        if self.is_link:
            self.attributes["link_type"] = value

    @property
    def description(self) -> Optional[str]:
        return self.attributes.get("description") if self.is_link else None

    @description.setter
    def description(self, value: Optional[str]) -> None:
        if self.is_link:
            self.attributes["description"] = value

    # NODE attributes -------------------------------------------------------
    @property
    def node_address(self) -> Optional[str]:
        return self.attributes.get("node_address") if self.is_node else None

    @node_address.setter
    def node_address(self, value: Optional[str]) -> None:
        if self.is_node:
            self.attributes["node_address"] = value

    @property
    def path_alias(self) -> Optional[str]:
        return self.attributes.get("path_alias") if self.is_node else None

    @path_alias.setter
    def path_alias(self, value: Optional[str]) -> None:
        if self.is_node:
            self.attributes["path_alias"] = value

    @property
    def scan_recursive(self) -> bool:
        return self.attributes.get("scan_recursive", True) if self.is_node else True

    @scan_recursive.setter
    def scan_recursive(self, value: bool) -> None:
        if self.is_node:
            self.attributes["scan_recursive"] = value

    @property
    def last_scan_time(self) -> Optional[datetime]:
        val = self.attributes.get("last_scan_time") if self.is_node else None
        if isinstance(val, str):
            return datetime.fromisoformat(val)
        return val

    @last_scan_time.setter
    def last_scan_time(self, value: Optional[datetime]) -> None:
        if self.is_node:
            self.attributes["last_scan_time"] = value.isoformat() if value else None

    @property
    def file_count(self) -> int:
        return self.attributes.get("file_count", 0) if self.is_node else 0

    @file_count.setter
    def file_count(self, value: int) -> None:
        if self.is_node:
            self.attributes["file_count"] = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "resource_type": self.resource_type.value,
            "status": self.status.value,
            "gmt_created": self.gmt_created.isoformat() if self.gmt_created else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
            "attributes": self.attributes,
            "metadata": self.metadata,
            "user_id": self.user_id,
            "created_by": self.created_by,
            "source": self.source,
            "bolt_id": self.bolt_id or "default",
        }


def create_file_resource(
    name: str,
    path: str,
    parent_path: Optional[str] = None,
    size: int = 0,
    mime_type: Optional[str] = None,
    extension: Optional[str] = None,
    is_directory: bool = False,
    content_hash: Optional[str] = None,
    preview_available: bool = False,
    **kwargs: Any,
) -> Resource:
    attributes = {
        "path": path,
        "parent_path": parent_path,
        "size": size,
        "mime_type": mime_type,
        "extension": extension,
        "is_directory": is_directory,
        "content_hash": content_hash,
        "preview_available": preview_available,
    }
    return Resource(name=name, resource_type=ResourceType.FILE, attributes=attributes, **kwargs)


def create_url_resource(
    name: str,
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    parent_path: Optional[str] = None,
    **kwargs: Any,
) -> Resource:
    attributes = {
        "url": url,
        "method": method,
        "headers": headers,
        "parent_path": parent_path,
    }
    return Resource(name=name, resource_type=ResourceType.URL, attributes=attributes, **kwargs)


def create_link_resource(
    name: str,
    url: str,
    link_type: str,
    description: Optional[str] = None,
    **kwargs: Any,
) -> Resource:
    """Create a LINK resource for external knowledge sources (yuque/dima/antcode)."""
    attributes: Dict[str, Any] = {
        "url": url,
        "link_type": link_type,
    }
    if description:
        attributes["description"] = description
    return Resource(name=name, resource_type=ResourceType.LINK, attributes=attributes, **kwargs)


def create_node_resource(
    name: str,
    node_address: str,
    path_alias: Optional[str] = None,
    scan_recursive: bool = True,
    parent_path: Optional[str] = None,
    **kwargs: Any,
) -> Resource:
    attributes = {
        "node_address": node_address,
        "path_alias": path_alias or name,
        "scan_recursive": scan_recursive,
        "parent_path": parent_path,
    }
    return Resource(name=name, resource_type=ResourceType.NODE, attributes=attributes, **kwargs)


__all__ = [
    "ResourceType",
    "ResourceStatus",
    "Resource",
    "create_file_resource",
    "create_url_resource",
    "create_link_resource",
    "create_node_resource",
]
