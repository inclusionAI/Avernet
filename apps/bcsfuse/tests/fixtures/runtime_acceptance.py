"""Shared HTTP acceptance against a disposable or explicitly selected deployment.

Only UUID-named resources created by this run may be removed. No model-quality
or process-restart claim is made by these HTTP checks.
"""

from contextlib import contextmanager
from uuid import uuid4


class RuntimeAcceptance:
    def __init__(self, client, token):
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}
        self.worker_ids = []

    def request(self, method, path, expected=200, **kwargs):
        response = self.client.request(method, path, headers=self.headers, **kwargs)
        assert response.status_code == expected, (
            f"{method} {path}: expected {expected}, got {response.status_code}"
        )
        return response.json() if response.content else None

    def cleanup(self):
        errors = []
        for worker_id in reversed(self.worker_ids):
            failure = ""
            for _attempt in range(2):
                try:
                    response = self.client.delete(f"/v1/workers/{worker_id}", headers=self.headers)
                    if response.status_code in (200, 404):
                        failure = ""
                        break
                    failure = f"{worker_id}: HTTP {response.status_code}"
                    if response.status_code < 500:
                        break
                except Exception as error:
                    failure = f"{worker_id}: {type(error).__name__}"
            if failure:
                errors.append(failure)
        assert not errors, "Acceptance cleanup failed for owned IDs: " + "; ".join(errors)

    def create_worker(self):
        worker_id = f"acceptance_{uuid4().hex}:owner"
        # Record before POST so a lost response still permits scoped cleanup.
        self.worker_ids.append(worker_id)
        created = self.request("POST", "/v1/workers", expected=201, json={
            "id": worker_id, "type": "bot", "name": "Acceptance Python Expert",
            "responsibilities": ["Python testing"],
            "capabilities": [{"name": "python", "level": "expert"}],
            "availability": "public", "trust_level": "trusted",
        })
        assert created["id"] == worker_id
        assert created["runtime_state"] == "offline"
        return worker_id

    def put_profile(self, worker_id, profile_id="release"):
        result = self.request("PUT", f"/v1/workers/{worker_id}/profiles/{profile_id}", json={
            "display_name": "Acceptance profile", "soul_md": "Python testing specialist",
            "contents": {"profile": "Python testing specialist", "capabilities": "python"},
        })
        assert result["profile_id"] == profile_id
        return result

    def search(self, worker_id):
        result = self.request("POST", "/api/v1/search", json={
            "query": worker_id, "top_k": 10, "mode": "auto", "min_score": 0,
            "filters": {"worker_id": worker_id},
        })
        return result["results"]


@contextmanager
def acceptance_run(client, token):
    acceptance = RuntimeAcceptance(client, token)
    try:
        yield acceptance
    finally:
        acceptance.cleanup()


def exercise_lifecycle(acceptance):
    """Create, activate, discover, mutate runtime state, sync draft, and delete."""
    assert acceptance.request("GET", "/health")["status"] == "ok"
    assert acceptance.request("GET", "/ready")["ready"] is True
    worker_id = acceptance.create_worker()
    worker_url = f"/v1/workers/{worker_id}"
    assert acceptance.request("GET", worker_url)["id"] == worker_id

    # Gateway batch config and internal legacy aliases are part of the contract.
    configs = acceptance.request("POST", "/v1/workers/config/batch", json={
        "worker_ids": [worker_id, "acceptance_missing_" + uuid4().hex],
    })
    assert configs["data"][worker_id] == {"fusion_enable": False}
    assert len(configs["not_found_ids"]) == 1
    online = acceptance.request("PUT", f"/api/v1/workers/{worker_id}/online")
    assert online["runtime_state"] == "online"
    assert acceptance.request("GET", worker_url)["runtime_state"] == "online"

    acceptance.put_profile(worker_id)
    active = acceptance.request("PUT", worker_url + "/profiles/release/activate")
    assert active["is_active"] is True
    profile = acceptance.request("GET", worker_url + "/profiles/release")
    assert profile["is_active"] is True
    matches = acceptance.search(worker_id)
    assert any(
        item["worker_id"] == worker_id and item["profile_key"] == f"{worker_id}:release"
        for item in matches
    ), matches

    acceptance.request("POST", f"/api/v1/workers/{worker_id}/sync", json={
        "name": "Acceptance Python Expert", "availability": "public", "runtime_state": "online",
        "profile": {"profile_id": "draft", "activate": False,
                    "contents": {"profile": "Unpublished alternative"}},
    })
    assert acceptance.request("GET", worker_url + "/profiles/release")["is_active"] is True
    assert acceptance.request("GET", worker_url + "/profiles/draft")["is_active"] is False
    profile_keys = {item["profile_key"] for item in acceptance.search(worker_id)}
    assert f"{worker_id}:release" in profile_keys
    assert f"{worker_id}:draft" not in profile_keys

    offline = acceptance.request("PUT", f"/api/v1/workers/{worker_id}/offline")
    assert offline["runtime_state"] == "offline"
    assert not acceptance.search(worker_id), "Offline worker must not be discovered by default"
    acceptance.request("PUT", worker_url + "/online")
    assert acceptance.search(worker_id)

    acceptance.request("DELETE", worker_url)
    acceptance.request("GET", worker_url, expected=404)
    acceptance.request("DELETE", worker_url, expected=404)
    assert not acceptance.search(worker_id), "Deleted worker vectors must not remain discoverable"


def exercise_invalid_requests(acceptance):
    missing = "acceptance_missing_" + uuid4().hex
    unauthorized = acceptance.client.get(f"/v1/workers/{missing}")
    assert unauthorized.status_code == 401
    acceptance.request("GET", f"/v1/workers/{missing}", expected=404)
    acceptance.request("POST", "/v1/workers", expected=422, json={"name": "missing-id"})
    acceptance.request("POST", "/api/v1/search", expected=422, json={"query": ""})
