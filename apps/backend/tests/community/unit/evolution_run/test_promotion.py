"""Gate regressions use the sole canonical policy; decisions are not Apply.

Old Backend Promotion mutated refs during decide/approve and allowed drift/stale
parent overrides. Those retired semantics must not be reintroduced. Delivery,
CAS, drift and manual authorization are exercised by the canonical manual suite
and Backend bot_genome promotion tests rather than an in-memory legacy service.
"""

from types import SimpleNamespace

import pytest
from avernet_evolution.core.evolution_promote import (
    CandidateFacts,
    GateOutcome,
    RiskTier,
    classify,
    decide,
)


@pytest.mark.parametrize(
    ("ops", "expected"),
    [
        ([], RiskTier.T0),
        ([{"op": "memory.update"}], RiskTier.T1),
        ([{"op": "memory.update"}, {"op": "persona.add"}], RiskTier.T2),
        (
            [
                {
                    "op": "file.edit",
                    "target": "skills/x/SKILL.md",
                    "description_only": True,
                }
            ],
            RiskTier.T2,
        ),
        ([{"op": "memory.set_mode", "mode": "replace"}], RiskTier.T3),
        ([{"op": "unknown"}], RiskTier.T2),
    ],
)
def test_canonical_risk_is_conservative(ops, expected):
    # No legacy description-only downgrade; uncertainty must not bypass review.
    assert classify(SimpleNamespace(ops=ops)) is expected


@pytest.mark.parametrize(
    ("changes", "ceiling", "expected"),
    [
        ({}, RiskTier.T1, GateOutcome.AUTO_PROMOTE),
        ({"risk_tier": RiskTier.T2}, RiskTier.T1, GateOutcome.NEEDS_REVIEW),
        ({"risk_tier": RiskTier.T3}, RiskTier.T3, GateOutcome.NEEDS_REVIEW),
        ({}, None, GateOutcome.NEEDS_REVIEW),
        ({"verdict_status": "reject"}, RiskTier.T1, GateOutcome.NOT_PROMOTABLE),
        ({"verdict_status": "inconclusive"}, RiskTier.T1, GateOutcome.NOT_PROMOTABLE),
        (
            {"regression_failures": 1, "regression_tolerance": 1},
            RiskTier.T1,
            GateOutcome.NEEDS_REVIEW,
        ),
        ({"safety_failures": 1}, RiskTier.T1, GateOutcome.NOT_PROMOTABLE),
        ({"frozen": True}, RiskTier.T1, GateOutcome.NEEDS_REVIEW),
        ({"incident": True}, RiskTier.T1, GateOutcome.NEEDS_REVIEW),
    ],
)
def test_canonical_gate_preserves_review_and_safety(changes, ceiling, expected):
    facts = dict(candidate_id="cid", risk_tier=RiskTier.T1, verdict_status="accept")
    assert (
        decide(CandidateFacts(**(facts | changes)), ceiling=ceiling).outcome is expected
    )
