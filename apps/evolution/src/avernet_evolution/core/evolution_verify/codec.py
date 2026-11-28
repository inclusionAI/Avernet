"""Verdict 的持久化编解码（07 §2.6）。

判定要跨进程重启可读：`verify` 的幂等性是按 (候选, 配置) 定义的，内存字典在
重启后归零会让同一个候选被重新评测一遍。这里只编解码判定本身，不编解码 rollout
明细（那些属于评估记录，另行存储）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .enums import OverfitFlag, SanityResult, Split, VerdictReasonCategory, VerdictStatus
from .models import CostComparison, FloorRef, SplitResult, Verdict

__all__ = ["verdict_from_json", "verdict_to_json"]


def _split_result(payload: dict[str, Any]) -> SplitResult:
    ci = payload.get("ci")
    return SplitResult(
        cases=int(payload.get("cases") or 0),
        seeds=int(payload.get("seeds") or 0),
        mean_delta=payload.get("mean_delta"),
        ci=(float(ci[0]), float(ci[1])) if ci else None,
        win_rate=payload.get("win_rate"),
        newly_failing=list(payload.get("newly_failing") or []),
    )


def verdict_to_json(verdict: Verdict) -> str:
    payload = {
        "candidate": verdict.candidate,
        "bot_owner_id": verdict.bot_owner_id,
        "bot_id": verdict.bot_id,
        "run_id": verdict.run_id,
        "revision": verdict.revision,
        "parent_revision": verdict.parent_revision,
        "profile": verdict.profile,
        "status": verdict.status.value,
        "floor": {
            "passed": verdict.floor.passed,
            "gate_decision_ref": verdict.floor.gate_decision_ref,
        },
        "sanity": verdict.sanity.value,
        "splits": {
            split.value: {
                "cases": result.cases,
                "seeds": result.seeds,
                "mean_delta": result.mean_delta,
                "ci": list(result.ci) if result.ci else None,
                "win_rate": result.win_rate,
                "newly_failing": list(result.newly_failing),
            }
            for split, result in verdict.splits.items()
        },
        "judge_agreement": verdict.judge_agreement,
        "overfit_flags": [flag.value for flag in verdict.overfit_flags],
        "tolerance_used": verdict.tolerance_used,
        "cost": {
            "parent_usd_per_case": verdict.cost.parent_usd_per_case,
            "candidate_usd_per_case": verdict.cost.candidate_usd_per_case,
            "increase_pct": verdict.cost.increase_pct,
            "wins_by_spending": verdict.cost.wins_by_spending,
        },
        "evaluations": list(verdict.evaluations),
        "verifier": dict(verdict.verifier),
        "reasons": list(verdict.reasons),
        "reasons_categories": [category.value for category in verdict.reasons_categories],
        "decided_at": verdict.decided_at.isoformat() if verdict.decided_at else None,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def verdict_from_json(text: str) -> Verdict:
    payload = json.loads(text)
    decided = payload.get("decided_at")
    return Verdict(
        candidate=payload["candidate"],
        bot_owner_id=payload["bot_owner_id"],
        bot_id=payload["bot_id"],
        run_id=payload["run_id"],
        revision=payload.get("revision"),
        parent_revision=payload["parent_revision"],
        profile=payload["profile"],
        status=VerdictStatus(payload["status"]),
        floor=FloorRef(
            passed=bool(payload["floor"]["passed"]),
            gate_decision_ref=payload["floor"].get("gate_decision_ref"),
        ),
        sanity=SanityResult(payload.get("sanity") or "not_run"),
        splits={Split(name): _split_result(value) for name, value in (payload.get("splits") or {}).items()},
        judge_agreement=payload.get("judge_agreement"),
        overfit_flags=tuple(OverfitFlag(flag) for flag in payload.get("overfit_flags") or ()),
        tolerance_used=bool(payload.get("tolerance_used")),
        cost=CostComparison(**payload.get("cost") or {}),
        evaluations=tuple(payload.get("evaluations") or ()),
        verifier=dict(payload.get("verifier") or {}),
        reasons=tuple(payload.get("reasons") or ()),
        reasons_categories=tuple(
            VerdictReasonCategory(item) for item in payload.get("reasons_categories") or ()
        ),
        decided_at=datetime.fromisoformat(decided) if decided else None,
    )
