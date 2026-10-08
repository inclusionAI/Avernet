"""Service contracts for employee binding; no transport or vendor SDK types."""
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class DigitalEmployeeError(RuntimeError):
    """Safe, user-visible binding/governance failure."""


@dataclass(frozen=True)
class DigitalEmployeeSettings:
    enabled: bool = False
    platform_code: str = ""
    site_url: str = ""


class DigitalEmployeeRepositoryProtocol(Protocol):
    def get_bot(self, bot_pk: int) -> dict[str, Any]: ...

    def list_service_bots(self, creator_no: str) -> list[dict[str, Any]]: ...

    def begin_binding(
        self, bot_pk: int, work_no: str, event_id: str, detail: dict[str, Any]
    ) -> dict[str, Any]: ...

    def change_binding_phase(
        self, bot_pk: int, work_no: str, expected: str, phase: str
    ) -> bool: ...


@runtime_checkable
class DigitalEmployeeServiceProtocol(Protocol):
    def list_bindable(self, creator_no: str) -> list[dict[str, Any]]: ...

    def handle_onboarded(self, event: dict[str, Any]) -> None: ...

    def query_permissions(
        self, bot_pk: int, mcp_codes: list[str]
    ) -> dict[str, Any]: ...

    def prepare_mcp_change(
        self, bot: dict[str, Any], desired: list[dict[str, Any]],
        historical: list[dict[str, Any]], operator: str,
    ) -> list[dict[str, Any]]: ...


class DigitalEmployeePublicationProtocol(Protocol):
    def capture(self, bot: dict[str, Any]) -> dict[str, Any] | None: ...

    def capture_artifact(self, bot: dict[str, Any], artifact_ext: dict[str, Any]) -> dict[str, Any]: ...

    def prepare_online(self, publish_id: int, operator: str) -> bool: ...

    def require_approved(self, publish_id: int) -> None: ...

    def finalize_scope(self, publish_id: int) -> None: ...

    def handle_approval_result(self, event: dict[str, Any]) -> tuple[int, str] | None: ...


@runtime_checkable
class DigitalEmployeeCatalogProtocol(Protocol):
    async def detail(self, agent_id: int, version_status: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DigitalEmployeeArtifactStorage:
    bucket_name: str
