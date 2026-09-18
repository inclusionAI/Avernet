"""Public deployments do not have an employee-platform integration."""
from typing import Any

from agentclaw.community.plugin_api.digital_employee import DigitalEmployeePlatformError


class UnavailableDigitalEmployeePlatform:
    def get_employee(self, work_no: str) -> dict[str, Any]:
        raise DigitalEmployeePlatformError("Digital employee platform is not configured")

    def apply_mcp_permissions(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise DigitalEmployeePlatformError("Digital employee platform is not configured")

    def query_mcp_permissions(self, work_no: str, mcp_codes: list[str]) -> dict[str, Any]:
        raise DigitalEmployeePlatformError("Digital employee platform is not configured")

    def create_skill_change(self, work_no: str, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise DigitalEmployeePlatformError("Digital employee platform is not configured")

    def query_skill_change(self, task_id: str) -> dict[str, Any]:
        raise DigitalEmployeePlatformError("Digital employee platform is not configured")
