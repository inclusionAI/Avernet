"""Unit tests for :class:`ConfigArtifactOffloader` — the ext ⇄ object-storage
transform in isolation from the repository/DB."""
from __future__ import annotations

import json

import pytest

from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ARTIFACT_KEY,
    ARTIFACT_OSS_MARKER,
    ARTIFACT_OSS_THRESHOLD_BYTES,
    ConfigArtifactOffloader,
)

pytestmark = pytest.mark.integration


class _FakeOSS:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.put_calls = 0

    def put_object(self, key, content) -> bool:
        self.put_calls += 1
        self.store[key] = content.encode("utf-8") if isinstance(content, str) else content
        return True

    def get_object(self, key):
        return self.store.get(key)

    def delete_object(self, key) -> bool:
        self.store.pop(key, None)
        return True

    def list_objects(self, prefix, max_keys: int = 1000):
        return [k for k in self.store if k.startswith(prefix)][:max_keys]


def _offloader():
    oss = _FakeOSS()
    return ConfigArtifactOffloader(oss), oss


def _big():
    return {"engine_type": "openclaw", "blob": "x" * (ARTIFACT_OSS_THRESHOLD_BYTES + 1024)}


def test_prepare_none_is_noop():
    off, _ = _offloader()
    assert off.prepare(None, 1, "dev") == (None, None)


def test_prepare_small_stays_inline():
    off, oss = _offloader()
    ext_json, pending = off.prepare({"config_artifact": {"a": 1}, "k": "v"}, 1, "dev")
    assert pending is None
    stored = json.loads(ext_json)
    assert stored == {"config_artifact": {"a": 1}, "k": "v"}
    assert ARTIFACT_OSS_MARKER not in stored
    assert oss.put_calls == 0  # prepare does no I/O


def test_prepare_large_emits_marker_and_pending():
    off, _ = _offloader()
    art = _big()
    ext_json, pending = off.prepare({"config_artifact": art, "k": "v"}, 7, "dev")
    stored = json.loads(ext_json)
    # Inline artifact replaced by the marker; sibling field kept.
    assert ARTIFACT_KEY not in stored
    assert stored["k"] == "v"
    marker = stored[ARTIFACT_OSS_MARKER]
    assert marker["offloaded"] is True
    assert marker["oss_key"].startswith("teclaw/dev/bot_publish/7/")
    assert marker["size_bytes"] > ARTIFACT_OSS_THRESHOLD_BYTES
    # pending carries the exact key + the artifact JSON (still no I/O done).
    key, body = pending
    assert key == marker["oss_key"]
    assert json.loads(body) == art


def test_prepare_drops_both_keys_then_writes_one():
    # Input carrying BOTH an inline artifact and a stale marker → only the fresh
    # inline artifact survives (mutual exclusion by construction).
    off, _ = _offloader()
    ext_json, pending = off.prepare(
        {"config_artifact": {"a": 1}, "config_artifact_oss": {"oss_key": "stale"}},
        1, "dev",
    )
    assert pending is None
    stored = json.loads(ext_json)
    assert stored == {"config_artifact": {"a": 1}}


def test_upload_writes_pending_and_fails_loud():
    off, oss = _offloader()
    off.upload(("teclaw/dev/bot_publish/1/config_artifact-abc.json", '{"a":1}'))
    assert oss.put_calls == 1
    off.upload(None)  # no-op
    assert oss.put_calls == 1

    class _Fail(_FakeOSS):
        def put_object(self, key, content):
            return False

    off_fail = ConfigArtifactOffloader(_Fail())
    with pytest.raises(RuntimeError):
        off_fail.upload(("k", "body"))


def test_prepare_then_upload_then_resolve_roundtrip():
    off, oss = _offloader()
    art = _big()
    ext_json, pending = off.prepare({"config_artifact": art}, 3, "dev")
    off.upload(pending)
    # Simulate what the repo stores/loads: parse the column, then resolve.
    resolved = off.resolve(json.loads(ext_json))
    assert resolved["config_artifact"] == art
    assert ARTIFACT_OSS_MARKER not in resolved


def test_resolve_passthrough_without_marker():
    off, _ = _offloader()
    assert off.resolve(None) is None
    assert off.resolve({"config_artifact": {"a": 1}}) == {"config_artifact": {"a": 1}}


def test_resolve_fetch_failure_raises():
    off, _ = _offloader()
    # Marker points at a key the store doesn't have → get_object returns None →
    # fail loud rather than return a record silently missing its artifact.
    ext = {ARTIFACT_OSS_MARKER: {"oss_key": "missing/key"}, "k": "v"}
    with pytest.raises(RuntimeError):
        off.resolve(ext)


def test_cleanup_sweeps_prefix_and_tolerates_errors():
    off, oss = _offloader()
    oss.store = {
        "teclaw/dev/bot_publish/9/config_artifact-a.json": b"1",
        "teclaw/dev/bot_publish/9/config_artifact-b.json": b"2",
        "teclaw/dev/bot_publish/10/config_artifact-c.json": b"3",
    }
    off.cleanup("teclaw/dev/bot_publish/9/")
    assert list(oss.store) == ["teclaw/dev/bot_publish/10/config_artifact-c.json"]

    class _ListRaises(_FakeOSS):
        def list_objects(self, prefix, max_keys=1000):
            raise RuntimeError("boom")

    # Must swallow the error (best-effort), not propagate.
    ConfigArtifactOffloader(_ListRaises()).cleanup("any/")
