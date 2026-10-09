"""Activation refreshes real vectors; failures preserve activation for retry."""

import pytest

from src.domain.models.vector_point import VectorPoint
from src.infra.config.feature_flags import FeatureFlags
from tests.fixtures.runtime_acceptance import acceptance_run
from tests.integration.test_isolated_runtime_acceptance import (
    TOKEN,
    isolated_app_factory,  # noqa: F401 - shared disposable app fixture
)


def _prepare(acceptance):
    worker_id = acceptance.create_worker()
    acceptance.request("PUT", f"/v1/workers/{worker_id}/online")
    acceptance.put_profile(worker_id)
    return worker_id, f"/v1/workers/{worker_id}/profiles/release/activate"


def _assert_active(registry, worker_id):
    assert registry.require("worker_profile_content_store").get_active(worker_id).profile_id == "release"
    assert registry.require("worker_registry_store").get_by_id(worker_id).active_profile_key == f"{worker_id}:release"
    assert registry.require("worker_profile_binding_store").get_active_binding(worker_id).profile_key == f"{worker_id}:release"


def test_activation_preserves_old_profile_vectors_and_repeat_is_searchable(isolated_app_factory):
    with isolated_app_factory() as (client, registry, _embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id, path = _prepare(acceptance)
            for _ in range(2):
                acceptance.request("PUT", path)
                _assert_active(registry, worker_id)
                matches = acceptance.search(worker_id)
                assert matches
                assert {item["profile_key"] for item in matches} == {
                    f"{worker_id}:default", f"{worker_id}:release",
                }


@pytest.mark.parametrize("failure", ["embedding", "empty_embedding"])
def test_activation_index_failure_is_durable_and_retryable(isolated_app_factory, monkeypatch, failure):
    with isolated_app_factory() as (client, registry, embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id, path = _prepare(acceptance)

            def fail(*_args, **_kwargs):
                raise RuntimeError("injected index failure")

            with monkeypatch.context() as patch:
                replacement = (lambda *_args: []) if failure == "empty_embedding" else fail
                patch.setattr(embedding, "embed", replacement)
                response = acceptance.request("PUT", path, expected=500)
            assert response["detail"]["code"] == "ACTIVATE_PROFILE_INDEX_ERROR"
            assert response["detail"]["activation_persisted"] is True
            assert response["detail"]["index_updated"] is False
            assert response["detail"]["retryable"] is True
            _assert_active(registry, worker_id)
            acceptance.request("PUT", path)
            assert {item["profile_key"] for item in acceptance.search(worker_id)} == {
                f"{worker_id}:default", f"{worker_id}:release",
            }


def test_activation_keeps_other_profiles_and_other_workers_vectors(isolated_app_factory):
    with isolated_app_factory() as (client, registry, _embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id, path = _prepare(acceptance)
            store = registry.require("vector_store")
            other_id = f"{worker_id}:nested:default:skills:1"
            active_id = f"{worker_id}:release:skills:1"
            stale_id = f"{worker_id}:default:skills:1"
            store.upsert([
                VectorPoint(id=vector_id, vector=[1.0] + [0.0] * 63, payload={
                    "worker_id": owner, "profile_key": profile_key,
                })
                for vector_id, owner, profile_key in (
                    (other_id, f"{worker_id}:nested", f"{worker_id}:nested:default"),
                    (active_id, worker_id, f"{worker_id}:release"),
                    (stale_id, worker_id, f"{worker_id}:default"),
                )
            ])
            try:
                acceptance.request("PUT", path)
                assert store.get(stale_id) is not None
                assert store.get(active_id) is not None
                assert store.get(other_id) is not None
            finally:
                store.delete(other_id)


def test_activation_without_index_feature_needs_no_embedding(isolated_app_factory, monkeypatch):
    with isolated_app_factory() as (client, registry, embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id, path = _prepare(acceptance)
            monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
            FeatureFlags.reset()
            calls_before = len(embedding.calls)
            acceptance.request("PUT", path)
            _assert_active(registry, worker_id)
            assert len(embedding.calls) == calls_before


def test_activation_keeps_offline_worker_undiscoverable(isolated_app_factory):
    with isolated_app_factory() as (client, registry, _embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id = acceptance.create_worker()
            acceptance.put_profile(worker_id)
            acceptance.request("PUT", f"/v1/workers/{worker_id}/profiles/release/activate")
            _assert_active(registry, worker_id)
            assert not acceptance.search(worker_id)
            store = registry.require("vector_store")
            payloads = [store.get(vector_id).payload for vector_id in store.get_vector_ids()]
            assert payloads
            assert all(payload["runtime_state"] == "offline" for payload in payloads)
