"""DISPATCH rationale builders + legacy JOIN drop-reason compatibility fields (REQ-2).

A small leaf module — no import from ``strategies.py`` at module load time — so
that ``strategies.py`` can ``from .rationale import _claim_product, _build_search_rationale``
without a circular import. The JOIN reason taxonomy + ``DispatchRationale`` builder
belong here (factored out of ``strategies.py`` per AGENTS.md's ≤1000-lines-per-file
constraint); the strategy just calls in with rule-mode/skill-mode inputs and receives
either a ``DispatchRationale`` or ``None`` (assembly is ``try/except``-safe — REQ-2 验收).

Compatibility note:
    * ``join_dropped`` and its historical reason values remain in the trajectory
      schema for readers of old events. Current dispatch never removes candidates
      through claim-join post-filtering; the filter-disabled visibility marker may
      still be emitted for trajectory compatibility, but it never affects routing.

Invariants:
    * ``_build_search_rationale`` is wrapped in ``try/except``: any sub-field
      raise (e.g. malformed ``recommend.score`` cannot ``float()``) → returns
      ``None`` + DEBUG log; never disables the dispatch forward-driving path.
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.task.task_context.task_trajectory.models import (
    DispatchCandidate,
    DispatchRationale,
    JoinDropped,
)

if TYPE_CHECKING:
    # Annotations only — never resolved at runtime (forward-ref stringized),
    # so this import does NOT create a circular dependency with strategies.py.
    from agentclaw.community.core.task.domain.models import TaskNode
    from agentclaw.community.core.task.task_dispatch.strategies import SearchResult

logger = logging.getLogger("task.dispatcher")

# Legacy trajectory taxonomy threshold retained for reading old rationale data.
_JOIN_SCORE_THRESHOLD = 0.5


def _claim_product(bot_id: str | None) -> str:
    """归一 bcs(``{p}:{o}``) / product(``{p}``) → product(首段)。

    Same shape as ``strategies._claim_product`` (kept here so this module is a
    leaf — no back-import into strategies at module load).
    """
    bid = (bot_id or "").strip()
    return bid.split(":", 1)[0] if bid else ""


def _classify_join_drop_reason(
    bot_id: str, candidate_by_product: dict[str, dict]
) -> str:
    """JOIN 滤除丢因微分类(REQ-7) — by most specific signal:

    * bot NOT in catalog (prefetch candidates) → ``catalog_miss``.
    * bot in catalog AND ``recommend.score < _JOIN_SCORE_THRESHOLD`` →
      ``score_below_threshold``.
    * bot in catalog AND score sufficient (or no score field) but not in
      claim_on roster → ``claim_mode_off`` (default existing reason).

    Does NOT mutate ``unauthorized_bots[].reason`` (kept ``claim_mode_off`` for
    dashboard backward compatibility); only used to fill
    ``DispatchRationale.join_dropped[].reason``.
    """
    prod = _claim_product(bot_id)
    cand = candidate_by_product.get(prod)
    if cand is None:
        return "catalog_miss"
    score = (cand.get("recommend") or {}).get("score")
    if score is not None:
        try:
            if float(score) < _JOIN_SCORE_THRESHOLD:
                return "score_below_threshold"
        except (TypeError, ValueError):
            # Malformed score (non-numeric type): treat as no score signal —
            # fall through to ``claim_mode_off`` rather than guess.
            return "claim_mode_off"
    return "claim_mode_off"


def _summarize_join_drop(
    sr: "SearchResult", candidates: list[dict], filter_ran: bool
) -> tuple[bool, list[JoinDropped]]:
    """For ``DispatchRationale``: ``(join_filter_applied, join_dropped)``.

    * ``filter_ran=True`` (gate enabled + bcn present, name roster fetched non-empty
      w/o exc): each dropped bot in ``sr.unauthorized_bots`` gets its reason
      classified by catalog/score (catalog_miss / score_below_threshold /
      claim_mode_off).
    * ``filter_ran=False`` (gate off / bcn absent): the bot is NOT dropped from
      ``sr`` (existing fail-open passthrough). For trajectory visibility we still
      flag search-result bots that are NOT in the prefetch catalog as
      ``claim_filter_disabled`` (would have been ``catalog_miss`` if the filter
      were on). Fire-and-forget — does not affect dispatch.
    """
    candidate_by_product: dict[str, dict] = {}
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        prod = _claim_product(c.get("bot_id"))
        if prod:
            candidate_by_product.setdefault(prod, c)

    if filter_ran:
        join_dropped: list[JoinDropped] = []
        for e in (sr.unauthorized_bots or []):
            bid = e.get("bot_id") or ""
            if not bid:
                continue
            join_dropped.append(
                JoinDropped(
                    bot_id=bid,
                    reason=_classify_join_drop_reason(bid, candidate_by_product),
                )
            )
        return True, join_dropped

    # filter off → no actual drop; flag would-be catalog-miss picks for visibility.
    # ``SearchOutcome`` is a StrEnum, so ``sr.outcome == "HIT_SINGLE"`` compares
    # correctly without importing SearchOutcome (avoids a back-import cycle).
    sr_bot_ids: list[str] = []
    if sr.outcome == "HIT_SINGLE" and sr.bot_id:
        sr_bot_ids = [sr.bot_id]
    elif sr.outcome == "HIT_MULTI_BOTS" and sr.group_formation is not None:
        sr_bot_ids = list(sr.group_formation.bot_ids or [])

    join_dropped = [
        JoinDropped(bot_id=bid, reason="claim_filter_disabled")
        for bid in sr_bot_ids
        if _claim_product(bid) and _claim_product(bid) not in candidate_by_product
    ]
    return False, join_dropped


def _extract_skill_response_content(run: Any) -> str:
    """Extract the LLM search-skill response content string from a round-trip run.

    ``run = {"status": "COMPLETED", "result": {"content": "...JSON..."}}``. Empty
    string for non-dict or missing content (digest below still computes SHA-256
    of empty — a well-formed 64-char hex digest).
    """
    if not isinstance(run, dict):
        return ""
    result = run.get("result")
    if isinstance(result, dict):
        return str(result.get("content") or "")
    return str(result) if result is not None else ""


def _build_search_rationale(
    *,
    node: "TaskNode",
    candidates: list[dict],
    sr: "SearchResult",
    use_skill: bool,
    prompt_text: str | None,
    response_text: str | None,
    filter_ran: bool,
    prefetch_tokens: list[str],
) -> DispatchRationale | None:
    """Build the ``DispatchRationale`` for ``SearchBasedDispatchStrategy.apply`` (REQ-2).

    Wraps the whole build in ``try/except``: any sub-field raise (e.g. a
    malformed ``recommend.score`` cannot ``float()``) → return ``None`` + DEBUG
    log (REQ-2 验收). The strategy then propagates ``None`` to ``sr.rationale`` —
    the dispatcher writes no ``_dispatch_rationale`` key and the engine DISPATCH
    gate still fires with ``ext_info=None``. Rationale assembly NEVER breaks the
    dispatch path.

    ``prefetch_tokens`` is **caller-supplied** (computed in strategies via
    ``_prefetch_tokens``) so this module stays leaf (no back-import).
    """
    try:
        # candidates: from ``_prefetch_candidates`` actual hits (REQ-2).
        cand_list: list[DispatchCandidate] = []
        for c in candidates or []:
            if not isinstance(c, dict):
                continue
            bid = c.get("bot_id")
            if not bid:
                continue
            rec = c.get("recommend") or {}
            score = rec.get("score")
            # float(score): non-numeric values (str/list/dict) raise here;
            # outer try/except → None (REQ-2 验收 defensive-assembly test path).
            score_f = float(score) if score is not None else 0.0
            short_profile = str(rec.get("short_profile") or "")
            cand_list.append(
                DispatchCandidate(
                    bot_id=str(bid),
                    recommend_score=score_f,
                    short_profile=short_profile,
                )
            )

        join_filter_applied, join_dropped = _summarize_join_drop(
            sr, candidates, filter_ran
        )

        # skill digests only when use_skill AND a prompt was actually sent
        # (None for rule mode / MISS(no_candidates) / direct / replay / bbs).
        skill_prompt_digest: str | None = None
        skill_response_digest: str | None = None
        if use_skill and prompt_text:
            skill_prompt_digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
            skill_response_digest = hashlib.sha256(
                (response_text or "")[:500].encode("utf-8")
            ).hexdigest()

        return DispatchRationale(
            strategy_name="search",
            decision_mode="skill" if use_skill else "rule",
            candidates=cand_list,
            prefetch_tokens=list(prefetch_tokens),
            join_filter_applied=join_filter_applied,
            join_dropped=join_dropped,
            skill_prompt_digest=skill_prompt_digest,
            skill_response_digest=skill_response_digest,
        )
    except Exception as ex:  # noqa: BLE001  rationale 装配失败 → None,派发继续
        logger.debug(
            "[task][dispatch][rationale] search strategy node=%s 装配失败: %s",
            getattr(node, "node_id", "?"),
            ex,
        )
        # 降级原因回填到 ``sr``(派生决策未失败,仅 rationale 丢):dispatcher 透传到节点
        # ``_dispatch_failure`` carrier,引擎 DISPATCH hit/miss 闸门据此在事件 ``ext_info``
        # 追加可见性备注。返回值仍 None(→ ``sr.rationale=None``,派发照常;仅多一条诊断留痕)。
        # SearchResult 非 frozen → 普通属性赋值不抛(若 sr 非 SearchResult 实例则被外层吞,无副作用)。
        try:
            sr.assembly_error = f"rationale_assembly_failed:{type(ex).__name__}"
        except Exception:  # noqa: BLE001  sr 不可写(非预期类型)→ 放弃备注,仅返回 None
            pass
        return None
