"""Unit tests for the ClaudeCode ACL adapters.

Drives each adapter against a fake port (a plain object implementing the
port methods and returning canned raw dicts) — the adapter's job is
auth-token extraction, dict→DTO translation, and request→port-args
serialisation. No gateway / relay is involved.

Mirrors the structure of ``core/adapters/openclaw/tests/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from engine.community.core.adapters.claude_code.chat import ClaudeCodeChatAdapter
from engine.community.core.adapters.claude_code.cron import ClaudeCodeCronAdapter
from engine.community.core.adapters.claude_code.file import ClaudeCodeFileAdapter
from engine.community.core.adapters.claude_code.mcp import ClaudeCodeMcpAdapter
from engine.community.core.adapters.claude_code.models import ClaudeCodeModelsAdapter
from engine.community.core.adapters.claude_code.relay import ClaudeCodeRelayAdapter
from engine.community.core.adapters.claude_code.session import (
    ClaudeCodeSessionAdapter,
    _parse_session_key,
    _relay_message_to_message,
    _relay_session_to_session,
)
from engine.community.core.adapters.claude_code.skills import ClaudeCodeSkillsAdapter
from engine.community.core.chat.models import ChatAbortRequest, ChatRequest
from engine.community.core.engine.context import AuthContext
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.mcp.models import (
    MCPFilterRequest,
    MCPServerConfig,
    MCPServerStatus,
    TransportType,
)
from engine.community.core.session.models import (
    SessionClearRequest,
    SessionCreateRequest,
    SessionDeleteRequest,
    SessionHistoryRequest,
    SessionListRequest,
    SessionResetRequest,
    SessionUpdateRequest,
)
from engine.community.core.skills.models import (
    CenterEnsureItem,
    CenterEnsureRequest,
    CleanSymlinksRequest,
    SkillConfig,
    SkillExecutionRequest,
    SkillType,
    SyncBindPathsRequest,
    SyncSymlinksRequest,
)
from engine.community.kernel.frames import EventFrame, ResponseFrame


# ── helpers ───────────────────────────────────────────────────────────────────


@dataclass
class _FakeAuth:
    token: str | None = None


def _auth(token: str | None = None) -> AuthContext:
    return _FakeAuth(token=token)  # type: ignore[return-value]


# ──────────────────────────────────────────────────────────────────────────────
# session adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeSessionPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._sessions: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []
        self._create_response: dict[str, Any] = {}

    async def sessions_list(
        self, token=None, offset=0, limit=50, agent_id=None,
    ) -> list[dict]:
        self.calls.append({
            "method": "sessions_list", "token": token, "offset": offset,
            "limit": limit, "agent_id": agent_id,
        })
        return self._sessions

    async def session_create(
        self, key, label=None, model=None, cwd=None, token=None,
    ) -> dict:
        self.calls.append({
            "method": "session_create", "key": key, "label": label,
            "model": model, "cwd": cwd, "token": token,
        })
        return self._create_response

    async def session_delete(self, key, token=None) -> bool:
        self.calls.append({"method": "session_delete", "key": key, "token": token})
        return True

    async def session_reset(self, key, token=None) -> dict:
        self.calls.append({"method": "session_reset", "key": key, "token": token})
        return {"success": True, "payload": {"reset": True}}

    async def session_get_history(self, key, limit=100, token=None) -> list[dict]:
        self.calls.append({
            "method": "session_get_history", "key": key, "limit": limit, "token": token,
        })
        return self._messages

    async def session_clear(self, key, token=None) -> dict:
        self.calls.append({"method": "session_clear", "key": key, "token": token})
        return {"success": True, "payload": {}}


def _make_raw_session(
    key="agent:bot1:session:abc:user:u1",
    label="My Session",
    model="claude/claude-3",
    message_count=3,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "model": model,
        "updatedAt": 1717000000000,
        "createdAt": 1717000000000,
        "messageCount": message_count,
        "inputTokens": 100,
        "outputTokens": 200,
        "preview": "hello",
    }


def _make_raw_message(role="user", content="hi", index=0, metadata=None) -> dict[str, Any]:
    return {
        "id": f"msg-{index}",
        "role": role,
        "content": content,
        "timestamp": 1717000000000,
        "metadata": metadata or {},
    }


class TestSessionAdapter:
    def test_parse_session_key_new_form(self):
        user, agent = _parse_session_key("agent:bot1:session:abc:user:u1")
        assert user == "u1"
        assert agent == "bot1"

    def test_parse_session_key_legacy_form(self):
        user, agent = _parse_session_key("user:u2:session:def:agent:bot2")
        assert user == "u2"
        assert agent == "bot2"

    def test_parse_session_key_short_form_returns_none(self):
        assert _parse_session_key("session:uuid-only") == (None, None)

    def test_relay_session_to_session_builds_dto(self):
        raw = _make_raw_session()
        s = _relay_session_to_session(raw, user_id="default")
        assert s.id == raw["key"]
        assert s.agent_id == "bot1"
        assert s.user_id == "u1"
        assert s.model == "claude/claude-3"
        assert s.message_count == 3
        assert s.total_input_tokens == 100
        assert s.total_output_tokens == 200
        assert s.last_message is not None
        assert s.last_message.content == "hello"

    def test_relay_message_to_message_user_role(self):
        m = _relay_message_to_message(_make_raw_message(), session_id="sess", index=0)
        assert m.id == "msg-0"
        assert m.session_id == "sess"
        assert m.role == "user"
        assert m.content == "hi"

    def test_relay_message_to_message_tool_use_role_renders_tool_block(self):
        m = _relay_message_to_message(
            _make_raw_message(
                role="tool_use",
                metadata={"toolName": "Bash", "toolCallId": "tc1", "input": "ls"},
            ),
            session_id="sess", index=2,
        )
        assert m.role == "tool_use"
        assert "<tool>" in m.content
        assert m.metadata["tool_name"] == "Bash"
        assert m.metadata["tool_call_id"] == "tc1"

    async def test_list_passes_token_and_filters_bcs_grp(self):
        port = _FakeSessionPort()
        port._sessions = [
            _make_raw_session(key="agent:b1:session:s1:user:u1"),
            _make_raw_session(key="agent:b1:session:bcs_grp_x:user:u1"),
        ]
        adapter = ClaudeCodeSessionAdapter(port)

        sessions = await adapter.list(
            SessionListRequest(user_id="u1", agent_id="b1", limit=10),
            auth=_auth("tok-1"),
        )

        assert len(sessions) == 1
        assert sessions[0].id == "agent:b1:session:s1:user:u1"
        assert port.calls[0]["token"] == "tok-1"
        assert port.calls[0]["agent_id"] == "b1"
        assert port.calls[0]["limit"] == 10

    async def test_list_filters_by_agent_id(self):
        port = _FakeSessionPort()
        port._sessions = [
            _make_raw_session(key="agent:b1:session:s1:user:u1"),
            _make_raw_session(key="agent:b2:session:s2:user:u1"),
        ]
        adapter = ClaudeCodeSessionAdapter(port)

        sessions = await adapter.list(
            SessionListRequest(agent_id="b2", limit=10),
        )
        assert len(sessions) == 1
        assert sessions[0].agent_id == "b2"

    async def test_create_builds_canonical_key_and_calls_port(self):
        port = _FakeSessionPort()
        adapter = ClaudeCodeSessionAdapter(port)
        session = await adapter.create(
            SessionCreateRequest(
                title="T", user_id="u1", agent_id="b1", model="m1",
            ),
            auth=_auth("tok-2"),
        )
        # The key form is agent:b1:session:<uuid>:user:u1
        assert session.id.startswith("agent:b1:session:")
        assert session.id.endswith(":user:u1")
        assert session.model == "m1"
        assert port.calls[0]["method"] == "session_create"
        assert port.calls[0]["token"] == "tok-2"
        assert port.calls[0]["model"] == "m1"

    async def test_delete_delegates(self):
        port = _FakeSessionPort()
        adapter = ClaudeCodeSessionAdapter(port)
        ok = await adapter.delete(SessionDeleteRequest(session_id="sk"), auth=_auth("t"))
        assert ok is True
        assert port.calls[0] == {"method": "session_delete", "key": "sk", "token": "t"}

    async def test_clear_raises_on_failure(self):
        class _FailingPort:
            async def session_clear(self, key, token=None):
                return {"success": False, "error": {"message": "nope"}}

            async def sessions_list(self, *a, **k): return []
            async def session_create(self, *a, **k): return {}
            async def session_delete(self, *a, **k): return True
            async def session_reset(self, *a, **k): return {"success": True}
            async def session_get_history(self, *a, **k): return []

        adapter = ClaudeCodeSessionAdapter(_FailingPort())  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="nope"):
            await adapter.clear(SessionClearRequest(session_id="sk"))

    async def test_reset_builds_result_on_success(self):
        port = _FakeSessionPort()
        adapter = ClaudeCodeSessionAdapter(port)
        result = await adapter.reset(SessionResetRequest(session_key="sk"), auth=_auth("t"))
        assert result.ok is True
        assert result.payload == {"reset": True}

    async def test_reset_builds_result_on_failure(self):
        class _P:
            async def sessions_list(self, *a, **k): return []
            async def session_create(self, *a, **k): return {}
            async def session_delete(self, *a, **k): return True
            async def session_reset(self, key, token=None):
                return {"success": False, "error": {"code": "X", "message": "fail"}}
            async def session_get_history(self, *a, **k): return []
            async def session_clear(self, *a, **k): return {"success": True}

        adapter = ClaudeCodeSessionAdapter(_P())  # type: ignore[arg-type]
        result = await adapter.reset(SessionResetRequest(session_key="sk"))
        assert result.ok is False
        assert result.error_code == "X"
        assert result.error_message == "fail"

    async def test_get_history_builds_messages(self):
        port = _FakeSessionPort()
        port._messages = [
            _make_raw_message(role="user", content="hello", index=0),
            _make_raw_message(role="assistant", content="hi back", index=1),
        ]
        adapter = ClaudeCodeSessionAdapter(port)
        result = await adapter.get_history(
            SessionHistoryRequest(session_id="sk", limit=10), auth=_auth("t"),
        )
        assert len(result.messages) == 2
        assert result.messages[0].role == "user"
        assert result.messages[1].content == "hi back"
        assert port.calls[0]["limit"] == 10


# ──────────────────────────────────────────────────────────────────────────────
# chat adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeChatPort:
    def __init__(self, frames: list[EventFrame] | None = None) -> None:
        self._frames = frames or []
        self.abort_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.inject_calls: list[dict[str, Any]] = []
        self._abort_response: dict[str, Any] = {
            "success": True,
            "payload": {"aborted": True, "runId": "run-9"},
        }

    async def chat_stream(self, *, session_key, message, timeout_ms=None, cwd=None,
                          model=None, permission_mode=None, attachments=None,
                          token=None):
        self.stream_calls.append({
            "session_key": session_key, "message": message, "timeout_ms": timeout_ms,
            "cwd": cwd, "model": model, "permission_mode": permission_mode,
            "attachments": attachments, "token": token,
        })
        for f in self._frames:
            yield f

    async def chat_abort(self, *, session_key, run_id=None, token=None) -> dict[str, Any]:
        self.abort_calls.append({
            "session_key": session_key, "run_id": run_id, "token": token,
        })
        return self._abort_response

    async def chat_inject(self, *, session_key, message, label=None, token=None) -> dict[str, Any]:
        self.inject_calls.append({
            "session_key": session_key, "message": message, "label": label, "token": token,
        })
        return {"success": True, "payload": {"injected": True}}

    async def resolve_exec_approval(self, **kw): return {"success": True}
    async def resolve_interaction(self, **kw): return {"success": True}
    async def resolve_mode_transition(self, **kw): return {"success": True}


class TestChatAdapter:
    def _request(self, **kw) -> ChatRequest:
        defaults = {
            "userId": "u1", "agentId": "b1", "query": "hello",
            "sessionId": "agent:b1:session:s1:user:u1",
            "extraParams": {"cwd": "/tmp", "model": "m1", "permissionMode": "plan"},
        }
        defaults.update(kw)
        return ChatRequest(**defaults)

    async def test_stream_yields_frames_and_extracts_params(self):
        frames = [
            EventFrame(event="agent", payload={"state": "delta"}),
            EventFrame(event="chat", payload={"state": "final"}),
        ]
        port = _FakeChatPort(frames)
        adapter = ClaudeCodeChatAdapter(port)

        out = []
        async for f in adapter.stream(self._request(), auth=_auth("tok")):
            out.append(f)

        assert out == frames
        call = port.stream_calls[0]
        assert call["session_key"] == "agent:b1:session:s1:user:u1"
        assert call["cwd"] == "/tmp"
        assert call["model"] == "m1"
        assert call["permission_mode"] == "plan"
        assert call["token"] == "tok"

    async def test_stream_builds_canonical_key_when_no_session(self):
        port = _FakeChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        async for _ in adapter.stream(
            ChatRequest(userId="u1", agentId="b1", query="hi"),
        ):
            pass
        assert port.stream_calls[0]["session_key"].startswith("agent:b1:session:")
        assert port.stream_calls[0]["session_key"].endswith(":user:u1")

    async def test_stream_converts_connection_error_to_error_frame(self):
        class _ErrPort(_FakeChatPort):
            async def chat_stream(self, **kw):
                raise ConnectionError("boom")
                yield  # pragma: no cover — unreachable; makes this an async gen

        adapter = ClaudeCodeChatAdapter(_ErrPort())  # type: ignore[arg-type]
        out = []
        async for f in adapter.stream(self._request()):
            out.append(f)
        assert len(out) == 1
        assert out[0].event == "error"
        assert out[0].payload["errorCode"] == "CONNECTION_ERROR"

    async def test_abort_builds_result_on_success(self):
        port = _FakeChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.abort(
            ChatAbortRequest(session_key="sk", run_id="r1"),
            auth=_auth("t"),
        )
        assert result.ok is True
        assert result.aborted is True
        assert result.run_id == "run-9"
        assert len(result.emit_events) == 1
        assert result.emit_events[0].payload["state"] == "aborted"

    async def test_abort_builds_result_on_failure(self):
        port = _FakeChatPort()
        port._abort_response = {"success": False, "error": {"code": "NOPE", "message": "x"}}
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.abort(ChatAbortRequest(session_key="sk"))
        assert result.ok is False
        assert result.error.code == "NOPE"

    async def test_inject_returns_ok_dict_on_success(self):
        port = _FakeChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.inject("sk", "msg", label="l", auth=_auth("t"))
        assert result["ok"] is True
        assert result["payload"] == {"injected": True}
        assert port.inject_calls[0]["label"] == "l"

    async def test_inject_validates_empty_inputs(self):
        adapter = ClaudeCodeChatAdapter(_FakeChatPort())
        assert (await adapter.inject("", "m"))["ok"] is False
        assert (await adapter.inject("sk", ""))["ok"] is False


# ──────────────────────────────────────────────────────────────────────────────
# mcp adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeMcpPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def mcp_list_servers(self, token=None) -> list[dict]:
        self.calls.append({"method": "mcp_list_servers", "token": token})
        return [{"serverCode": "s1", "type": "stdio", "command": "npx",
                 "args": ["-y", "x"], "enabled": True}]

    async def mcp_get_server(self, server_code, token=None) -> dict | None:
        self.calls.append({"method": "mcp_get_server", "server_code": server_code, "token": token})
        return {"serverCode": server_code, "type": "sse", "url": "http://x", "enabled": True}

    async def mcp_create_server(self, config, token=None) -> dict:
        self.calls.append({"method": "mcp_create_server", "config": config, "token": token})
        return config

    async def mcp_update_server(self, server_code, patch, token=None) -> dict:
        self.calls.append({"method": "mcp_update_server", "server_code": server_code, "patch": patch, "token": token})
        return {"serverCode": server_code, **patch}

    async def mcp_delete_server(self, server_code, token=None) -> bool:
        self.calls.append({"method": "mcp_delete_server", "server_code": server_code, "token": token})
        return True

    async def mcp_start_server(self, *a, **k): ...
    async def mcp_stop_server(self, *a, **k): ...
    async def mcp_restart_server(self, *a, **k): ...

    async def mcp_get_server_status(self, server_code, token=None) -> dict:
        return {"status": "running"}

    async def mcp_list_tools(self, server_code, token=None) -> list[dict]:
        return [{"name": "t1", "description": "d", "inputSchema": {"x": 1}}]

    async def mcp_call_tool(self, server_code, tool_name, arguments=None, token=None, timeout_ms=None) -> dict:
        return {"content": [{"type": "text", "text": "ok"}], "isError": False, "serverCode": server_code}

    async def mcp_list_resources(self, server_code, token=None) -> list[dict]:
        return [{"uri": "u://1", "name": "r1"}]

    async def mcp_read_resource(self, server_code, resource_uri, token=None) -> dict:
        return {"content": [{"type": "text", "text": "file-body"}]}

    async def mcp_list_prompts(self, server_code, token=None) -> list[dict]:
        return [{"name": "p1", "description": "d"}]

    async def mcp_get_prompt(self, server_code, prompt_name, arguments=None, token=None) -> dict:
        return {"content": "rendered"}

    async def mcp_filter_servers(self, query=None, token=None) -> list[dict]:
        return []

    async def mcp_apply_server_filter(self, server_codes, timeout_seconds=30, token=None) -> dict:
        self.applied = {"server_codes": server_codes, "timeout_seconds": timeout_seconds, "token": token}
        return {"serverCodes": server_codes, "command": ["mcporter", "filter"],
                "returnCode": 0, "stdout": "ok", "stderr": ""}


class TestMcpAdapter:
    async def test_filter_servers_applies_allowlist_via_port(self):
        port = _FakeMcpPort()
        adapter = ClaudeCodeMcpAdapter(port)
        result = await adapter.filter_servers(
            MCPFilterRequest(server_codes=["a", "b"], timeout_seconds=15), auth=_auth("tk"),
        )
        assert port.applied == {"server_codes": ["a", "b"], "timeout_seconds": 15, "token": "tk"}
        assert result.server_codes == ["a", "b"]
        assert result.command == ["mcporter", "filter"]
        assert result.return_code == 0 and result.stdout == "ok"

    async def test_list_servers_builds_dtos(self):
        adapter = ClaudeCodeMcpAdapter(_FakeMcpPort())
        servers = await adapter.list_servers(auth=_auth("t"))
        assert len(servers) == 1
        s = servers[0]
        assert s.config.server_code == "s1"
        assert s.config.transport == TransportType.STDIO
        assert s.config.command == "npx"
        assert s.status == MCPServerStatus.RUNNING

    async def test_get_server_returns_none_when_port_returns_none(self):
        class _P(_FakeMcpPort):
            async def mcp_get_server(self, server_code, token=None):
                return None
        adapter = ClaudeCodeMcpAdapter(_P())  # type: ignore[arg-type]
        assert await adapter.get_server("missing") is None

    async def test_create_server_serializes_config(self):
        port = _FakeMcpPort()
        adapter = ClaudeCodeMcpAdapter(port)
        config = MCPServerConfig(
            server_code="c1", transport=TransportType.HTTP, url="http://x",
        )
        s = await adapter.create_server(config, auth=_auth("t"))
        assert s.config.server_code == "c1"
        call = port.calls[0]
        assert call["config"]["serverCode"] == "c1"
        assert call["config"]["type"] == "http"
        assert call["token"] == "t"

    async def test_lifecycle_ops_raise_capability_not_supported(self):
        adapter = ClaudeCodeMcpAdapter(_FakeMcpPort())
        with pytest.raises(CapabilityNotSupportedError):
            await adapter.start_server("s1")
        with pytest.raises(CapabilityNotSupportedError):
            await adapter.stop_server("s1")
        with pytest.raises(CapabilityNotSupportedError):
            await adapter.restart_server("s1")


    async def test_call_tool_builds_result(self):
        adapter = ClaudeCodeMcpAdapter(_FakeMcpPort())
        from engine.community.core.mcp.models import MCPToolCallRequest
        result = await adapter.call_tool(
            MCPToolCallRequest(tool_name="t1", arguments={"a": 1}, server_code="s1"),
        )
        assert result.tool_name == "t1"
        assert result.is_error is False
        assert result.content[0]["text"] == "ok"

    async def test_list_tools_returns_empty_without_server_code(self):
        adapter = ClaudeCodeMcpAdapter(_FakeMcpPort())
        assert await adapter.list_tools(server_code=None) == []


# ──────────────────────────────────────────────────────────────────────────────
# skills adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeSkillsPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def skills_list(self, token=None) -> list[dict]:
        return [{"skillId": "sk1", "name": "Skill 1", "description": "d", "skillType": "symlink"}]

    async def skills_get(self, skill_id, token=None) -> dict | None:
        if skill_id == "missing":
            return None
        return {"skillId": skill_id, "name": skill_id, "description": "", "status": "installed"}

    async def skills_install(self, config, token=None) -> dict:
        return {"skillId": config["skillId"], "name": config["skillId"], "description": ""}

    async def skills_uninstall(self, skill_id, token=None) -> bool:
        return True

    async def skills_update(self, skill_id, patch, token=None) -> dict:
        return {"skillId": skill_id, "name": skill_id, "description": ""}

    async def skills_enable(self, skill_id, token=None) -> bool:
        return True

    async def skills_disable(self, skill_id, token=None) -> bool:
        return True

    async def skills_execute(self, skill_id, args=None, token=None) -> dict:
        self.executed = True
        return {"success": True, "payload": {"done": True}}

    async def skills_validate(self, config, token=None) -> dict:
        return {"valid": True}

    async def skills_discover(self, source, token=None) -> list[dict]:
        return [{"skillId": "new", "name": "New", "description": ""}]

    async def skills_sync_symlinks(self, token=None) -> dict:
        return {"total": 2, "created": ["a"], "kept": ["b"]}

    async def skills_sync_bindpaths(self, token=None) -> dict:
        return {"total": 1, "created": ["c"]}

    async def skills_clean_symlinks(self, token=None) -> dict:
        return {"directories_scanned": 2, "removed": ["x"]}

    async def skills_ensure_center(self, token=None) -> dict:
        return {"ok": [{"skill_uuid": "u1", "version": "1.0"}],
                "failed": [{"skill_uuid": "u2", "version": "2.0", "reason": "missing"}]}


class TestSkillsAdapter:
    async def test_list_skills_builds_dtos(self):
        adapter = ClaudeCodeSkillsAdapter(_FakeSkillsPort())
        skills = await adapter.list_skills(auth=_auth("t"))
        assert len(skills) == 1
        s = skills[0]
        assert s.skill_id == "sk1"
        assert s.config.skill_type == SkillType.SYMLINK

    async def test_get_skill_returns_none(self):
        adapter = ClaudeCodeSkillsAdapter(_FakeSkillsPort())
        assert await adapter.get_skill("missing") is None

    async def test_install_passes_serialised_config(self):
        port = _FakeSkillsPort()
        adapter = ClaudeCodeSkillsAdapter(port)
        config = SkillConfig(skill_id="x", skill_type=SkillType.PACKAGE, source="pkg")
        s = await adapter.install_skill(config, auth=_auth("t"))
        assert s.skill_id == "x"

    async def test_validate_skill_returns_message_when_missing(self):
        adapter = ClaudeCodeSkillsAdapter(_FakeSkillsPort())
        result = await adapter.validate_skill("missing")
        assert len(result) == 1
        assert "not found" in result[0].lower()

    async def test_validate_skill_returns_empty_when_ok(self):
        adapter = ClaudeCodeSkillsAdapter(_FakeSkillsPort())
        assert await adapter.validate_skill("sk1") == []

    async def test_execute_skill_is_chat_triggered_noop_not_live_rpc(self):
        # SKILLS_EXECUTE is 'limited' (chat-triggered): the adapter must NOT fire
        # a live skills.execute RPC; it returns a no-op result mirroring corp.
        port = _FakeSkillsPort()
        adapter = ClaudeCodeSkillsAdapter(port)
        result = await adapter.execute_skill(
            SkillExecutionRequest(skill_id="sk1", action="run"),
            auth=_auth("t"),
        )
        assert getattr(port, "executed", False) is False
        assert result.success is False
        assert result.skill_id == "sk1" and result.action == "run"
        assert "chat" in (result.error or "").lower()

    async def test_sync_symlinks_builds_result(self):
        adapter = ClaudeCodeSkillsAdapter(_FakeSkillsPort())
        result = await adapter.sync_symlinks(
            SyncSymlinksRequest(), auth=_auth("t"),
        )
        assert result.total == 2
        assert result.created == ["a"]
        assert result.kept == ["b"]

    async def test_clean_symlinks_builds_result(self):
        adapter = ClaudeCodeSkillsAdapter(_FakeSkillsPort())
        result = await adapter.clean_symlinks(
            CleanSymlinksRequest(directories=["/d"]), auth=_auth("t"),
        )
        assert result.directories_scanned == 2
        assert result.removed == ["x"]

    async def test_ensure_center_builds_result(self):
        adapter = ClaudeCodeSkillsAdapter(_FakeSkillsPort())
        result = await adapter.ensure_center_skills(
            CenterEnsureRequest(items=[CenterEnsureItem(skill_uuid="u1", version="1.0")]),
            auth=_auth("t"),
        )
        assert len(result.ok) == 1
        assert result.ok[0].skill_uuid == "u1"
        assert len(result.failed) == 1
        assert result.failed[0].reason == "missing"


# ──────────────────────────────────────────────────────────────────────────────
# cron adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeCronPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status_response: dict[str, Any] = {
            "running": True, "jobCount": 5, "enabledCount": 3, "nextRunAtMs": 999,
        }

    async def cron_list_jobs(self, token=None) -> list[dict]:
        self.calls.append({"method": "cron_list_jobs", "token": token})
        return [{
            "id": "j1", "name": "Job 1", "enabled": True,
            "schedule": {"kind": "cron", "expr": "* * * * *"},
            "payload": {"kind": "agentTurn", "timeoutSeconds": 30},
            "state": {"running_at_ms": 123},
            "sessionTarget": "isolated", "createdAtMs": 1, "updatedAtMs": 2,
        }]

    async def cron_get_job(self, job_id, token=None) -> dict | None:
        if job_id == "missing":
            return None
        return {"id": job_id, "name": "n", "enabled": True,
                "schedule": {}, "payload": {}, "state": {}, "sessionTarget": "isolated"}

    async def cron_get_status(self, token=None) -> dict:
        self.calls.append({"method": "cron_get_status", "token": token})
        return self._status_response

    async def cron_get_runs(self, job_id, limit=20, token=None) -> list[dict]:
        return [{"jobId": job_id, "runAtMs": 10, "ts": 20, "status": "ok", "durationMs": 5}]

    async def cron_get_running_jobs(self, token=None) -> list[dict]:
        return [{"id": "j1", "name": "Job 1", "running_at_ms": 123}]

    async def cron_add_job(self, job, token=None) -> dict:
        self.calls.append({"method": "cron_add_job", "job": job, "token": token})
        return {"id": "new", "name": job["name"], "enabled": True,
                "schedule": job["schedule"], "payload": job["payload"],
                "state": {}, "sessionTarget": job["sessionTarget"]}

    async def cron_update_job(self, job_id, patch, token=None) -> dict:
        self.calls.append({"method": "cron_update_job", "job_id": job_id, "patch": patch, "token": token})
        return {"id": job_id, "name": "n", "enabled": True,
                "schedule": {}, "payload": {}, "state": {}, "sessionTarget": "isolated"}

    async def cron_remove_job(self, job_id, token=None) -> bool:
        self.calls.append({"method": "cron_remove_job", "job_id": job_id, "token": token})
        return True

    async def cron_run_job(self, job_id, token=None) -> dict:
        self.calls.append({"method": "cron_run_job", "job_id": job_id, "token": token})
        return {"ran": job_id}


class TestCronAdapter:
    async def test_list_jobs_builds_dtos_with_camel_to_snake(self):
        adapter = ClaudeCodeCronAdapter(_FakeCronPort())
        jobs = await adapter.list_jobs(auth=_auth("t"))
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "j1"
        # payload timeoutSeconds → timeout_secs (renamed, not naive snake)
        assert "timeout_secs" in j.payload
        assert j.payload["timeout_secs"] == 30
        # schedule keys are converted
        assert j.schedule == {"kind": "cron", "expr": "* * * * *"}
        # delivery absent → notify None
        assert j.notify is None

    async def test_get_job_returns_none_when_missing(self):
        adapter = ClaudeCodeCronAdapter(_FakeCronPort())
        assert await adapter.get_job("missing") is None

    async def test_get_status_builds_status(self):
        adapter = ClaudeCodeCronAdapter(_FakeCronPort())
        status = await adapter.get_status(auth=_auth("t"))
        assert status.running is True
        assert status.job_count == 5
        assert status.enabled_count == 3
        assert status.next_run_at_ms == 999

    async def test_get_runs_builds_records(self):
        adapter = ClaudeCodeCronAdapter(_FakeCronPort())
        runs = await adapter.get_runs("j1", limit=5, auth=_auth("t"))
        assert len(runs) == 1
        r = runs[0]
        assert r.job_id == "j1"
        assert r.status == "ok"
        assert r.duration_ms == 5

    async def test_add_job_serializes_request(self):
        from engine.community.plugin_api.cron.models import CreateJobRequest
        port = _FakeCronPort()
        adapter = ClaudeCodeCronAdapter(port)
        request = CreateJobRequest(
            name="N",
            schedule={"kind": "every", "every_ms": 60000},
            payload={"kind": "agentTurn", "timeout_secs": 30},
            session_target="isolated", enabled=True,
        )
        job = await adapter.add_job(request, auth=_auth("t"))
        assert job.id == "new"
        call = port.calls[0]
        # snake_case payload → camelCase wire
        assert call["job"]["payload"]["timeoutSeconds"] == 30
        # schedule every → everyMs
        assert call["job"]["schedule"]["everyMs"] == 60000

    async def test_update_job_raises_when_no_patch(self):
        from engine.community.plugin_api.cron.models import UpdateJobRequest
        adapter = ClaudeCodeCronAdapter(_FakeCronPort())
        with pytest.raises(ValueError, match="No fields"):
            await adapter.update_job("j1", UpdateJobRequest())

    async def test_run_job_returns_dict(self):
        adapter = ClaudeCodeCronAdapter(_FakeCronPort())
        result = await adapter.run_job("j1", force=True, auth=_auth("t"))
        assert result == {"ran": "j1"}

    async def test_get_running_jobs_delegates_to_port(self):
        adapter = ClaudeCodeCronAdapter(_FakeCronPort())
        running = await adapter.get_running_jobs(auth=_auth("t"))
        assert running == [{"id": "j1", "name": "Job 1", "running_at_ms": 123}]


# ──────────────────────────────────────────────────────────────────────────────
# models adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeModelsPort:
    async def models_list(self, token=None) -> list[dict]:
        return [
            {"id": "m1", "provider": "anthropic", "name": "Claude",
             "display_name": "Claude 3", "enabled": True, "default": True,
             "capabilities": {"context_window": 200000, "vision": True}},
        ]

    async def models_list_providers(self, token=None) -> list[dict]:
        return [
            {"id": "anthropic", "name": "Anthropic", "enabled": True,
             "models": [{"id": "m1", "name": "Claude"}]},
        ]


class TestModelsAdapter:
    async def test_list_models_builds_dtos(self):
        adapter = ClaudeCodeModelsAdapter(_FakeModelsPort())
        models = await adapter.list_models(auth=_auth("t"))
        assert len(models) == 1
        m = models[0]
        assert m.id == "m1"
        assert m.provider == "anthropic"
        assert m.display_name == "Claude 3"
        assert m.default is True
        assert m.capabilities.context_window == 200000
        assert m.capabilities.vision is True

    async def test_list_providers_builds_nested_models(self):
        adapter = ClaudeCodeModelsAdapter(_FakeModelsPort())
        providers = await adapter.list_providers(auth=_auth("t"))
        assert len(providers) == 1
        p = providers[0]
        assert p.id == "anthropic"
        assert len(p.models) == 1
        assert p.models[0].id == "m1"


# ──────────────────────────────────────────────────────────────────────────────
# file adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeFilePort:
    async def file_upload(self, path, content_bytes=None, token=None) -> dict:
        return {"target_path": path, "size": len(content_bytes or b""), "overwritten": False}

    async def file_read(self, path, token=None) -> dict:
        return {"content": "hello body"}

    async def file_remove(self, path, token=None) -> bool:
        return True

    async def file_rmtree(self, path, token=None) -> bool:
        return True

    async def file_list_dir(self, path, token=None) -> list[dict]:
        return [{"name": "f.txt", "path": "/d/f.txt", "relative_path": "f.txt",
                 "is_dir": False, "size": 10}]


class TestFileAdapter:
    async def test_upload_builds_upload_result(self):
        adapter = ClaudeCodeFileAdapter(_FakeFilePort())
        result = await adapter.upload("/d/f.txt", b"data", auth=_auth("t"))
        assert result.target_path == "/d/f.txt"
        assert result.size == 4
        assert result.overwritten is False

    async def test_read_returns_bytes_from_content(self):
        adapter = ClaudeCodeFileAdapter(_FakeFilePort())
        body = await adapter.read("/d/f.txt", auth=_auth("t"))
        assert body == b"hello body"

    async def test_remove_builds_remove_result(self):
        adapter = ClaudeCodeFileAdapter(_FakeFilePort())
        result = await adapter.remove("/d/f.txt", auth=_auth("t"))
        assert result.target_path == "/d/f.txt"
        assert result.path_type == "file"

    async def test_rmtree_returns_target_path(self):
        adapter = ClaudeCodeFileAdapter(_FakeFilePort())
        path = await adapter.rmtree("/d", auth=_auth("t"))
        assert path == "/d"

    async def test_list_dir_builds_entries(self):
        adapter = ClaudeCodeFileAdapter(_FakeFilePort())
        result = await adapter.list_dir("/d", recursive=True, auth=_auth("t"))
        assert result.dir_path == "/d"
        assert result.recursive is True
        assert len(result.files) == 1
        assert result.files[0].name == "f.txt"
        assert result.files[0].size == 10


# ──────────────────────────────────────────────────────────────────────────────
# relay adapter
# ──────────────────────────────────────────────────────────────────────────────


class _FakeRelayPort:
    def __init__(self) -> None:
        self.forward_request_calls: list[dict[str, Any]] = []
        self.forward_raw_frame_calls: list[dict[str, Any]] = []

    async def relay_forward_request(self, method, params=None, request_id=None, token=None) -> dict:
        self.forward_request_calls.append({
            "method": method, "params": params, "request_id": request_id, "token": token,
        })
        return {"id": request_id, "ok": True, "payload": {"relayed": True}}

    async def relay_forward_raw_frame(self, frame, token=None) -> dict:
        self.forward_raw_frame_calls.append({"frame": frame, "token": token})
        return {"ack": True}


class TestRelayAdapter:
    async def test_forward_request_extracts_token_and_returns_response_frame(self):
        port = _FakeRelayPort()
        adapter = ClaudeCodeRelayAdapter(port)
        resp = await adapter.forward_request(
            request_id="req-1", method="interaction.resolve",
            params={"x": 1}, auth=_auth("tok"),
        )
        assert isinstance(resp, ResponseFrame)
        assert resp.id == "req-1"
        assert resp.ok is True
        assert resp.payload == {"relayed": True}
        assert port.forward_request_calls[0]["token"] == "tok"
        assert port.forward_request_calls[0]["method"] == "interaction.resolve"

    async def test_forward_raw_frame_returns_none(self):
        port = _FakeRelayPort()
        adapter = ClaudeCodeRelayAdapter(port)
        result = await adapter.forward_raw_frame({"type": "ping"}, auth=_auth("t"))
        assert result is None
        assert port.forward_raw_frame_calls[0]["token"] == "t"
