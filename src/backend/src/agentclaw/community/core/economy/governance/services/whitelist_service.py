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
    from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
        TaskRecordRepository,
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
        task_repo: TaskRecordRepository,
    ) -> None:
        self._whitelist_repo = whitelist_repo
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._config = config
        self._lifecycle_svc = lifecycle_svc
        self._task_repo = task_repo

    def bulk_whitelist(
        self,
        bot_ids: list[str],
        reason: str,
        operator: str,
    ) -> dict:
        """批量加白 + 取消待通知 + 关对应 ``task_record`` 主体 (Task 8 口径对齐)。

        通知侧 cancel scope = bot_id IN (...) 且 response IS NULL +
        open/muted。工单侧按被关通知的 ``ticket_id`` 集合关 —— 逐条走
        :meth:`admin_close` 链路激活领域模型守卫、幂等(不可裸用全量
        ``bulk_close_open``,会多关已反馈的 scheduled 单)。

        审计口径(本特性):逐 ``(bot_id, owner_id)`` 对写**带实体**审计行 + 1 条批次
        摘要行,全部共享唯一 ``run_id``(可聚合同一次加白)。摘要行 ``error_msg`` 含
        真实处置计数。修复旧口径"单条孤儿审计行(bot_id=None/run_id='admin' 公共桶、
        按被治理实体查不到"的问题。

        Returns ``{"whitelisted": N, "cancelled": N}``.
        """
        now = datetime.now()
        cooldown_days = self._config.cooldown_days
        run_id = f"admin-wl-{uuid.uuid4().hex[:12]}"  # 唯一批次标识,聚合用

        # 1. 逐条 add whitelist (先点查, 仅对不在白名单中的 pair 计数) + 逐对写审计
        bot_owner_pairs = self._notify_repo.list_distinct_bot_owner(bot_ids)
        whitelisted = 0
        for bot_id, owner_id in bot_owner_pairs:
            already = self._whitelist_repo.is_whitelisted(bot_id, owner_id)
            self._whitelist_repo.add(
                bot_id=bot_id,
                owner_id=owner_id,
                created_by=operator,
                whitelist_type="governance",
                source="admin",
            )
            if not already:
                whitelisted += 1
            # 逐对带实体审计行 — 使审计可按被治理实体(bot/owner)检索。
            self._audit_repo.add_audit(
                run_id,
                bot_id=bot_id,
                owner_id=owner_id,
                action_taken=AuditAction.ADMIN_WHITELIST,
                actor_id=operator,
                error_msg=f"reason={reason}; added={'0' if already else '1'}",
                source="admin_api",
                dry_run=0,
            )

        # 2. Pre-collect the ticket_id set scoped to the same filter as the
        # notify bulk-cancel (bot_id IN (...) + response IS NULL + open/muted),
        # before the cancel mutates rows.
        ticket_ids = self._notify_repo.list_ticket_ids_by_bots(bot_ids)

        # 3. Cancel pending + close open/muted notifications (behavior unchanged)
        cancelled = self._notify_repo.bulk_cancel_by_bots(
            bot_ids,
            close_reason=CloseReason.ADMIN_CLOSED,
            closed_at=now,
            cooldown_until=now + timedelta(days=cooldown_days),
        )

        # 4. Ticket-side close — per-ticket guard-activated (ADMIN_CLOSED),
        # idempotent. Aligns the ticket/notify sets (Task 8).
        self._lifecycle_svc.bulk_close_by_ticket_ids(ticket_ids, now=now)

        # 5. 批次摘要行(同 run_id):汇总真实处置计数,供按批次聚合查询。
        skipped = len(bot_owner_pairs) - whitelisted
        self._audit_repo.add_audit(
            run_id,
            action_taken=AuditAction.ADMIN_WHITELIST,
            actor_id=operator,
            error_msg=(
                f"whitelisted={whitelisted}; skipped={skipped}; "
                f"cancelled={cancelled}; closed={len(ticket_ids)}; reason={reason}"
            ),
            source="admin_api",
            dry_run=0,
        )
        log.info(
            "[GovernanceWhitelist] bulk_whitelist by %s: run_id=%s whitelisted=%d, cancelled=%d, tickets_closed_by=%d",
            operator, run_id, whitelisted, cancelled, len(ticket_ids),
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

    def list_all(
        self,
        *,
        whitelist_type: str = "governance",
        owner_id: str | None = None,
        bot_id: str | None = None,
        include_expired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WhitelistEntry], int]:
        """全量分页查询白名单(delegates to repo)。

        可选 owner_id / bot_id 精确筛选;include_expired=False 默认排除已过期;
        返回 (领域模型列表, 筛选条件下总数)。
        """
        return self._whitelist_repo.list_all(
            whitelist_type=whitelist_type,
            owner_id=owner_id,
            bot_id=bot_id,
            include_expired=include_expired,
            limit=limit,
            offset=offset,
        )

    # 工单维度叠加字段(取最近一条工单的治理快照,服务 admin 复评白名单合理性)。
    def list_all_with_ticket_meta(
        self,
        *,
        whitelist_type: str = "governance",
        owner_id: str | None = None,
        bot_id: str | None = None,
        include_expired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """list_all + 按 (bot_id, owner_id) 批量叠加最近工单维度字段。

        白单为主表(白名单唯一权威来源);治理维度字段(bot_name/owner_name/
        token_baseline/expected_token_saving/hit_dimensions/saving_ratio/
        latest_decision/latest_ticket_gmt_create)取自该 worker **最近一条**
        governance 工单的快照(``find_latest_tickets_by_worker_keys`` 一次 IN
        查询 + Python 侧 group,无 N+1)。治理快照按监控刷新,反映 bot 当前
        状态,服务于 admin 复评「bot 是否持续恶化、建议是否合理」。

        Args: 同 :meth:`list_all`。

        Returns:
            ``(items, total)``:items 为 dict 列表(白单元数据 + 工单维度叠加),
            total 为筛选条件下的白单总数(分页配套)。
        """
        entries, total = self._whitelist_repo.list_all(
            whitelist_type=whitelist_type,
            owner_id=owner_id,
            bot_id=bot_id,
            include_expired=include_expired,
            limit=limit,
            offset=offset,
        )
        # worker_key 集合 → 批量取最近一条工单
        worker_keys = [f"{e.owner_id}:{e.bot_id}" for e in entries]
        latest_tickets = self._task_repo.find_latest_tickets_by_worker_keys(
            worker_keys,
        )
        items: list[dict] = []
        for entry in entries:
            item = entry.to_dict()
            ticket = latest_tickets.get(f"{entry.owner_id}:{entry.bot_id}")
            ticket_dict = ticket.to_dict() if ticket is not None else {}
            # 工单 gmt_create 转译为 latest_ticket_gmt_create,避免与白单无该字段歧义。
            item["bot_name"] = ticket_dict.get("bot_name")
            item["owner_name"] = ticket_dict.get("owner_name")
            item["token_baseline"] = ticket_dict.get("token_baseline")
            item["expected_token_saving"] = ticket_dict.get("expected_token_saving")
            item["hit_dimensions"] = ticket_dict.get("hit_dimensions")
            item["saving_ratio"] = ticket_dict.get("saving_ratio")
            item["latest_decision"] = ticket_dict.get("latest_decision")
            item["latest_ticket_gmt_create"] = ticket_dict.get("gmt_create")
            items.append(item)
        return items, total

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