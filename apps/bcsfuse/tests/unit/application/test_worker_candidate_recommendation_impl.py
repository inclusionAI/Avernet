"""Candidate behavior retained after the legacy scoring implementation retired."""

import math

import pytest
from app.adapters.http.dependencies import fusion_dependencies as dependencies
from app.domain.models.retrieval_mode import RetrievalMode

from tests.contract.test_tc_catalog import runtime as tc_runtime  # noqa: F401
from tests.contract.test_tc_catalog import sync


def test_explicit_candidate_keeps_priority_and_supplements_are_marked(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client, "first:owner")
    sync(client, "second:owner")
    response = dependencies.get_candidate_recommendation_service().recommend(
        "MySQL", participants=["first:owner"], max_candidates=2,
    )
    assert [candidate.worker_id for candidate in response.recommendations] == ["first:owner", "second:owner"]
    assert [candidate.is_supplement for candidate in response.recommendations] == [False, True]


def test_worker_and_current_profile_alias_do_not_duplicate_candidates(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    response = dependencies.get_candidate_recommendation_service().recommend(
        "MySQL", participants=["bot:owner", "bot:owner:legacy"], strict_participants=True,
    )
    assert [candidate.profile_key for candidate in response.recommendations] == ["bot:owner:legacy"]
    assert response.recommendations[0].is_supplement is False


def test_strict_participants_never_add_supplements(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client, "first:owner")
    sync(client, "second:owner")
    response = dependencies.get_candidate_recommendation_service().recommend(
        "MySQL", participants=["first:owner", "missing"], strict_participants=True,
    )
    assert [candidate.worker_id for candidate in response.recommendations] == ["first:owner"]
    assert not any(candidate.is_supplement for candidate in response.recommendations)


def test_max_candidates_bounds_current_worker_results(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    for worker in ("first", "second", "third"):
        sync(client, worker)
    response = dependencies.get_candidate_recommendation_service().recommend("MySQL", max_candidates=2)
    assert len(response.recommendations) == response.selected_candidates == 2
    assert all(math.isfinite(candidate.score) for candidate in response.recommendations)


@pytest.mark.parametrize("mode", list(RetrievalMode))
def test_modes_keep_the_same_current_retrieval_contract(tc_runtime, mode):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    response = dependencies.get_candidate_recommendation_service().recommend("MySQL", mode=mode)
    assert response.mode == mode
    assert [candidate.worker_id for candidate in response.recommendations] == ["bot:owner"]
    assert response.metadata["candidate_source"] == "tc_card"


def test_shared_rerank_failure_preserves_candidates_and_reports_degradation(tc_runtime):  # noqa: F811
    client, registry = tc_runtime
    sync(client)
    baseline = client.post("/api/v1/recommend", json={"question": "MySQL", "enable_rerank": False}).json()

    class UnavailableReranker:
        calls = 0

        def rerank(self, query, candidates, top_k=5):
            self.calls += 1
            raise RuntimeError("controlled reranker outage")

    model = UnavailableReranker()
    registry.require("tc_search").reranker = model
    response = client.post("/api/v1/recommend", json={"question": "MySQL", "enable_rerank": True})
    assert response.status_code == 200
    degraded = response.json()
    assert model.calls == 1
    assert [row["worker_id"] for row in degraded["recommendations"]] == [row["worker_id"] for row in baseline["recommendations"]]
    assert degraded["metadata"]["reasons"]
