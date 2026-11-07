"""Strict mode scopes explicit participants; an omitted list still permits recall."""

from app.adapters.http.dependencies import fusion_dependencies as dependencies
from tests.contract.test_tc_catalog import runtime as tc_runtime, sync  # noqa: F401


def test_strict_mode_without_explicit_participants_allows_shared_retrieval(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    response = dependencies.get_candidate_recommendation_service().recommend(
        "MySQL", participants=None, strict_participants=True,
    )
    assert [candidate.worker_id for candidate in response.recommendations] == ["bot:owner"]
    assert response.recommendations[0].is_supplement is True
