"""Legacy min_score is accepted without restoring mode-specific scoring."""

from app.adapters.http.dependencies import fusion_dependencies as dependencies
from app.domain.models.retrieval_mode import RetrievalMode
from tests.contract.test_tc_catalog import runtime as tc_runtime  # noqa: F401
from tests.contract.test_tc_catalog import sync


def test_compatibility_threshold_cannot_remove_current_candidates(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    service = dependencies.get_profile_retrieval_service()
    for min_score in (0.0, 0.99):
        response = service.retrieve("MySQL", RetrievalMode.AGENT, top_k=1, min_score=min_score)
        assert [result.profile.staff_id for result in response.results] == ["bot:owner"]


def test_compatibility_threshold_cannot_restore_filtered_profiles(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client, runtime_state="offline")
    response = dependencies.get_profile_retrieval_service().retrieve(
        "MySQL", RetrievalMode.AGENT, min_score=0.0,
    )
    assert response.results == []
