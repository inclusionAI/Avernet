"""
Unit tests for engine.community.api.session.router

The session router resolves its SessionService via _get_session_api(), which
calls EngineManager.get_instance() at request time.  We patch that function
directly so no real engine runtime is needed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.session.router import router


# ── helpers ──────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 1, 12, 0, 0)


def _make_session(session_id: str = "sess-1", **kwargs):
    s = MagicMock()
    s.id = session_id
    s.title = kwargs.get("title", "Test Session")
    s.user_id = kwargs.get("user_id", "user-1")
    s.agent_id = kwargs.get("agent_id", None)
    s.model = kwargs.get("model", "gpt-4")
    s.runtime = kwargs.get("runtime", None)
    s.permission_mode = kwargs.get("permission_mode", None)
    s.created_at = _NOW
    s.updated_at = _NOW
    s.message_count = kwargs.get("message_count", 3)
    s.last_message = kwargs.get("last_message", None)
    return s


def _make_message(msg_id: str = "msg-1", session_id: str = "sess-1"):
    m = MagicMock()
    m.id = msg_id
    m.session_id = session_id
    m.role = "user"
    m.content = "Hello"
    m.created_at = _NOW
    m.metadata = {}
    m.history_meta = None
    return m


@pytest.fixture()
def mock_session_api():
    """Return a mock SessionService and patch _get_session_api to return it."""
    api = MagicMock()
    api.list = AsyncMock(return_value=[_make_session()])
    api.create = AsyncMock(return_value=_make_session())
    api.delete = AsyncMock(return_value=True)
    api.clear = AsyncMock(return_value=None)
    _history_result = MagicMock()
    _history_result.messages = [_make_message()]
    _history_result.total = 1
    api.get_history = AsyncMock(return_value=_history_result)
    api.update = AsyncMock(return_value=_make_session())
    with patch("engine.community.api.session.router._get_session_api", return_value=api):
        yield api


@pytest.fixture()
def client(mock_session_api) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── GET /api/sessions ─────────────────────────────────────────────────────────

class TestListSessions:
    def test_success_returns_list(self, client, mock_session_api):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == "sess-1"

    def test_query_params_forwarded(self, client, mock_session_api):
        resp = client.get("/api/sessions?user_id=u1&agent_id=a1&limit=5&offset=10")
        assert resp.status_code == 200
        call_args = mock_session_api.list.call_args[0][0]
        assert call_args.user_id == "u1"
        assert call_args.agent_id == "a1"
        assert call_args.limit == 5
        assert call_args.offset == 10

    def test_session_key_query_param_forwarded(self, client, mock_session_api):
        resp = client.get("/api/sessions?session_key=session%3Atarget")
        assert resp.status_code == 200
        call_args = mock_session_api.list.call_args[0][0]
        assert call_args.session_key == "session:target"

    def test_service_error_returns_500(self, client, mock_session_api):
        mock_session_api.list.side_effect = RuntimeError("db error")
        resp = client.get("/api/sessions")
        assert resp.status_code == 500
        assert "db error" in resp.json()["detail"]


# ── POST /api/sessions ────────────────────────────────────────────────────────

class TestCreateSession:
    def test_minimal_create(self, client, mock_session_api):
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == "sess-1"

    def test_full_create_passes_fields(self, client, mock_session_api):
        resp = client.post("/api/sessions", json={
            "title": "My Chat",
            "user_id": "alice",
            "agent_id": "bot-1",
            "model": "claude-3",
        })
        assert resp.status_code == 200
        req = mock_session_api.create.call_args[0][0]
        assert req.title == "My Chat"
        assert req.user_id == "alice"
        assert req.agent_id == "bot-1"
        assert req.model == "claude-3"

    def test_default_user_id_when_omitted(self, client, mock_session_api):
        client.post("/api/sessions", json={})
        req = mock_session_api.create.call_args[0][0]
        assert req.user_id == "default"

    def test_service_error_returns_500(self, client, mock_session_api):
        mock_session_api.create.side_effect = RuntimeError("create failed")
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 500

    def test_uuid_forwarded_to_create_request(self, client, mock_session_api):
        resp = client.post("/api/sessions", json={"uuid": "my-custom-uuid"})
        assert resp.status_code == 200
        req = mock_session_api.create.call_args[0][0]
        assert req.uuid == "my-custom-uuid"

    def test_uuid_defaults_to_none_when_omitted(self, client, mock_session_api):
        client.post("/api/sessions", json={})
        req = mock_session_api.create.call_args[0][0]
        assert req.uuid is None

    def test_runtime_forwarded_to_create_request(self, client, mock_session_api):
        resp = client.post("/api/sessions", json={
            "title": "AntCC Chat",
            "runtime": "codefuse-antcc",
        })
        assert resp.status_code == 200
        req = mock_session_api.create.call_args[0][0]
        assert req.runtime == "codefuse-antcc"

    def test_runtime_defaults_to_none(self, client, mock_session_api):
        client.post("/api/sessions", json={})
        req = mock_session_api.create.call_args[0][0]
        assert req.runtime is None

    def test_runtime_in_response(self, client, mock_session_api):
        mock_session_api.create.return_value = _make_session(runtime="codefuse-antcc")
        resp = client.post("/api/sessions", json={"runtime": "codefuse-antcc"})
        assert resp.status_code == 200
        assert resp.json()["data"]["runtime"] == "codefuse-antcc"

    def test_runtime_omitted_from_response_when_none(self, client, mock_session_api):
        mock_session_api.create.return_value = _make_session()
        resp = client.post("/api/sessions", json={})
        assert resp.status_code == 200
        assert "runtime" not in resp.json()["data"]

# ── GET /api/sessions/{session_id} ───────────────────────────────────────────

class TestGetSession:
    def test_found(self, client, mock_session_api):
        mock_session_api.list.return_value = [_make_session("sess-1")]
        resp = client.get("/api/sessions/sess-1")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "sess-1"

    def test_not_found_returns_404(self, client, mock_session_api):
        mock_session_api.list.return_value = []
        resp = client.get("/api/sessions/missing")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_base64_encoded_session_id_is_decoded(self, client, mock_session_api):
        """An id that doesn't contain ':' is treated as base64 and decoded."""
        import base64
        raw = "sess:1"
        encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        mock_session_api.list.return_value = [_make_session(raw)]
        resp = client.get(f"/api/sessions/{encoded}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == raw

    def test_service_error_returns_500(self, client, mock_session_api):
        mock_session_api.list.side_effect = RuntimeError("fetch failed")
        resp = client.get("/api/sessions/sess-1")
        assert resp.status_code == 500


# ── DELETE /api/sessions/{session_id} ────────────────────────────────────────

class TestDeleteSession:
    def test_success(self, client, mock_session_api):
        resp = client.delete("/api/sessions/sess-1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "deleted" in resp.json()["message"].lower()

    def test_not_found_returns_404(self, client, mock_session_api):
        mock_session_api.delete.return_value = False
        resp = client.delete("/api/sessions/missing")
        assert resp.status_code == 404

    def test_force_param_forwarded(self, client, mock_session_api):
        client.delete("/api/sessions/sess-1?force=true")
        req = mock_session_api.delete.call_args[0][0]
        assert req.force is True

    def test_service_error_returns_500(self, client, mock_session_api):
        mock_session_api.delete.side_effect = RuntimeError("cannot delete")
        resp = client.delete("/api/sessions/sess-1")
        assert resp.status_code == 500


# ── GET /api/sessions/{session_id}/messages ───────────────────────────────────

class TestGetSessionMessages:
    def test_success(self, client, mock_session_api):
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == "msg-1"

    def test_limit_and_offset_forwarded(self, client, mock_session_api):
        resp = client.get("/api/sessions/sess-1/messages?limit=10&offset=5")
        assert resp.status_code == 200
        req = mock_session_api.get_history.call_args[0][0]
        assert req.limit == 10
        assert req.offset == 5

    def test_message_with_history_meta(self, client, mock_session_api):
        msg = _make_message()
        msg.history_meta = {"summary": "x"}
        result = MagicMock()
        result.messages = [msg]
        result.total = 1
        mock_session_api.get_history.return_value = result
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["history_meta"] == {"summary": "x"}

    def test_service_error_returns_500(self, client, mock_session_api):
        mock_session_api.get_history.side_effect = RuntimeError("history fail")
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 500


# ── DELETE /api/sessions/{session_id}/messages ───────────────────────────────

class TestClearSessionMessages:
    def test_success(self, client, mock_session_api):
        resp = client.delete("/api/sessions/sess-1/messages")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "cleared" in resp.json()["message"].lower()

    def test_session_id_forwarded(self, client, mock_session_api):
        client.delete("/api/sessions/sess-42/messages")
        req = mock_session_api.clear.call_args[0][0]
        assert req.session_id == "sess-42"

    def test_service_error_returns_500(self, client, mock_session_api):
        mock_session_api.clear.side_effect = RuntimeError("clear failed")
        resp = client.delete("/api/sessions/sess-1/messages")
        assert resp.status_code == 500


# ── POST /api/sessions/{session_id}/update ───────────────────────────────────

class TestUpdateSession:
    def test_success(self, client, mock_session_api):
        resp = client.post("/api/sessions/sess-1/update?title=New+Title&model=gpt-4o")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_fields_forwarded(self, client, mock_session_api):
        client.post("/api/sessions/sess-1/update?title=Chat&model=claude")
        req = mock_session_api.update.call_args[0][0]
        assert req.title == "Chat"
        assert req.model == "claude"
        assert req.session_id == "sess-1"

    def test_no_params_still_calls_update(self, client, mock_session_api):
        resp = client.post("/api/sessions/sess-1/update")
        assert resp.status_code == 200
        req = mock_session_api.update.call_args[0][0]
        assert req.title is None
        assert req.model is None

    def test_all_query_params_forwarded(self, client, mock_session_api):
        resp = client.post(
            "/api/sessions/sess-1/update"
            "?title=Chat&model=GLM-5&cwd=/tmp/work"
            "&user_id=u-1&agent_id=a-1&permission_mode=ask"
        )
        assert resp.status_code == 200
        req = mock_session_api.update.call_args[0][0]
        assert req.title == "Chat"
        assert req.model == "GLM-5"
        assert req.cwd == "/tmp/work"
        assert req.user_id == "u-1"
        assert req.agent_id == "a-1"
        assert req.permission_mode == "ask"

    def test_engine_query_param_routes_to_session_api(self, client, mock_session_api, monkeypatch):
        from engine.community.api.session import router as router_mod
        captured: dict = {}
        original = router_mod._get_session_api

        def spy(engine=None):
            captured["engine"] = engine
            return original(engine)

        monkeypatch.setattr(router_mod, "_get_session_api", spy)
        resp = client.post("/api/sessions/sess-1/update?engine=openclaw&title=X")
        assert resp.status_code == 200
        assert captured["engine"] == "openclaw"

    def test_runtime_forwarded_to_update_request(self, client, mock_session_api):
        client.post("/api/sessions/sess-1/update?runtime=codefuse-antcc&model=qwen3")
        req = mock_session_api.update.call_args[0][0]
        assert req.runtime == "codefuse-antcc"
        assert req.model == "qwen3"

    def test_runtime_defaults_to_none_on_update(self, client, mock_session_api):
        client.post("/api/sessions/sess-1/update?model=gpt-4o")
        req = mock_session_api.update.call_args[0][0]
        assert req.runtime is None

    def test_runtime_in_update_response(self, client, mock_session_api):
        mock_session_api.update.return_value = _make_session(runtime="codefuse-antcc")
        resp = client.post("/api/sessions/sess-1/update?runtime=codefuse-antcc")
        assert resp.json()["data"]["runtime"] == "codefuse-antcc"

    def test_service_error_returns_500(self, client, mock_session_api):
        mock_session_api.update.side_effect = RuntimeError("update error")
        resp = client.post("/api/sessions/sess-1/update")
        assert resp.status_code == 500

    def test_session_with_last_message_serialized(self, client, mock_session_api):
        sess = _make_session("sess-1")
        sess.last_message = _make_message()
        mock_session_api.update.return_value = sess
        resp = client.post("/api/sessions/sess-1/update")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "last_message" in data
        assert data["last_message"]["id"] == "msg-1"
