"""Versioned outbound digital employee platform contract.

Payloads use the documented platform field names. Implementations must check
both HTTP status and the outer business envelope. Partial MCP application
results remain visible to consumers; they must not be turned into success.
"""
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class DigitalEmployeePlatformError(RuntimeError):
    """A platform operation failed without exposing credentials or response bodies."""


@runtime_checkable
class DigitalEmployeePlatformPlugin(Plugin, Protocol):
    def get_employee(self, work_no: str) -> dict[str, Any]: ...

    def apply_mcp_permissions(
        self, request_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    def query_mcp_permissions(
        self, work_no: str, mcp_codes: list[str]
    ) -> dict[str, Any]: ...

    def create_skill_change(
        self, work_no: str, request_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    def query_skill_change(self, task_id: str) -> dict[str, Any]: ...
