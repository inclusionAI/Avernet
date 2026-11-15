"""Current registry state reaches both Fuse consumers through the shared TC search."""

from app.adapters.http.dependencies import fusion_dependencies as dependencies
from app.domain.models.retrieval_mode import RetrievalMode
from tests.contract.test_tc_catalog import runtime as tc_runtime, sync  # noqa: F401


def test_state_update_invalidates_both_existing_fuse_consumer_results(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client, "first:owner")
    sync(client, "second:owner")
    candidates = dependencies.get_candidate_recommendation_service()
    profiles = dependencies.get_profile_retrieval_service()
    assert len(candidates.recommend("MySQL").recommendations) == 2
    assert len(profiles.retrieve("MySQL", RetrievalMode.EXPERT_DIAGNOSIS).results) == 2
    client.put("/v1/workers/first:owner/offline").raise_for_status()
    assert [r.worker_id for r in candidates.recommend("MySQL").recommendations] == ["second:owner"]
    assert [r.profile.staff_id for r in profiles.retrieve("MySQL", RetrievalMode.EXPERT_DIAGNOSIS).results] == ["second:owner"]


def test_new_profile_parameter_does_not_restore_a_second_current_profile(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    sync(client, description="Updated release expertise", profile={"profile_id": "another", "activate": False})
    retrieval = dependencies.get_profile_retrieval_service()
    active = retrieval.retrieve(
        "release", RetrievalMode.EXPERT_DIAGNOSIS,
        profile_keys=["bot:owner:legacy"], strict_participants=True,
    )
    assert [r.profile.profile_key for r in active.results] == ["bot:owner:legacy"]
    assert "Updated release expertise" in active.results[0].profile.searchable_text
    unknown = retrieval.retrieve(
        "release", RetrievalMode.EXPERT_DIAGNOSIS,
        profile_keys=["bot:owner:another"], strict_participants=True,
    )
    assert unknown.results == []


def test_explicit_participant_remains_separate_from_supplement_visibility_policy(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client, availability="private", runtime_state="offline")
    candidates = dependencies.get_candidate_recommendation_service()
    assert candidates.recommend("MySQL").recommendations == []
    explicit = candidates.recommend("MySQL", participants=["bot:owner"], strict_participants=True)
    assert [candidate.worker_id for candidate in explicit.recommendations] == ["bot:owner"]
    assert explicit.recommendations[0].is_supplement is False
