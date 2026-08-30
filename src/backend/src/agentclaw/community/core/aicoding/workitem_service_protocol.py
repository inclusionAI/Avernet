"""Service API Protocol for hosted-workspace work-item operations.

Canonical Protocol — lives in the api layer so that adapters and DI
wiring can import it without reaching into plugin_api. Concrete impls
live in core/ (prod) and core/ (local/test stub), wired via DI modules.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


# NOTE: only the external route URL ``/api/public/dima`` still carries the vendor
# name (an external contract; renaming it is a separate api-route change). Every
# code identifier here + in the impls is neutral (B8 review).
@runtime_checkable
class WorkItemServiceProtocol(Protocol):
    """Service API for hosted-workspace work-item operations."""

    def create_work_item(self, staff_id: str, request_body: Dict[str, Any]) -> Dict[str, Any]: ...

    def create_work_item_relation(self, operator: str, request_body: Dict[str, Any]) -> Dict[str, Any]: ...

    def upload_file_to_arkgw(
        self,
        staff_id: str,
        source_id: str,
        file_content: bytes | None = None,
        file_name: str | None = None,
        content_type: str = "application/octet-stream",
        url: str | None = None,
    ) -> Dict[str, Any]: ...

    def update_work_item_document(
        self,
        staff_id: str,
        work_item_id: str,
        content: str,
        format_type: str = "MARKDOWN",
        editor_type: str = "YUQUE",
    ) -> Dict[str, Any]: ...
