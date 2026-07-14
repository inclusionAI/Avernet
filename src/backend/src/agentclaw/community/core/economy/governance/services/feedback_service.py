"""[编排] Governance feedback service — task_record based (§7.4).

Handles 4 formal responses (optimized / need_time / dispute / whitelist)
via the **one-time feedback rule** (§7.4.1): one task_record gets at most
one user response. Repeated clicks or terminal-state submissions are
rejected with audit but no field mutation.

All lifecycle transitions land on ``task_record`` — never on ``notify_log``
(§4.2.3 读写路由规则). User feedback enters ``waiting_review`` (Phase1
rule, §7.4.2) rather than closing the ticket directly; all closures are
admin-driven (§7.5).
"""
from __future__ import annotations

import json
from agentclaw.community.log import get_logger
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from injector import inject

from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    GovernanceStatus,
    Response,
)
from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceLifecycleServiceProtocol,
    GovernanceWhitelistServiceProtocol,
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

log = get_logger(__name__)

# Valid formal responses
_FORMAL_RESPONSES = {e.value for e in Response}

# Statuses that block user feedback (§7.4.1 step 3)
_BLOCKED_STATUSES = {GovernanceStatus.SCHEDULED, GovernanceStatus.WAITING_REVIEW, GovernanceStatus.CLOSED}

# response → (target_status, review_reason) — all go to waiting_review (§7.4.2)
_RESPONSE_TRANSITION_MAP: dict[str, tuple[str, str]] = {
    Response.OPTIMIZED: (GovernanceStatus.WAITING_REVIEW, "user_optimized"),
    Response.DISPUTE: (GovernanceStatus.WAITING_REVIEW, "user_disputed"),
    Response.WHITELIST: (GovernanceStatus.WAITING_REVIEW, "user_whitelisted"),
    # need_time → scheduled, handled separately
}

# ── v2 feedback_payload 归一化 ─────────────────────────────────────
# 卡片顶层 raw `response` → 归一化 `overall.decision`(对齐下游 ETL 枚举)。
# 逐项决策直接采用卡片 items[].action,未出现 index 视为 `unevaluated`。
_NORMALIZED_OVERALL_DECISION: dict[str, str] = {
    Response.OPTIMIZED.value: "accepted",
    Response.NEED_TIME.value: "deferred",
    Response.DISPUTE.value: "rejected",
    Response.WHITELIST.value: "whitelist",
}

# 归一化顶层决策中需要 remark 的取值(dispute/whitelist → rejected/whitelist)。
_REMARK_REQUIRED_RAW = {Response.DISPUTE.value, Response.WHITELIST.value}

# 卡片逐项 action → 归一化 item.decision(verbatim;卡片仅 accepted/partial/rejected)。
_ITEM_ACTIONS = {"accepted", "partial", "rejected"}
_UNEVALUATED = "unevaluated"


def _normalize_response(raw_response: str) -> str:
    """把卡片顶层 raw `response` 归一化为 `overall.decision` 枚举。

    Args:
        raw_response: 卡片回传的原始 response(optimized/need_time/dispute/whitelist)。

    Returns:
        归一化决策: accepted | deferred | rejected | whitelist。

    Raises:
        ValueError: raw_response 不在 4 个合法值内(调用方应已先行校验)。
    """
    normalized = _NORMALIZED_OVERALL_DECISION.get(raw_response)
    if normalized is None:
        raise ValueError(f"Unnormalizable response: {raw_response}")
    return normalized


def _compute_consistency_flag(
    overall_decision: str,
    item_decisions: list[str],
) -> str:
    """计算 overall 与逐项决策一致性标志。

    Args:
        overall_decision: 归一化顶层决策(accepted/rejected/deferred/whitelist)。
        item_decisions: 已点评项的归一化逐项决策列表(不含未点评项)。

    Returns:
        ``consistent`` 逐项全同一且与顶层一致;
        ``partial_mix`` 顶层 accepted 但存在逐项 rejected/partial;
        ``overall_dominates`` 无任何逐项反馈(全 unevaluated)。
    """
    if not item_decisions:
        return "overall_dominates"
    unique = set(item_decisions)
    if len(unique) == 1 and next(iter(unique)) == overall_decision:
        return "consistent"
    if overall_decision == "accepted" and (unique & {"rejected", "partial"}):
        return "partial_mix"
    return "consistent"


# ── v2 feedback_payload enrich(自包含) ─────────────────────────────
_FEEDBACK_SCHEMA_VERSION = 2


def _coerce_payload_input(payload: Any) -> dict[str, Any]:
    """把卡片回传的 feedback_payload(Pydantic 模型或 dict)归一为 dict。

    Args:
        payload: ``CardCallbackFeedbackPayload`` 模型 / dict / None。

    Returns:
        dict;None 输入返回空 dict。
    """
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    # Pydantic v2 模型
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=False)
    return dict(payload)  # 兜底


def _parse_notification_structured(raw: str | None) -> dict[str, Any] | None:
    """解析 ticket.notification_structured JSON;失败/空返回 None(降级用)。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _hit_dimensions_list(ticket: Any) -> list[str]:
    """ticket.triggered_dimensions(逗号分隔串)→ 去空 list。"""
    raw = ticket.triggered_dimensions
    if not raw:
        return []
    return [d.strip() for d in str(raw).split(",") if d.strip()]


def _build_enriched_items(
    structured: dict[str, Any] | None,
    user_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """构建逐项反馈(含建议项正文快照),未点评项标 unevaluated。

    以 ``notification_structured.action_items`` 为建议全集(确保 index 对齐)，
    按 index 合并用户决策。structured 解析失败时退回用户提供的 items。

    Args:
        structured: 解析后的 notification_structured dict(可能 None)。
        user_items: 用户回传的逐项决策 list[{index,action,remark}]。

    Returns:
        items[]: 每项含 index/decision/suggestion_action/正文快照/remark。
    """
    user_by_index: dict[int, dict[str, Any]] = {}
    for it in user_items:
        idx = it.get("index")
        if isinstance(idx, int):
            user_by_index[idx] = it

    raw_suggestions = (
        structured.get("action_items") or structured.get("optimizationSuggestions") or []
        if structured
        else []
    )

    if not raw_suggestions:
        # 降级:无建议全集,仅输出用户点评项
        return [
            {
                "index": it.get("index"),
                "decision": it.get("action") if it.get("action") in _ITEM_ACTIONS else _UNEVALUATED,
                "suggestion_action": None,
                "what_to_change": None,
                "why": None,
                "expected_effect": None,
                "remark": it.get("remark"),
            }
            for it in user_items
            if isinstance(it.get("index"), int)
        ]

    result: list[dict[str, Any]] = []
    for idx, sug in enumerate(raw_suggestions, start=1):
        index = sug.get("index") if isinstance(sug.get("index"), int) else idx
        user_it = user_by_index.get(index)
        action = user_it.get("action") if user_it else None
        decision = action if action in _ITEM_ACTIONS else _UNEVALUATED
        result.append({
            "index": index,
            "decision": decision,
            "suggestion_action": sug.get("action") or sug.get("title"),
            "what_to_change": sug.get("what_to_change") or sug.get("description"),
            "why": sug.get("why"),
            "expected_effect": sug.get("expected_effect"),
            "remark": user_it.get("remark") if user_it else None,
        })
    return result


def _build_enriched_payload(
    *,
    ticket: Any,
    notification_id: str,
    raw_response: str,
    remark: str | None,
    repair_deadline: datetime | None,
    feedback_payload: Any,
    source: str,
    actor_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    """从 ticket + 回传输入构建自包含 v2 feedback_payload dict。

    输入仅取用户能提供的(整体决策/备注/截止日/逐项决策);``ticket_ref``/
    ``analysis_snapshot``/建议项正文快照均由服务端注入,不可被前端伪造。
    notification_structured 解析失败时降级,绝不丢弃用户决策。

    Args:
        ticket: 已加载的 GovernanceTicket(自带 worker_id/dt_version 等)。
        notification_id: 通知唯一 ID。
        raw_response: 卡片原始 response(optimized/need_time/dispute/whitelist)。
        remark: 用户整体备注。
        repair_deadline: need_time 截止日。
        feedback_payload: 卡片结构化输入(模型/dict/None)。
        source: 反馈来源(card_callback/http_api/admin_api)。
        actor_id: 实际操作人。
        now: 提交时刻。

    Returns:
        v2 自包含 payload dict,调用方 json.dumps 后写库。
    """
    overall_decision = _normalize_response(raw_response)
    payload_in = _coerce_payload_input(feedback_payload)
    user_items_raw = payload_in.get("items") or []
    user_items = [it for it in user_items_raw if isinstance(it, dict)]

    structured = _parse_notification_structured(ticket.notification_structured)
    items = _build_enriched_items(structured, user_items)

    item_decisions = [it["decision"] for it in items if it["decision"] != _UNEVALUATED]
    consistency_flag = _compute_consistency_flag(overall_decision, item_decisions)

    deferred_until = (
        repair_deadline.isoformat() if raw_response == Response.NEED_TIME.value and repair_deadline else None
    )

    meta_block = structured.get("meta") if structured and isinstance(structured.get("meta"), dict) else {}

    return {
        "feedback_schema_version": _FEEDBACK_SCHEMA_VERSION,
        "ticket_ref": {
            "notification_id": notification_id,
            "worker_id": ticket.worker_id,
            "dt_version": ticket.dt_version,
            "ticket_id": ticket.ticket_id,
        },
        "analysis_snapshot": {
            "hit_dimensions": _hit_dimensions_list(ticket),
            "hit_dimensions_count": ticket.hit_dimensions_count,
            "governance_action": meta_block.get("governance_action"),
            "governance_urgency": ticket.severity,
            "governance_decision": ticket.initial_decision,
            "expected_token_saving": ticket.estimated_saving_tokens,
            "saving_ratio": ticket.saving_ratio,
            "suggestion_count": len(items),
        },
        "overall": {
            "decision": overall_decision,
            "raw_response": raw_response,
            "remark": remark,
            "deferred_until": deferred_until,
            "consistency_flag": consistency_flag,
        },
        "items": items,
        "meta": {
            "response_source": source,
            "submitted_at": now.isoformat(),
            "staff_id": actor_id,
            "actor_id": actor_id,
        },
    }


@dataclass
class ResolveResult:
    """Result of a resolve operation."""

    success: bool = False
    ticket_id: str = ""
    governance_status: str = ""
    close_reason: str | None = None
    mute_until: datetime | None = None
    error: str | None = None
    # Card callback needs these fields for response body
    response: str = ""
    response_source: str = ""
    message: str | None = None
    # Notification ID for backward compat (card callback traceback)
    notification_id: str = ""
    # Structured error code for HTTP status mapping
    error_code: str | None = None


def _result_from_ticket(
    ticket: Any,
    *,
    notification_id: str = "",
    message: str | None = None,
) -> ResolveResult:
    """Build a ResolveResult from a GovernanceTicket for idempotent returns."""
    return ResolveResult(
        success=True,
        ticket_id=(ticket.ticket_id or ""),
        notification_id=notification_id,
        governance_status=(ticket.governance_status or ""),
        close_reason=ticket.close_reason,
        mute_until=ticket.resume_at,
        response=(ticket.user_feedback or ""),
        response_source=(ticket.feedback_source or ""),
        message=message,
    )


class GovernanceFeedbackService:
    """Handle user feedback on governance tickets (§7.4)."""

    @inject
    def __init__(
        self,
        whitelist_service: GovernanceWhitelistServiceProtocol,
        notify_repo: NotifyLogRepository,
        audit_repo: GovernanceAuditRepository,
        task_repo: TaskRecordRepository,
        config: Any,  # EconomyGovernanceConfig
        lifecycle_svc: GovernanceLifecycleServiceProtocol,
    ) -> None:
        # ``whitelist_service`` / ``notify_repo`` retained as injected deps
        # (constructor signature stable across migration); the resolve path
        # now delegates whitelist-add + cancel-pending to lifecycle_svc, so
        # these are read only by future admin/review paths. Group C cleanup
        # may drop them if confirmed unused.
        self._whitelist_service = whitelist_service
        self._notify_repo = notify_repo
        self._audit_repo = audit_repo
        self._task_repo = task_repo
        self._config = config
        self._lifecycle_svc = lifecycle_svc

    def resolve(
        self,
        notification_id: str,
        response: str,
        user_id: str = "",
        *,
        actor_id: str | None = None,
        remark: str | None = None,
        source: str = "http_api",
        repair_deadline: datetime | None = None,
        feedback_payload: dict | None = None,
    ) -> ResolveResult:
        """Process a user response on a governance notification (§7.4).

        One-time feedback rule (§7.4.1):
          1. Ticket not found → error
          2. response not empty → duplicate ignored
          3. status in (scheduled, waiting_review, closed) → terminal ignored
          4. Only open + response empty → accept

        State transitions (§7.4.2):
          - optimized/dispute/whitelist → waiting_review
          - need_time → scheduled

        Args:
            notification_id: The notification to resolve.
            response: One of optimized/need_time/dispute/whitelist.
            user_id: The user's ID.  When empty (card_callback),
                owner_id is resolved from the DB record.
            actor_id: Actual operator (defaults to user_id).
            remark: Optional remark (required for dispute and whitelist).
            source: Response source (http_api / card_callback / admin_api).
            repair_deadline: Required for need_time.
            feedback_payload: Optional structured feedback JSON.

        Returns:
            ResolveResult with outcome details.
        """
        # Find ticket via notification_id → notify_log.ticket_id → task_record
        # (§7.4.1 step 1) — repo uses self-managed session
        ticket = self._task_repo.find_ticket_by_notification_id(
            notification_id,
        )
        if not ticket:
            return ResolveResult(
                success=False,
                error="该治理工单不存在或已失效",
                error_code="NOT_FOUND",
                notification_id=notification_id,
            )

        # Resolve effective user ID from DB if empty (card_callback)
        effective_user_id = user_id or ticket.owner_id or ""
        effective_actor = actor_id or effective_user_id

        # §7.4.1 step 2: response not empty → duplicate ignored
        if ticket.user_feedback is not None and ticket.user_feedback != "":
            self._audit_repo.add_audit(
                f"feedback-{uuid.uuid4().hex[:8]}",
                ticket.bot_id,
                ticket.owner_id,
                notification_id=notification_id,
                actor_id=effective_user_id,
                action_taken=AuditAction.FEEDBACK_DUPLICATE_IGNORED,
                source=source,
                dry_run=0,
            )
            return _result_from_ticket(
                ticket,
                notification_id=notification_id,
                message="该治理工单已反馈过，无需重复提交",
            )

        # §7.4.1 step 3: terminal status → rejected
        if ticket.governance_status in _BLOCKED_STATUSES:
            self._audit_repo.add_audit(
                f"feedback-{uuid.uuid4().hex[:8]}",
                ticket.bot_id,
                ticket.owner_id,
                notification_id=notification_id,
                actor_id=effective_user_id,
                action_taken=AuditAction.FEEDBACK_TERMINAL_IGNORED,
                source=source,
                error_msg=f"status={ticket.governance_status}",
                dry_run=0,
            )
            return ResolveResult(
                success=False,
                error="该治理工单状态不允许反馈",
                error_code="INVALID_STATUS",
                ticket_id=(ticket.ticket_id or ""),
                notification_id=notification_id,
            )

        # §7.4.1 step 4: must be open + response empty
        # (scheduled/waiting_review already filtered above)
        if ticket.governance_status != GovernanceStatus.OPEN:
            return ResolveResult(
                success=False,
                error=f"Unexpected status: {ticket.governance_status}",
                error_code="INVALID_STATUS",
                ticket_id=(ticket.ticket_id or ""),
                notification_id=notification_id,
            )

        # Validate response value
        if response not in _FORMAL_RESPONSES:
            return ResolveResult(
                success=False,
                error=f"Invalid response: {response}",
                error_code="INVALID_RESPONSE",
                notification_id=notification_id,
            )

        # Dispute/whitelist require remark
        if response in (Response.DISPUTE, Response.WHITELIST) and not remark:
            return ResolveResult(
                success=False,
                error="Remark is required for dispute/whitelist",
                error_code="MISSING_REMARK",
                notification_id=notification_id,
            )

        # Need_time requires repair_deadline
        if response == Response.NEED_TIME and not repair_deadline:
            return ResolveResult(
                success=False,
                error="repair_deadline is required for need_time",
                error_code="MISSING_REPAIR_DEADLINE",
                notification_id=notification_id,
            )

        # Apply feedback (§7.4.2)
        now = datetime.now()
        target_status: str
        review_reason: str | None = None
        mute_until: datetime | None = None

        if response == Response.NEED_TIME:
            target_status = GovernanceStatus.SCHEDULED
            mute_until = repair_deadline + timedelta(
                days=self._config.cooldown_days,
            )
        else:
            target_status, review_reason = _RESPONSE_TRANSITION_MAP[response]

        # Build self-contained v2 feedback_payload (enrich on server side).
        # Enrichment reads ticket.notification_structured; degrades gracefully,
        # so an Invalid-feedback_payload error only fires on serialization.
        feedback_payload_json: str | None = None
        try:
            enriched = _build_enriched_payload(
                ticket=ticket,
                notification_id=notification_id,
                raw_response=response,
                remark=remark,
                repair_deadline=repair_deadline if response == Response.NEED_TIME else None,
                feedback_payload=feedback_payload,
                source=source,
                actor_id=effective_actor,
                now=now,
            )
            feedback_payload_json = json.dumps(enriched, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            log.warning("[GovernanceFeedback] enrich failed nid=%s: %s", notification_id, exc)
            return ResolveResult(
                success=False,
                error="Invalid feedback_payload JSON",
                error_code="INVALID_FEEDBACK_PAYLOAD",
                notification_id=notification_id,
            )

        # Advance ticket state via the driver service (sole driver). The
        # driver orchestrates: state transition (guard-activated) + cancel
        # pending notifies. Pre-business checks (one-time rule, response
        # validity, remark/deadline requirements) stay here in feedback_service.
        updated = self._lifecycle_svc.accept_feedback(
            ticket.ticket_id,
            user_feedback=response,
            feedback_at=now,
            feedback_source=source,
            target_status=target_status,
            feedback_remark=remark,
            repair_deadline=repair_deadline if response == Response.NEED_TIME else None,
            resume_at=mute_until if response == Response.NEED_TIME else None,
            review_reason=review_reason if response != Response.NEED_TIME else None,
            actor_id=effective_actor,
            feedback_payload=feedback_payload_json,
        )
        if not updated:
            return ResolveResult(
                success=False,
                error="该治理工单不存在或已失效",
                error_code="NOT_FOUND",
                notification_id=notification_id,
            )

        # Whitelist feedback → add to the whitelist table. Owned by
        # feedback_service (not the driver) to keep lifecycle_service free of
        # a whitelist_service dependency (breaks the whitelist↔lifecycle DI
        # cycle). Source & created_by carry the rich feedback semantics
        # (effective_user_id = owner; original source e.g. card_callback).
        if response == Response.WHITELIST:
            try:
                self._whitelist_service.add(
                    bot_id=ticket.bot_id,
                    owner_id=ticket.owner_id,
                    created_by=effective_user_id,
                    whitelist_type="governance",
                    source=source,
                )
            except Exception:
                log.exception(
                    "[GovernanceFeedback] Failed to add whitelist for bot_id=%s",
                    ticket.bot_id,
                )

        # Audit (§7.4.3) — feedback_service keeps its per-response audit
        # (USER_OPTIMIZED / USER_NEED_TIME / USER_DISPUTE / USER_WHITELIST),
        # which the driver does not duplicate.
        _RESPONSE_AUDIT_MAP: dict[str, str] = {
            Response.OPTIMIZED: AuditAction.USER_OPTIMIZED,
            Response.NEED_TIME: AuditAction.USER_NEED_TIME,
            Response.DISPUTE: AuditAction.USER_DISPUTE,
            Response.WHITELIST: AuditAction.USER_WHITELIST,
        }
        audit_action = _RESPONSE_AUDIT_MAP.get(response, response)
        self._audit_repo.add_audit(
            f"feedback-{uuid.uuid4().hex[:8]}",
            ticket.bot_id,
            ticket.owner_id,
            notification_id=notification_id,
            actor_id=effective_user_id,
            check_result="actionable",
            action_taken=audit_action,
            source=source,
            dry_run=0,
        )

        return ResolveResult(
            success=True,
            ticket_id=(ticket.ticket_id or ""),
            notification_id=notification_id,
            governance_status=target_status,
            close_reason=ticket.close_reason,
            mute_until=mute_until if response == Response.NEED_TIME else None,
            response=response,
            response_source=source,
        )

    # ------------------------------------------------------------------
    # List queries (list_pending/list_history/get_notification) 已删除:
    # 无真实用户主动调用,    治理反馈真入口是 card-callback(经 resolve)。完整移除于
    # admin-router-regroup Task 7。仅保留 resolve。
    # ------------------------------------------------------------------

    