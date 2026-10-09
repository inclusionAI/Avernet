"""Append-only audit writer for the agentcompute boundary (design §17.5, D25).

Mirrors :class:`agentclaw.community.core.repository.implementations.governance.audit.GovernanceAuditRepository`:

- best-effort writes — every failure is caught and logged, never raised;
  an audit problem must never change the caller's response;
- self-managed ``DatabasePlugin.orm_session()`` per call;
- reads are subject-filtered only (a run, or an actor) — no full-table scan
  surface;
- append-only by protocol shape: no update/delete methods exist, so
  ``ac_agent_compute_audit`` rows are structurally immutable from this module.

Rows are written by the HTTP router for: accepted submits, rejected submits
(admission reasons: ``tenant_limit`` / ``config_missing`` / ``queue_depth`` /
``global_limit``), and cancel requests (``not_found`` when the tenant cannot
see the run). Health/ready and per-poll reads are deliberately not audited —
the closed action set is:

    submit_accepted | submit_rejected | cancel_requested
"""
from __future__ import annotations

from enum import StrEnum

from injector import inject

from agentclaw.community.core.agentcompute.repository.audit_models import (
    AcAgentComputeAuditModel,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

log = get_logger(__name__)

_MAX_READ_ROWS = 500

# Reason vocabulary (documented in the table DDL); rejections must pick from
# this set so ops queries can group without scrubbing prose.
REASONS = frozenset({
    "tenant_limit", "global_limit", "queue_depth", "config_missing", "not_found",
})


class AuditAction(StrEnum):
    """Closed set of auditable boundary actions (D18-style vocabulary)."""

    SUBMIT_ACCEPTED = "submit_accepted"
    SUBMIT_REJECTED = "submit_rejected"
    CANCEL_REQUESTED = "cancel_requested"


class AgentComputeAuditWriter:
    """Best-effort append-only audit writer for ``ac_agent_compute_audit``.

    ``env`` is set explicitly rather than relying on the ORM column default,
    matching the governance audit precedent — a row must never inherit an
    ambiguous env even if session defaults change under it.
    """

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def record(
        self,
        *,
        tenant: str,
        actor: str,
        action: AuditAction,
        reason: str | None = None,
        run_id: str | None = None,
        source: str = "http",
    ) -> None:
        """Append one audit row. Never raises (best-effort, governance shape)."""
        try:
            with self._db.orm_session() as s:
                s.expire_on_commit = False
                s.add(AcAgentComputeAuditModel(
                    env=get_current_env(),
                    tenant=tenant,
                    actor=actor,
                    action=action.value,
                    reason=reason,
                    run_id=run_id,
                    source=source,
                ))
        except Exception:
            log.exception(
                "[AgentComputeAudit] best-effort write LOST: tenant=%s "
                "actor=%s action=%s reason=%s run_id=%s",
                tenant, actor, action, reason, run_id,
            )

    def list_by_run(
        self, run_id: str, tenant: str, limit: int = 50,
    ) -> list[AcAgentComputeAuditModel]:
        """Audit history for one run, scoped to its tenant (subject-filtered)."""
        if not run_id or not tenant:
            raise ValueError("list_by_run requires a non-empty run_id and tenant")
        return self._select(
            [AcAgentComputeAuditModel.run_id == run_id,
             AcAgentComputeAuditModel.tenant == tenant],
            limit,
        )

    def list_by_actor(self, actor: str, limit: int = 50) -> list[AcAgentComputeAuditModel]:
        """Recent audit rows for one actor (subject-filtered; no unfiltered reads)."""
        if not actor:
            raise ValueError("list_by_actor requires a non-empty actor")
        return self._select([AcAgentComputeAuditModel.actor == actor], limit)

    def _select(self, filters: list, limit: int) -> list[AcAgentComputeAuditModel]:
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(AcAgentComputeAuditModel)
                .filter(*filters, AcAgentComputeAuditModel.env == get_current_env())
                .order_by(AcAgentComputeAuditModel.id.desc())
                .limit(min(max(limit, 1), _MAX_READ_ROWS))
                .all()
            )
            return list(rows)