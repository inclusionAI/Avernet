"""Service API Protocol for publish approval."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@dataclass
class ApprovalResult:
    """Result of an approval check operation.

    Attributes:
        should_approval: True = stop flow and wait for approval, False = continue executing
        status: One of "SKIP" | "PROCESSING" | "AGREED" | "DISAGREED" | "CANCEL" | "ERROR"
        approval: The approval object from ext.approval, or None
        message: Human-readable message describing the result
    """
    should_approval: bool
    status: str
    approval: Optional[Dict[str, Any]]
    message: str


@runtime_checkable
class PublishApprovalServiceProtocol(Protocol):
    """Service API for publish approval management."""

    async def check_and_process_should_approval(
        self,
        publish_record: Any,
        operator: str,
    ) -> ApprovalResult:
        """Check and process online (publish) approval."""
        ...

    async def check_and_process_offline_approval(
        self,
        publish_record: Any,
        operator: str,
    ) -> ApprovalResult:
        """Check and process offline (unpublish) approval."""
        ...

    async def handle_approval_callback(
        self,
        publish_id: int,
        action: str,
        applicant: str,
        puid: str,
        last_operate: str,
    ) -> Dict[str, Any]:
        """Handle antprocess approval callback."""
        ...
