"""Unit tests for the OpenClaw session ACL adapter.

Drives `OpenClawSessionAdapter` against a fake `OpenClawSessionPort` (a plain
object returning canned raw dicts) — the adapter's job is:
  - dict→DTO translation (Session / Message / SessionResetResult)
  - request→port-args serialisation (create / update)
  - request-param filtering / pagination (list)
  - reset in-band-error handling (no raise)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from engine.community.core.adapters.openclaw.session import OpenClawSessionAdapter, _session_label_suffix
from engine.community.core.engine.context import AuthContext
from engine.community.core.session.models import (
    Message,
    Session,
    SessionClearRequest,
    SessionCreateRequest,
    SessionDeleteRequest,
    SessionHistoryRequest,
    SessionListRequest,
    SessionResetRequest,
    SessionResetResult,
    SessionUpdateRequest,
)


# ── helpers ───────────────────────────────────────────────────────────────────


@dataclass
class _FakeAuth:
    token: str | None = None


def _auth(token: str | None = None) -> AuthContext:
    return _FakeAuth(token=token)  # type: ignore[return-value]


def _make_raw_session(
    key: str = "session:abc:user:default",
    label: str = "My Session",
    model: str | None = "claude/claude-3",
    message_count: int = 3,
    messages: list[dict] | None = None,
    preview: str | None = "hello",
) -> dict[str, Any]:
    msgs = messages if messages is not None else [
        _make_raw_message(role="user", content="hi"),
        _make_raw_message(role="assistant", content="hello"),
        _make_raw_message(role="user", content="bye"),
    ]
    return {
        "key": key,
        "label": label,
        "model": model,
        "_normalized_model": model,
        "_message_count": message_count,
        "_messages": msgs,
        "inputTokens": 10,
        "outputTokens": 5,
        "preview": preview,
    }


def _make_raw_message(
    role: str = "user",
    content: str = "hello",
    msg_id: str | None = None,
    timestamp: int | None = None,
) -> dict[str, Any]:
    return {
        "id": msg_id or f"msg-{role}-1",
        "role": role,
        "content": content,
        "timestamp": timestamp or 1_700_000_000_000,
    }


# ── fake port ─────────────────────────────────────────────────────────────────


class _FakeSessionPort:
    """Fake `OpenClawSessionPort` — returns canned dicts; records calls."""

    def __init__(
        self,
        sessions_list_result: list[dict] | None = None,
        session_create_result: dict | None = None,
        session_delete_result: bool = True,
        session_clear_raises: Exception | None = None,
        chat_history_result: list[dict] | None = None,
        session_patch_then_get_result: dict | None = None,
        session_reset_result: dict | None = None,
    ) -> None:
        self._sessions_list_result = (
            sessions_list_result
            if sessions_list_result is not None
            else [_make_raw_session()]
        )
        self._session_create_result = session_create_result or {
            "key": "session:abc:user:default",
            "label": "Test_0601120000",
        }
        self._session_delete_result = session_delete_result
        self._session_clear_raises = session_clear_raises
        self._chat_history_result = chat_history_result if chat_history_result is not None else [
            _make_raw_message(),
        ]
        self._session_patch_then_get_result = session_patch_then_get_result or _make_raw_session(
            label="Updated"
        )
        self._session_reset_result = session_reset_result or {
            "success": True,
            "payload": {"reset": True},
        }

        # call recorders
        self.sessions_list_calls: list[dict] = []
        self.session_create_calls: list[dict] = []
        self.session_delete_calls: list[dict] = []
        self.session_clear_calls: list[dict] = []
        self.chat_history_calls: list[dict] = []
        self.session_patch_then_get_calls: list[dict] = []
        self.session_reset_calls: list[dict] = []

    async def sessions_list(
        self,
        token: str | None = None,
        offset: int = 0,
        limit: int = 50,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> list[dict]:
        self.sessions_list_calls.append(
            {
                "token": token,
                "offset": offset,
                "limit": limit,
                "agent_id": agent_id,
                "session_key": session_key,
            }
        )
        # Mirror the real port: request filtering happens before pagination.
        # (the adapter forwards the primitives and only builds DTOs).
        result = self._sessions_list_result
        if agent_id is not None:
            result = [s for s in result if s.get("agentId") == agent_id]
        if session_key and session_key.strip():
            result = [s for s in result if s.get("key") == session_key]
        return result[offset : offset + limit]

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict:
        self.session_create_calls.append(
            {"key": key, "label": label, "model": model, "token": token}
        )
        return self._session_create_result

    async def session_delete(self, key: str, token: str | None = None) -> bool:
        self.session_delete_calls.append({"key": key, "token": token})
        return self._session_delete_result

    async def session_clear(self, key: str, token: str | None = None) -> None:
        self.session_clear_calls.append({"key": key, "token": token})
        if self._session_clear_raises:
            raise self._session_clear_raises

    async def chat_history(
        self,
        session_key: str,
        limit: int | None = None,
        token: str | None = None,
    ) -> list[dict]:
        self.chat_history_calls.append(
            {"session_key": session_key, "limit": limit, "token": token}
        )
        return self._chat_history_result

    async def session_patch_then_get(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict:
        self.session_patch_then_get_calls.append(
            {"key": key, "label": label, "model": model, "token": token}
        )
        return self._session_patch_then_get_result

    async def session_reset(self, session_key: str, token: str | None = None) -> dict:
        self.session_reset_calls.append({"session_key": session_key, "token": token})
        return self._session_reset_result


# ── list ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_builds_session_dto_from_raw_dict():
    raw = _make_raw_session(key="session:x:user:default", label="X Session", model="ant/llm")
    port = _FakeSessionPort(sessions_list_result=[raw])
    adapter = OpenClawSessionAdapter(port)
    sessions = await adapter.list(SessionListRequest(), auth=_auth("tok1"))
    assert len(sessions) == 1
    s = sessions[0]
    assert isinstance(s, Session)
    assert s.id == "session:x:user:default"
    assert s.key == "session:x:user:default"
    assert s.title == "X Session"
    assert s.model == "ant/llm"
    assert port.sessions_list_calls[0]["token"] == "tok1"


@pytest.mark.asyncio
async def test_list_no_auth_passes_none_token():
    port = _FakeSessionPort()
    adapter = OpenClawSessionAdapter(port)
    await adapter.list(SessionListRequest())
    assert port.sessions_list_calls[0]["token"] is None


@pytest.mark.asyncio
async def test_list_empty_result():
    port = _FakeSessionPort(sessions_list_result=[])
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.list(SessionListRequest())
    assert result == []


@pytest.mark.asyncio
async def test_list_forwards_pagination_to_port():
    # Pagination is the PORT's job now (it sits mid-orchestration, between the
    # bcs filter and the per-page chat.history fetch — see the legacy ordering).
    # The adapter must forward offset/limit/agent_id and only build DTOs.
    raws = [
        _make_raw_session(key=f"session:{i}:user:default", label=f"S{i}")
        for i in range(5)
    ]
    port = _FakeSessionPort(sessions_list_result=raws)
    adapter = OpenClawSessionAdapter(port)
    sessions = await adapter.list(SessionListRequest(offset=2, limit=2))
    # adapter forwarded the primitives ...
    assert port.sessions_list_calls[0]["offset"] == 2
    assert port.sessions_list_calls[0]["limit"] == 2
    # ... and returned exactly the page the port produced (no adapter-side slice).
    assert len(sessions) == 2
    assert sessions[0].key == "session:2:user:default"
    assert sessions[1].key == "session:3:user:default"


@pytest.mark.asyncio
async def test_list_forwards_session_key_to_port():
    port = _FakeSessionPort()
    adapter = OpenClawSessionAdapter(port)

    await adapter.list(SessionListRequest(session_key="session:target"))

    assert port.sessions_list_calls[0]["session_key"] == "session:target"


@pytest.mark.asyncio
async def test_list_populates_last_message_from_raw_messages():
    msg = _make_raw_message(role="assistant", content="last reply", msg_id="msg-last")
    raw = _make_raw_session(messages=[msg], message_count=1)
    port = _FakeSessionPort(sessions_list_result=[raw])
    adapter = OpenClawSessionAdapter(port)
    sessions = await adapter.list(SessionListRequest())
    assert sessions[0].last_message is not None
    assert isinstance(sessions[0].last_message, Message)
    assert sessions[0].last_message.content == "last reply"


@pytest.mark.asyncio
async def test_list_message_count_from_impl_field():
    raw = _make_raw_session(message_count=7)
    port = _FakeSessionPort(sessions_list_result=[raw])
    adapter = OpenClawSessionAdapter(port)
    sessions = await adapter.list(SessionListRequest())
    assert sessions[0].message_count == 7


# ── label suffix helper ───────────────────────────────────────────────────────


def test_session_label_suffix_empty_value_falls_back_to_uuid_hex():
    suffix = _session_label_suffix("")

    assert isinstance(suffix, str)
    assert len(suffix) == 32


def test_session_label_suffix_string_conversion_error_falls_back_to_uuid_hex():
    class BadSessionId:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    suffix = _session_label_suffix(BadSessionId())

    assert isinstance(suffix, str)
    assert len(suffix) == 32


# ── create ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_builds_session_dto_and_calls_port():
    port = _FakeSessionPort(
        session_create_result={"key": "session:fixed:user:u1", "label": "My_0601120000"}
    )
    adapter = OpenClawSessionAdapter(port)
    request = SessionCreateRequest(title="My", user_id="u1", model="ant/llm")

    # Inject a fixed uuid so the key is deterministic.
    request.uuid = "fixed"
    session = await adapter.create(request, auth=_auth("tok2"))

    assert isinstance(session, Session)
    assert session.key == "session:fixed:user:u1"
    assert session.model == "ant/llm"
    assert session.user_id == "u1"

    call = port.session_create_calls[0]
    assert call["key"] == "session:fixed:user:u1"
    assert call["model"] == "ant/llm"
    assert call["token"] == "tok2"
    # label should use the session uuid suffix, not a second-level timestamp.
    assert call["label"] == "My_fixed"


@pytest.mark.asyncio
async def test_create_no_title_passes_none_label():
    port = _FakeSessionPort()
    adapter = OpenClawSessionAdapter(port)
    request = SessionCreateRequest(user_id="u2")
    request.uuid = "no-title"
    await adapter.create(request)
    call = port.session_create_calls[0]
    assert call["label"] is None


# ── delete ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_returns_true_on_success():
    port = _FakeSessionPort(session_delete_result=True)
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.delete(
        SessionDeleteRequest(session_id="s1"), auth=_auth("tok3")
    )
    assert result is True
    assert port.session_delete_calls[0]["key"] == "s1"
    assert port.session_delete_calls[0]["token"] == "tok3"


@pytest.mark.asyncio
async def test_delete_returns_false_on_failure():
    port = _FakeSessionPort(session_delete_result=False)
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.delete(SessionDeleteRequest(session_id="s1"))
    assert result is False


# ── clear ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_calls_port_and_does_not_raise():
    port = _FakeSessionPort()
    adapter = OpenClawSessionAdapter(port)
    await adapter.clear(SessionClearRequest(session_id="s2"), auth=_auth("tok4"))
    assert port.session_clear_calls[0]["key"] == "s2"
    assert port.session_clear_calls[0]["token"] == "tok4"


@pytest.mark.asyncio
async def test_clear_propagates_port_error():
    port = _FakeSessionPort(session_clear_raises=RuntimeError("clear failed"))
    adapter = OpenClawSessionAdapter(port)
    with pytest.raises(RuntimeError, match="clear failed"):
        await adapter.clear(SessionClearRequest(session_id="s2"))


# ── get_history ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_builds_message_dtos():
    msgs = [
        _make_raw_message(role="user", content="q", msg_id="m1"),
        _make_raw_message(role="assistant", content="a", msg_id="m2"),
    ]
    port = _FakeSessionPort(chat_history_result=msgs)
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(
        SessionHistoryRequest(session_id="s3"), auth=_auth("tok5")
    )
    assert len(result.messages) == 2
    assert all(isinstance(m, Message) for m in result.messages)
    assert result.messages[0].role == "user"
    assert result.messages[0].content == "q"
    assert result.messages[1].role == "assistant"
    assert result.messages[1].content == "a"
    assert result.total is None
    call = port.chat_history_calls[0]
    assert call["session_key"] == "s3"
    assert call["token"] == "tok5"


@pytest.mark.asyncio
async def test_get_history_preserves_history_meta():
    # `historyMeta` from the gateway must pass through to Message.history_meta.
    meta = {"plugin": "kimi", "schemaVersion": 2, "agentId": "a1"}
    raw = {"id": "m1", "role": "assistant", "content": "x", "historyMeta": meta}
    port = _FakeSessionPort(chat_history_result=[raw])
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(SessionHistoryRequest(session_id="s9"))
    assert result.messages[0].history_meta == meta


@pytest.mark.asyncio
async def test_get_history_generates_id_when_missing():
    # No `id` key → fallback id is `{session_id}_{index}` (legacy contract).
    raws = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    port = _FakeSessionPort(chat_history_result=raws)
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(SessionHistoryRequest(session_id="sess"))
    assert result.messages[0].id == "sess_0"
    assert result.messages[1].id == "sess_1"


@pytest.mark.asyncio
async def test_get_history_applies_offset_limit():
    msgs = [
        _make_raw_message(role="user", content=f"msg{i}", msg_id=f"m{i}")
        for i in range(5)
    ]
    port = _FakeSessionPort(chat_history_result=msgs)
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(
        SessionHistoryRequest(session_id="s3", offset=1, limit=2)
    )
    assert len(result.messages) == 2
    assert result.messages[0].content == "msg1"
    assert result.messages[1].content == "msg2"


@pytest.mark.asyncio
async def test_get_history_maps_tool_result_role():
    msg = {
        "id": "t1",
        "role": "toolResult",
        "content": "result-text",
        "toolName": "bash",
        "toolCallId": "tc1",
        "isError": False,
    }
    port = _FakeSessionPort(chat_history_result=[msg])
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(SessionHistoryRequest(session_id="s4"))
    assert len(result.messages) == 1
    assert result.messages[0].role == "tool_result"
    assert result.messages[0].metadata is not None
    assert result.messages[0].metadata["tool_name"] == "bash"
    assert result.messages[0].metadata["success"] is True


@pytest.mark.asyncio
async def test_get_history_maps_tool_use_role():
    msg = {
        "id": "t2",
        "role": "toolUse",
        "content": "",
        "toolName": "browser",
        "toolCallId": "tc2",
    }
    port = _FakeSessionPort(chat_history_result=[msg])
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(SessionHistoryRequest(session_id="s5"))
    assert result.messages[0].role == "tool_use"
    assert result.messages[0].metadata is not None
    assert result.messages[0].metadata["tool_name"] == "browser"


@pytest.mark.asyncio
async def test_get_history_empty_result():
    port = _FakeSessionPort(chat_history_result=[])
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(SessionHistoryRequest(session_id="s6"))
    assert result.messages == []


@pytest.mark.asyncio
async def test_get_history_timestamp_int_parsed():
    msg = _make_raw_message(timestamp=1_700_000_000_000)
    port = _FakeSessionPort(chat_history_result=[msg])
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(SessionHistoryRequest(session_id="s7"))
    assert isinstance(result.messages[0].created_at, datetime)
    assert result.messages[0].created_at.tzinfo is UTC


# ── update (session_patch_then_get) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_calls_patch_then_get_and_builds_session():
    updated_raw = _make_raw_session(label="New Label")
    port = _FakeSessionPort(session_patch_then_get_result=updated_raw)
    adapter = OpenClawSessionAdapter(port)
    request = SessionUpdateRequest(
        session_id="session:test-openclaw-session-id:user:u1",
        title="New Label",
    )
    session = await adapter.update(request, auth=_auth("tok6"))
    assert isinstance(session, Session)
    assert session.title == "New Label"
    call = port.session_patch_then_get_calls[0]
    assert call["key"] == "session:test-openclaw-session-id:user:u1"
    assert call["token"] == "tok6"
    assert call["label"] == "New Label_test-openclaw-session-id"


@pytest.mark.asyncio
async def test_update_title_with_unexpected_session_id_format_still_builds_label():
    updated_raw = _make_raw_session(label="New Label_s8")
    port = _FakeSessionPort(session_patch_then_get_result=updated_raw)
    adapter = OpenClawSessionAdapter(port)
    request = SessionUpdateRequest(session_id="s8", title="New Label")

    await adapter.update(request)

    call = port.session_patch_then_get_calls[0]
    assert call["label"] == "New Label_s8"


@pytest.mark.asyncio
async def test_update_no_title_no_model_scans_list():
    raw = _make_raw_session(key="s9")
    port = _FakeSessionPort(sessions_list_result=[raw])
    adapter = OpenClawSessionAdapter(port)
    request = SessionUpdateRequest(session_id="s9")
    session = await adapter.update(request)
    # No patch call; list was called instead.
    assert port.session_patch_then_get_calls == []
    assert port.sessions_list_calls != []
    assert isinstance(session, Session)
    assert session.id == "s9"


@pytest.mark.asyncio
async def test_update_no_title_no_model_raises_when_not_found():
    port = _FakeSessionPort(sessions_list_result=[])
    adapter = OpenClawSessionAdapter(port)
    request = SessionUpdateRequest(session_id="missing")
    with pytest.raises(RuntimeError, match="Session not found"):
        await adapter.update(request)


@pytest.mark.asyncio
async def test_update_model_only_sends_none_label():
    updated_raw = _make_raw_session(model="new/model")
    port = _FakeSessionPort(session_patch_then_get_result=updated_raw)
    adapter = OpenClawSessionAdapter(port)
    request = SessionUpdateRequest(session_id="s10", model="new/model")
    await adapter.update(request)
    call = port.session_patch_then_get_calls[0]
    assert call["label"] is None
    assert call["model"] == "new/model"


# ── reset (in-band error) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_success_builds_result_with_payload():
    port = _FakeSessionPort(
        session_reset_result={"success": True, "payload": {"reset": True}}
    )
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.reset(
        SessionResetRequest(session_key="sk1"), auth=_auth("tok7")
    )
    assert isinstance(result, SessionResetResult)
    assert result.ok is True
    assert result.payload == {"reset": True}
    assert result.error_code is None
    assert result.error_message is None
    call = port.session_reset_calls[0]
    assert call["session_key"] == "sk1"
    assert call["token"] == "tok7"


@pytest.mark.asyncio
async def test_reset_failure_builds_result_with_error_no_raise():
    port = _FakeSessionPort(
        session_reset_result={
            "success": False,
            "error": {"code": "NOT_FOUND", "message": "session gone"},
        }
    )
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.reset(SessionResetRequest(session_key="sk2"))
    assert result.ok is False
    assert result.error_code == "NOT_FOUND"
    assert result.error_message == "session gone"
    # Must NOT raise.


@pytest.mark.asyncio
async def test_reset_failure_missing_error_dict_uses_defaults():
    port = _FakeSessionPort(session_reset_result={"success": False})
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.reset(SessionResetRequest(session_key="sk3"))
    assert result.ok is False
    assert result.error_code == "UNKNOWN"
    assert result.error_message == "Unknown error"


@pytest.mark.asyncio
async def test_reset_empty_payload_normalised_to_empty_dict():
    port = _FakeSessionPort(
        session_reset_result={"success": True, "payload": None}
    )
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.reset(SessionResetRequest(session_key="sk4"))
    assert result.ok is True
    assert result.payload == {}


# ── content list parsing ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_content_list_joined():
    msg = {
        "id": "cl1",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "part1"},
            {"type": "text", "text": "part2"},
        ],
    }
    port = _FakeSessionPort(chat_history_result=[msg])
    adapter = OpenClawSessionAdapter(port)
    result = await adapter.get_history(SessionHistoryRequest(session_id="cx"))
    assert result.messages[0].content == "part1\npart2"
