"""Defaults, explicit requests and emergency permissions remain independent."""

import json
from itertools import product

import pytest
from search_core.application.emergency import (
    EmergencyControl,
    EmergencyPolicy,
    RetrievalSwitches,
)


def control(*, fallback=True):
    def variants(policy):
        allowed = tuple(policy.retrieval.pipeline_switches().values())
        return {
            bits: object()
            for bits in product((False, True), repeat=3)
            if not (bits[2] and not any(bits[:2]))
            and all(not bit or permit for bit, permit in zip(bits, allowed))
        }

    return EmergencyControl(
        EmergencyPolicy(
            max_inflight_requests=32,
            retrieval=RetrievalSwitches(
                keyword_enabled=True, vector_enabled=True, rerank_enabled=True
            ),
            keyword_timeout_ms=100,
            embedding_timeout_ms=200,
            reranker_timeout_ms=3000,
            card_metadata_timeout_ms=50,
        ),
        lambda p: object(),
        build_variants=variants,
        request_defaults=RetrievalSwitches(
            keyword_enabled=True,
            vector_enabled=True,
            rerank_enabled=False,
            empty_result_fallback_enabled=fallback,
        ),
    )


def switches(snapshot):
    return tuple(snapshot.policy.retrieval.pipeline_switches().values())


def test_defaults_request_emergency_and_recovery():
    c = control()
    assert switches(c.for_request({})) == (True, True, False)
    assert c.update_retrieval({"vector_enabled": False, "embedding_timeout_ms": 100})
    assert switches(c.for_request({})) == (True, False, False)
    assert switches(c.for_request({"vector_enabled": True})) == (True, True, False)
    assert c.for_request({}).policy.embedding_timeout_ms == 100
    assert c.update({"retrieval": {"vector_enabled": False}})
    assert switches(c.for_request({"vector_enabled": True})) == (True, False, False)
    assert c.update({})
    assert switches(c.for_request({})) == (True, False, False)
    assert switches(c.for_request({"vector_enabled": True})) == (True, True, False)
    assert c.update({"retrieval": {"keyword_enabled": False, "vector_enabled": False}})
    assert switches(c.for_request({"rerank_enabled": True})) == (False, False, False)
    assert c.update({})
    assert c.update_retrieval({})
    assert switches(c.for_request({})) == (True, True, False)
    assert c.for_request({}).policy.embedding_timeout_ms == 200


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "broken",
        [],
        {"vector_enabled": "false"},
        {"max_inflight_requests": 0},
        {"max_inflight_requests": True},
        {"keyword_timeout_ms": 0},
        {"embedding_timeout_ms": 4001},
        {"card_metadata_timeout_ms": True},
        {"cache": {}},
        {"retrieval": {}},
    ],
)
def test_bad_defaults_retain_last_valid_snapshot(bad):
    c = control()
    assert c.update_retrieval({"vector_enabled": False})
    before = c.current
    assert not c.update_retrieval(bad)
    assert c.current is before


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {"max_inflight_requests": 32},
        {"embedding_timeout_ms": 100},
        {"retrieval": {"vector_enabled": 0}},
        {"retrieval": {"vector_enabled": "false"}},
        {"retrieval": None},
        {"retrieval": {"vector_enable": False}},
        {"retrieval": {"vector_enabled": False}, "unknown": 1},
    ],
)
def test_emergency_only_accepts_three_permissions(bad):
    c = control()
    assert c.update({"retrieval": {"vector_enabled": False}})
    before = c.current
    assert not c.update(bad)
    assert c.current is before


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("normal_config_pushed", [False, True])
def test_old_emergency_preserves_denials_without_overwriting_normal_config(
    encoded, normal_config_pushed
):
    c = control()
    if normal_config_pushed:
        assert c.update_retrieval(
            {"embedding_timeout_ms": 750, "max_inflight_requests": 12}
        )
    assert c.update_cache({"card_metadata": {"ttl_ms": 1234}})
    before = c.current.policy.model_dump(exclude={"retrieval"})
    raw = {
        "max_inflight_requests": 32,
        "retrieval": {
            "keyword_enabled": True,
            "vector_enabled": False,
            "rerank_enabled": False,
        },
        "embedding_timeout_ms": 100,
        "reranker_timeout_ms": 300,
    }
    original = json.dumps(raw, sort_keys=True)
    assert c.update(json.dumps(raw) if encoded else raw)
    assert switches(c.for_request({})) == (True, False, False)
    assert switches(
        c.for_request({"vector_enabled": True, "rerank_enabled": True})
    ) == (True, False, False)
    assert c.current.policy.model_dump(exclude={"retrieval"}) == before
    assert json.dumps(raw, sort_keys=True) == original
    assert c.update_retrieval({"vector_enabled": True})
    assert switches(c.for_request({})) == (True, False, False)
    assert c.update({"retrieval": {"vector_enabled": True}})
    assert switches(c.for_request({})) == (True, True, False)


def test_full_old_policy_ignores_fallback_and_cache_but_keeps_all_off():
    c = control()
    raw = c.current.policy.model_dump()
    raw["retrieval"] = dict.fromkeys(raw["retrieval"], False)
    raw["cache"]["enabled"] = False
    assert c.update(raw)
    assert switches(
        c.for_request(
            dict.fromkeys(("keyword_enabled", "vector_enabled", "rerank_enabled"), True)
        )
    ) == (False, False, False)
    assert c.current.policy.retrieval.empty_result_fallback_enabled is True
    assert c.current.policy.cache.enabled is True


def test_cache_and_bound_request_survive_independent_updates():
    c = control()
    assert c.update_retrieval({"vector_enabled": False})
    previous = c.for_request({})
    assert c.update_cache({"card_metadata": {"ttl_ms": 1234}})
    assert switches(c.for_request({})) == (True, False, False)
    assert c.update_retrieval({"vector_enabled": True})
    assert c.current.policy.cache.card_metadata.ttl_ms == 1234
    assert c.update({"retrieval": {"keyword_enabled": False}})
    with c.bind(previous):
        assert switches(c.for_request({})) == (True, False, False)
        assert c.snapshot() is previous
    assert switches(c.for_request({})) == (False, True, False)


def test_legacy_control_retains_tc_semantics():
    policy = control().current.policy
    c = EmergencyControl(policy, lambda p: object())
    assert c.update(policy.model_dump())
    assert not c.update({"retrieval": {"vector_enabled": False}})
    assert c.current.policy == policy


@pytest.mark.parametrize("enabled", [False, True])
def test_startup_fallback_default_does_not_require_a_config_callback(enabled):
    c = control(fallback=enabled)
    assert c.for_request({}).policy.retrieval.empty_result_fallback_enabled is enabled
    assert c.update({"retrieval": {"vector_enabled": False}})
    assert c.for_request({}).policy.retrieval.empty_result_fallback_enabled is enabled
    assert c.update_retrieval({"empty_result_fallback_enabled": not enabled})
    assert (
        c.for_request({}).policy.retrieval.empty_result_fallback_enabled is not enabled
    )
    assert c.update_retrieval({})
    assert c.for_request({}).policy.retrieval.empty_result_fallback_enabled is enabled
