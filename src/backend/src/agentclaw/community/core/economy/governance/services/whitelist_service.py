"""[能力] Governance whitelist service — single-entry whitelist add + delete (§7.5).

Standalone service extracted from ``admin_service.py`` — whitelist management
is an independent functional domain, not a mixin of admin operations.

Key methods:
  - :meth:`bulk_whitelist` — iterate add + close active tickets (service orchestrates)
  - :meth:`delete_whitelist_entry` — single remove + audit
  - :meth:`add` — single add (thin delegate)
  - :meth:`count_by_type` — count (thin delegate)
  - :meth:`list_by_owner` — list by owner (thin delegate)

Repo provides single-point primitives; batch semantics are orchestrated here.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.domain.whitelist import WhitelistEntry
from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    CloseReason,
)
from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceLifecycleServiceProtocol,
)


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

log = get_logger(__name__)


class GovernanceWhitelistService:
    """Whitelist management — add, remove, bulk orchestration (independent domain)."""

    @inject
    def __init__(
        self,
        whitelist_repo: GovernanceWhitelistRepository,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        config: Any,  # EconomyGovernanceConfig
        lifecycle_svc: GovernanceLifecycleServiceProtocol,
    ) -> None:
        self._whitelist_repo = whitelist_repo
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._config = config
        self._lifecycle_svc = lifecycle_svc

    def bulk_whitelist(
        self,
        bot_ids: list[str],
        reason: str,
        operator: str,
    ) -> dict:
        """批量加白 + 取消待通知 + 关对应 ``task_record`` 主体 (Task 8 口径对齐)。

        通知侧 cancel scope = bot_id IN (...) 且 response IS NULL +
        open/muted。工单侧按被关通知的 ``ticket_id`` 集合关 —— 逐条走
        :meth:`emergency_close` 链路激活领域模型守卫、幂等(不可裸用全量
        ``bulk_close_open``,会多关已反馈的 scheduled 单)。

        Returns ``{"whitelisted": N, "cancelled": N}``.
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days

        # 1. 逐条 add whitelist (先点查, 仅对不在白名单中的 pair 计数)
        bot_owner_pairs = self._notify_repo.list_distinct_bot_owner(bot_ids)
        whitelisted = 0
        for bot_id, owner_id in bot_owner_pairs:
            already = self._whitelist_repo.is_whitelisted(bot_id, owner_id)
            self._whitelist_repo.add(
                bot_id=bot_id,
                owner_id=owner_id,
                created_by=operator,
                whitelist_type="governance",
                source="emergency",
            )
            if not already:
                whitelisted += 1

        # 2. Pre-collect the ticket_id set scoped to the same filter as the
        # notify bulk-cancel (bot_id IN (...) + response IS NULL + open/muted),
        # before the cancel mutates rows.
        ticket_ids = self._notify_repo.list_ticket_ids_by_bots(bot_ids)

        # 3. Cancel pending + close open/muted notifications (behavior unchanged)
        cancelled = self._notify_repo.bulk_cancel_by_bots(
            bot_ids,
            close_reason=CloseReason.EMERGENCY_CLOSED,
            closed_at=now,
            cooldown_until=now + timedelta(days=cooldown_days),
        )

        # 4. Ticket-side close — per-ticket guard-activated (EMERGENCY_CLOSED),
        # idempotent. Aligns the ticket/notify sets (Task 8).
        self._lifecycle_svc.bulk_close_by_ticket_ids(ticket_ids, now=now)

        self._audit_repo.add_audit(
            "emergency",
            action_taken=AuditAction.ADMIN_WHITELIST,
            actor_id=operator,
            error_msg=f"reason={reason}; operator={operator}; bot_count={len(bot_ids)}",
            source="admin_api",
            dry_run=0,
        )
        log.info(
            "[GovernanceWhitelist] bulk_whitelist by %s: whitelisted=%d, cancelled=%d, tickets_closed_by=%d",
            operator, whitelisted, cancelled, len(ticket_ids),
        )
        return {"whitelisted": whitelisted, "cancelled": cancelled}

    def delete_whitelist_entry(
        self,
        *,
        bot_id: str,
        owner_id: str,
        reason: str,
        operator: str,
    ) -> dict:
        """删除单条白名单 + 审计。

        Args:
            bot_id: Bot ID。
            owner_id: 负责人 ID。
            reason: 删除原因。
            operator: 操作人。

        Returns:
            ``{"deleted": bool, "bot_id": str, "owner_id": str}``
        """
        deleted = self._whitelist_repo.remove(
            bot_id=bot_id,
            owner_id=owner_id,
            whitelist_type="governance",
        )

        audit_run_id = f"wl-del-{uuid.uuid4().hex[:8]}"
        try:
            self._audit_repo.add_audit(
                audit_run_id,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=operator,
                action_taken=AuditAction.WHITELIST_REMOVED,
                source="admin_api",
                error_msg=f"reason={reason} bot_id={bot_id} owner_id={owner_id} deleted={deleted}",
                dry_run=0,
            )
        except Exception:
            log.exception("[WhitelistDelete] Failed to write audit")

        return {"deleted": deleted, "bot_id": bot_id, "owner_id": owner_id}

    # ------------------------------------------------------------------
    # Thin delegates — expose repo-level single-point operations.
    # ------------------------------------------------------------------

    def count_by_type(self, *, whitelist_type: str = "governance") -> int:
        """Count whitelist entries of a given type (delegates to repo)."""
        return self._whitelist_repo.count_by_type(whitelist_type=whitelist_type)

    def add(
        self,
        *,
        bot_id: str,
        owner_id: str,
        created_by: str,
        whitelist_type: str = "governance",
        source: str = "manual",
        reason: str = "",
    ) -> WhitelistEntry:
        """添加单条白名单 (delegates to repo)。

        Returns:
            WhitelistEntry 领域模型。
        """
        return self._whitelist_repo.add(
            bot_id=bot_id,
            owner_id=owner_id,
            created_by=created_by,
            whitelist_type=whitelist_type,
            source=source,
            reason=reason,
        )

    def list_by_owner(
        self,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
        limit: int = 100,
        offset: int = 0,
    ) -> list[WhitelistEntry]:
        """按 owner_id 分页查询白名单 (delegates to repo)。"""
        return self._whitelist_repo.list_by_owner(
            owner_id=owner_id,
            whitelist_type=whitelist_type,
            limit=limit,
            offset=offset,
        )

    def is_whitelisted(
        self,
        bot_id: str,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
    ) -> bool:
        """点查白名单 (delegates to repo)。"""
        return self._whitelist_repo.is_whitelisted(
            bot_id, owner_id, whitelist_type=whitelist_type,
        )