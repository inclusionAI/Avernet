"""Retiring the broken AGENT experiment must preserve its legacy fallback."""

from types import SimpleNamespace

import pytest

from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.worker_profile import (
    ProfileType,
    WorkerProfile,
    WorkerProfileScanResult,
)
from src.domain.services.worker_profile_retrieval_service import (
    RetrievalResult,
    WorkerProfileRetrievalService,
)
from src.infra.config.feature_flags import FeatureFlags
from src.infra.embedding.providers.real_provider import RealEmbeddingProvider


@pytest.fixture
def service(monkeypatch):
    profiles = [
        WorkerProfile(
            staff_id=worker,
            profile_id="default",
            profile_type=kind,
            source_root="test-fixture",
            searchable_text="python",
        )
        for worker, kind in [("bot", ProfileType.BOT), ("owner", ProfileType.DEFAULT)]
    ]
    source = SimpleNamespace(scan=lambda: WorkerProfileScanResult(profiles=profiles))
    return WorkerProfileRetrievalService(source=source)


@pytest.mark.parametrize("v2_enabled", [False, True])
def test_retired_hybrid_flag_preserves_legacy_scores_without_embedding(
    monkeypatch, service, v2_enabled
):
    monkeypatch.setenv("ENABLE_HYBRID_RETRIEVAL", "true")
    monkeypatch.setenv("ENABLE_G1_PROFILE_RERANK", str(v2_enabled).lower())
    FeatureFlags.reset()
    embedding_attempts = []

    def unavailable_embedding(self):
        embedding_attempts.append(True)
        raise RuntimeError(
            "embedding must not be constructed for compatibility scoring"
        )

    monkeypatch.setattr(RealEmbeddingProvider, "__init__", unavailable_embedding)
    result = service.retrieve(question="python", mode=RetrievalMode.AGENT)

    assert [item.profile.profile_key for item in result.results] == [
        "owner:default",
        "bot:default",
    ]
    # Searchable text contributes .15; DEFAULT adds .5 * .10.
    assert [item.total_score for item in result.results] == pytest.approx([0.20, 0.15])
    assert [item.rank for item in result.results] == [1, 2]
    assert embedding_attempts == []


@pytest.mark.parametrize(
    "min_score,top_k,keys",
    [
        (0.0, 1, ["owner:default"]),
        (0.16, 10, ["owner:default"]),
        (0.21, 10, []),
    ],
)
def test_compatibility_scoring_preserves_threshold_and_limit(
    monkeypatch, service, min_score, top_k, keys
):
    monkeypatch.setenv("ENABLE_HYBRID_RETRIEVAL", "true")
    FeatureFlags.reset()
    result = service.retrieve(
        question="python", mode=RetrievalMode.AGENT, min_score=min_score, top_k=top_k
    )
    assert [item.profile.profile_key for item in result.results] == keys


@pytest.mark.parametrize(
    "requested,expected",
    [
        (["bot:default"], ["bot:default"]),
        (["missing:default"], []),
        (["owner:default", "bot:default"], ["bot:default"]),
    ],
)
def test_compatibility_scoring_cannot_restore_filtered_profiles(
    monkeypatch, service, requested, expected
):
    monkeypatch.setenv("ENABLE_HYBRID_RETRIEVAL", "true")
    FeatureFlags.reset()
    service._profile_filter = SimpleNamespace(
        filter_profiles=lambda profiles: [p for p in profiles if p.staff_id == "bot"]
    )
    result = service.retrieve(
        question="python",
        mode=RetrievalMode.AGENT,
        profile_keys=requested,
        strict_participants=True,
    )
    assert [item.profile.profile_key for item in result.results] == expected


def test_explicit_v2_selection_without_retired_flag_is_preserved(monkeypatch, service):
    monkeypatch.setenv("ENABLE_HYBRID_RETRIEVAL", "false")
    monkeypatch.setenv("ENABLE_G1_PROFILE_RERANK", "true")
    FeatureFlags.reset()

    # This test pins dispatch, not V2's independently tested scoring algorithm.
    def v2_scores(**kwargs):
        return [RetrievalResult(profile=kwargs["profiles"][0], total_score=0.77)]

    monkeypatch.setattr(service, "_calculate_v2_scores", v2_scores)
    result = service.retrieve(question="python", mode=RetrievalMode.AGENT)
    assert [(r.profile.profile_key, r.total_score) for r in result.results] == [
        ("bot:default", 0.77)
    ]
