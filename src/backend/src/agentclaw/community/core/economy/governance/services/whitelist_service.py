"""Governance whitelist service — batch whitelist add + delete (§7.5).

Standalone service extracted from ``admin_service.py`` — whitelist management
is an independent functional domain, not a mixin of admin operations.

Two methods:
  - :meth:`bulk_whitelist` — batch add whitelist + close active tickets
  - :meth:`delete_whitelist_entries` — batch remove whitelist + audit

Both methods are the *real implementation*; ``GovernanceAdminService`` retains
thin delegates for backward compatibility.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.contracts.enums import AuditAction
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceNotifyLog,
)
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.repositories.audit_repo import (
        GovernanceAuditRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
        NotifyLogRepository,
    )
    from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
        GovernanceWhitelistRepository,
    )
    from agentclaw.community.plugin_api.database_protocol import DatabasePlugin

log = logging.getLogger(__name__)


class GovernanceWhitelistService:
    """Whitelist management — batch add + delete (independent domain)."""

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        whitelist_repo: GovernanceWhitelistRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: Any,  # EconomyGovernanceConfig
    ) -> None:
        self._db = db
        self._whitelist_repo = whitelist_repo
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._config = config

    def bulk_whitelist(
        self,
        bot_ids: list[str],
        reason: str,
        operator: str,
    ) -> dict:
        """Batch whitelist + cancel pending notifications for specified bots.

        Returns ``{"whitelisted": N, "cancelled": N}``.
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        # 1. Batch whitelist — repos self-manage sessions
        # Get owner_ids for these bots from existing notifications
        bot_owner_pairs: list[dict] = [
            {"bot_id": bot_id, "owner_id": owner_id}
            for bot_id, owner_id in self._notify_repo.list_distinct_bot_owner(
                bot_ids,
            )
        ]

        whitelisted = 0
        if bot_owner_pairs:
            result = self._whitelist_repo.batch_add(
                entries=bot_owner_pairs,
                created_by=operator,
                whitelist_type="governance",
                source="emergency",
            )
            whitelisted = result.get("inserted", 0)

        # 2. Cancel pending + close open/muted notifications
        # Repo returns detached rows (self-managed session). Re-query in our own session.
        cancelled = 0
        with self._db.orm_session() as s:
            affected = (
                s.query(GovernanceNotifyLog)
                .filter(
                    GovernanceNotifyLog.bot_id.in_(bot_ids),
                    GovernanceNotifyLog.response.is_(None),
                    GovernanceNotifyLog.governance_status.in_(["open", "muted"]),
                )
                .all()
            )
            for row in affected:
                row.notify_status = "cancelled"
                row.governance_status = "closed"
                row.close_reason = "emergency_closed"
                row.closed_at = now
                row.cooldown_until = now + timedelta(days=cooldown_days)
                cancelled += 1

        self._audit_repo.add_audit(
            "emergency",
            action_taken=AuditAction.ADMIN_WHITELIST,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}; bot_count={len(bot_ids)}",
            source="admin_api",
            dry_run=0,
        )
        log.info(
            "[GovernanceWhitelist] bulk_whitelist by %s: whitelisted=%d, cancelled=%d",
            operator, whitelisted, cancelled,
        )
        return {"whitelisted": whitelisted, "cancelled": cancelled}

    def delete_whitelist_entries(self, body: dict, operator: str) -> dict:
        """Delete governance whitelist entries by ID or (bot_id, owner_id) pair.

        ``body`` keys: ids, bot_owner_pairs, dry_run, reason.
        """
        has_ids = body.get("ids") and len(body["ids"]) > 0
        has_pairs = body.get("bot_owner_pairs") and len(body["bot_owner_pairs"]) > 0

        pairs_dicts: list[dict] | None = None
        if has_pairs:
            pairs_dicts = body["bot_owner_pairs"]

        # Phase 1: Count (dry_run=true)
        count_result = self._whitelist_repo.batch_remove(
            ids=body.get("ids") if has_ids else None,
            bot_owner_pairs=pairs_dicts,
            whitelist_type="governance",
            dry_run=True,
        )
        would_delete = count_result["deleted"]
        not_found_raw = count_result.get("not_found", [])
        affected_pairs_raw = count_result.get("affected_pairs", [])

        # Phase 2: dry_run → return count only
        if body["dry_run"]:
            return {
                "dry_run": True,
                "would_delete": would_delete,
                "deleted": 0,
                "not_found": not_found_raw,
                "affected_owner_bots": affected_pairs_raw,
            }

        # Phase 3: real delete + audit
        delete_result = self._whitelist_repo.batch_remove(
            ids=body.get("ids") if has_ids else None,
            bot_owner_pairs=pairs_dicts,
            whitelist_type="governance",
            dry_run=False,
        )
        deleted = delete_result["deleted"]

        audit_run_id = f"wl-del-{uuid.uuid4().hex[:8]}"

        if affected_pairs_raw:
            for ap in affected_pairs_raw:
                try:
                    self._audit_repo.add_audit(
                        audit_run_id,
                        bot_id=ap.get("bot_id", ""),
                        owner_id=ap.get("owner_id", ""),
                        actor_id=operator,
                        action_taken=AuditAction.WHITELIST_REMOVED,
                        source="admin_api",
                        error_msg=(
                            f"reason={body.get('reason', '')} "
                            f"bot_id={ap.get('bot_id', '')} "
                            f"owner_id={ap.get('owner_id', '')} "
                            f"deleted={deleted}"
                        ),
                        dry_run=0,
                    )
                except Exception:
                    log.exception("[whitelist-delete] Failed to write audit")

        return {
            "dry_run": False,
            "would_delete": would_delete,
            "deleted": deleted,
            "not_found": not_found_raw,
            "affected_owner_bots": affected_pairs_raw,
        }

    # ------------------------------------------------------------------
    # Thin delegates — expose repo-level read/write for callers that
    # need direct access without the full bulk_whitelist orchestration.
    # ------------------------------------------------------------------

    def count_by_type(self, *, whitelist_type: str = "governance") -> int:
        """Count whitelist entries of a given type (delegates to repo)."""
        return self._whitelist_repo.count_by_type(whitelist_type=whitelist_type)

    def batch_add(
        self,
        entries: list[dict],
        created_by: str,
        *,
        whitelist_type: str = "governance",
        source: str = "manual",
    ) -> dict:
        """Batch-add whitelist entries (delegates to repo).

        Returns ``{"inserted": N, "skipped": N}``.
        """
        return self._whitelist_repo.batch_add(
            entries=entries,
            created_by=created_by,
            whitelist_type=whitelist_type,
            source=source,
        )