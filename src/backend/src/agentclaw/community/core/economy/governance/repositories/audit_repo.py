"""Governance audit repository — ``ac_governance_audit``.

Append-only audit trail for governance operations. Every scan run,
user feedback, and emergency action writes a row here.

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions; env is resolved
Env is resolved internally via ``get_current_env()``.
"""
from __future__ import annotations

import logging
from datetime import datetime

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceAudit,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env, get_server_host

log = get_logger()


class GovernanceAuditRepository:
    """Append-only audit trail for governance — self-managed sessions."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def add_audit(
        self,
        run_id: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        *,
        notification_id: str | None = None,
        actor_id: str | None = None,
        check_result: str | None = None,
        governance_decision: str | None = None,
        hit_dimensions: str | None = None,
        expected_token_saving: int | None = None,
        saving_ratio: float | None = None,
        action_taken: str | None = None,
        source: str = "daily_scan",
        error_msg: str | None = None,
        dry_run: int = 0,
    ) -> None:
        """Best-effort audit write (self-managed session).

        ``env`` and ``actor_id`` are explicitly set — no reliance on ORM defaults.
        ``server_host`` is auto-captured from ``socket.gethostname()``.
        Failures are caught and logged; this method never raises.
        """
        _env = get_current_env()
        server_host = get_server_host()
        try:
            with self._db.orm_session() as s:
                s.add(GovernanceAudit(
                    run_id=run_id,
                    notification_id=notification_id,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_id=actor_id,
                    server_host=server_host,
                    check_result=check_result,
                    governance_decision=governance_decision,
                    hit_dimensions=hit_dimensions,
                    expected_token_saving=expected_token_saving,
                    saving_ratio=saving_ratio,
                    action_taken=action_taken,
                    source=source,
                    error_msg=error_msg,
                    dry_run=dry_run,
                    env=_env,
                ))
        except Exception:
            log.exception(
                "[AuditRepo] best-effort write failed for run_id=%s, bot_id=%s",
                run_id, bot_id,
            )

    def get_last_scan_time(self) -> datetime | None:
        """MAX(audit.gmt_create) for data-dependent scan actions (self-managed session).

        Used by the data-readiness heuristic: the newest audit time for an
        ``enqueued`` / ``notification_created`` or ``auto_resolved`` /
        ``system_resolved`` action marks the last successful data-dependent
        scan. Accepts both legacy and new AuditAction values for transition
        compat.
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(func.max(GovernanceAudit.gmt_create))
                .filter(
                    GovernanceAudit.action_taken.in_([
                        # Legacy values (may exist in rows written before enum migration)
                        "enqueued", "auto_resolved",
                        # New AuditAction values
                        "notification_created", "system_resolved",
                    ]),
                    GovernanceAudit.env == _env,
                )
                .scalar()
            )