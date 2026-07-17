"""Governance audit repository — ``ac_governance_audit``.

Append-only audit trail for governance operations. Every scan run,
user feedback, and admin action writes a row here.

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions; env is resolved
Env is resolved internally via ``get_current_env()``.
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.economy.governance.repositories.orm import (
    AuditLogOrm,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env, get_server_host

log = get_logger(__name__)


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
                s.expire_on_commit = False
                s.add(AuditLogOrm(
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
                "[AuditRepo] best-effort write LOST: run_id=%s, bot_id=%s, owner_id=%s, action=%s",
                run_id, bot_id, owner_id, action_taken,
            )

    def list_by_subject(
        self,
        *,
        owner_id: str | None = None,
        bot_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        """Read-side audit query by governed entity (self-managed session).

        Mirrors :meth:`whitelist_repo.list_all`: filters on current ``env``,
        optional ``owner_id`` / ``bot_id`` / ``action`` (``action_taken``),
        returns ``(rows, total)`` ordered by ``gmt_create`` DESC with
        ``offset``/``limit`` pagination.

        Args:
            owner_id: optional exact owner filter.
            bot_id: optional exact bot filter.
            action: optional ``action_taken`` filter (AuditAction value).
            limit: page size.
            offset: page offset.

        Returns:
            ``(AuditLogOrm rows, total count under the same filters)``.

        Raises:
            ValueError: if all of ``owner_id``/``bot_id``/``action`` are empty
                (prevents full-table scans; the caller surfaces HTTP 400).
        """
        if not any([owner_id, bot_id, action]):
            raise ValueError(
                "list_by_subject requires at least one of owner_id/bot_id/action"
            )
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            filters = [AuditLogOrm.env == _env]
            if owner_id is not None:
                filters.append(AuditLogOrm.owner_id == owner_id)
            if bot_id is not None:
                filters.append(AuditLogOrm.bot_id == bot_id)
            if action is not None:
                filters.append(AuditLogOrm.action_taken == action)
            total = s.query(AuditLogOrm).filter(*filters).count()
            rows = (
                s.query(AuditLogOrm)
                .filter(*filters)
                .order_by(AuditLogOrm.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return rows, total