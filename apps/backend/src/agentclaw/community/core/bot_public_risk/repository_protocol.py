"""Persistence contract and data for public risk applications."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RiskCheck:
    risk_check_id: str
    env: str
    bot_pk: int
    owner_id: str
    operator_id: str
    client_scope: str
    client_request_id: str
    public_scope: str
    target_json: str
    request_hash: str
    input_json: str
    input_hash: str
    approval_biz_id: str
    phase: str = "PREPARING"
    security_status: str | None = None
    auto_poll_paused: bool = False
    next_action_at: datetime | None = None
    auto_poll_until: datetime | None = None
    check_id: str | None = None
    approval_puid: str | None = None
    detail_url: str | None = None
    notification_due_at: datetime | None = None
    workflow_json: str = "{}"
    current_target_key: str | None = None
    row_version: int = 1
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None


class RequestConflict(ValueError):
    """A request identifier already belongs to a different intent."""


class RiskRepository(Protocol):
    """Risk persistence scoped to the active tenant and supplied environment.

    Errors propagate; a false CAS result means the observation is stale or no
    longer current. Recovery pages use exclusive after_id and stable ID order.
    The narrow tenant discovery operation is the sole cross-tenant lookup.
    """
    def find_risk_tenant(self, *, env: str, risk_check_id: str) -> str | None:
        ...

    def create_or_replay(self, record: RiskCheck) -> tuple[RiskCheck, bool]:
        """Atomically replay identical request IDs or replace the current target; conflicting intent raises RequestConflict."""
        ...

    def get_by_request(self, *, env: str, client_scope: str, client_request_id: str) -> RiskCheck | None:
        ...

    def get(self, *, env: str, risk_check_id: str) -> RiskCheck | None:
        ...

    def get_current(self, *, env: str, bot_pk: int, public_scope: str) -> RiskCheck | None:
        ...

    def find_approval(self, *, env: str, puid: str) -> RiskCheck | None:
        ...

    def update_current(self, *, env: str, risk_check_id: str, row_version: int, changes: dict) -> bool:
        """CAS only the observed current record; reject invalid field changes."""
        ...

    def invalidate_current(self, *, env: str, bot_pk: int, public_scope: str) -> bool:
        ...

    def invalidate_if_current(self, *, env: str, risk_check_id: str, row_version: int) -> bool:
        """Withdraw only the observed current version, never its replacement."""
        ...

    def list_due(self, *, env: str, now: datetime, after_id: str='', limit: int=100) -> tuple[RiskCheck, ...]:
        """Page current due work; exclude paused records and reject invalid limits."""
        ...

    def update_notice(self, *, env: str, risk_check_id: str, row_version: int, workflow_json: str, notification_due_at: datetime | None) -> bool:
        ...

    def list_notice_due(self, *, env: str, now: datetime, after_id: str='', limit: int=100) -> tuple[RiskCheck, ...]:
        """Page due notices including historical records; reject invalid limits."""
        ...

    def list_recovery_tenants(self, *, env: str) -> tuple[str, ...]:
        ...
