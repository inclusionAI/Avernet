"""Port-impl tests for the claude_code community transport (Stage 2).

Drives ``ClaudeCodePluginImpl`` against a fake relay client so the
relay→EventFrame translation, rpc param shaping, and in-band error
conventions are pinned at the transport layer — the adapter tests
(Stage 3) use a fake *port* and never exercise this.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from engine.community.plugins.claude_code._base import (
    ClaudeCodePortBase,
    ClaudeCodeRelayClient,
)


class _FakeRelayClient:
    """Minimal stand-in for ClaudeCodeRelayClient used by every domain test.

    Records every call and returns canned responses. Each test configures
    the attributes it needs; unset RPCs raise so missing coverage is loud.
    """

    def __init__(self) -> None:
        self.connected = True
        self.calls: list[tuple[str, tuple, dict]] = []
        self._responses: dict[str, Any] = {}
        self._events: list[dict[str, Any]] = []
        self.hello = None

    def set_response(self, method: str, resp: Any) -> None:
        self._responses[method] = resp

    async def chat_stream(self, **kwargs: Any):
        self.calls.append(("chat_stream", (), dict(kwargs)))
        for e in self._events:
            yield dict(e)

    async def send_request(self, method: str, params: dict | None = None,
                           timeout: float = 30.0) -> Any:
        self.calls.append(("send_request", (method,), {"params": params, "timeout": timeout}))
        return self._responses.get(method, _ok({}))

    async def send_request_with_events(self, method, params, event_names,
                                       session_key=None, response_timeout=30.0):
        self.calls.append(("send_request_with_events", (method,),
                           {"params": params, "event_names": event_names,
                            "session_key": session_key}))
        return self._responses.get(method, (_ok({}), []))

    async def send_request_with_id(self, request_id, method, params, timeout=30.0):
        self.calls.append(("send_request_with_id", (method,),
                           {"request_id": request_id, "params": params, "timeout": timeout}))
        return self._responses.get(method, _ok({}))


def _ok(payload: Any) -> Any:
    from engine.community.kernel.frames import ResponseFrame
    return ResponseFrame(id="x", ok=True, payload=payload)


def _err(code: str, message: str) -> Any:
    from engine.community.kernel.frames import ErrorShape, ResponseFrame
    return ResponseFrame(id="x", ok=False, error=ErrorShape(code=code, message=message))


def _impl(client: _FakeRelayClient | None = None) -> tuple[Any, _FakeRelayClient]:
    from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl
    c = client or _FakeRelayClient()
    return ClaudeCodePluginImpl(client=c), c


# ── chat ─────────────────────────────────────────────────────────────────────


async def test_chat_stream_translates_events_to_eventframes_and_stops_on_terminal():
    c = _FakeRelayClient()
    c._events = [
        {"state": "delta", "runId": "r1"},
        {"state": "final", "runId": "r1"},
        {"state": "delta", "runId": "r1"},  # never reached
    ]
    impl, client = _impl(c)
    frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
    assert len(frames) == 2
    assert frames[0].event == "agent"
    assert frames[-1].payload["state"] == "final"
    # sessionKey injected into payload
    assert frames[0].payload["sessionKey"] == "s"


async def test_chat_stream_final_frame_preserves_message_content():
    """Load-bearing contract: the frontend (AICodingParser) renders the assistant
    bubble directly from the final frame's ``message.content`` text blocks — NOT
    from a synthetic delta (community deliberately drops corp's final->delta
    synthesis). So the community transport MUST pass ``message.content`` through
    the final frame unmodified, or bubbles render empty.
    """
    c = _FakeRelayClient()
    c._events = [
        {"state": "delta", "runId": "r1"},
        {
            "state": "final",
            "runId": "r1",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello world"}],
            },
        },
    ]
    impl, _ = _impl(c)
    frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
    final = frames[-1]
    assert final.payload["state"] == "final"
    assert final.payload["message"]["role"] == "assistant"
    assert final.payload["message"]["content"][0]["text"] == "hello world"


async def test_chat_stream_inject_runid_final_does_not_stop():
    c = _FakeRelayClient()
    c._events = [
        {"state": "final", "runId": "inject-x"},  # yielded, NOT a stop
        {"state": "final", "runId": "r1"},        # real final → stop
    ]
    impl, _ = _impl(c)
    frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
    assert len(frames) == 2


async def test_chat_stream_forwards_optional_params():
    captured: dict = {}

    class _C(_FakeRelayClient):
        async def chat_stream(self, **kwargs):
            captured.update(kwargs)
            if False:  # pragma: no cover
                yield {}

    impl, _ = _impl(_C())
    _ = [f async for f in impl.chat_stream(
        session_key="s", message="m", timeout_ms=1000, cwd="/tmp",
        model="claude-x", permission_mode="plan")]
    assert captured["session_key"] == "s"
    assert captured["timeout_ms"] == 1000
    assert captured["cwd"] == "/tmp"
    assert captured["model"] == "claude-x"
    assert captured["permission_mode"] == "plan"


async def test_chat_abort_maps_to_chat_abort_rpc():
    c = _FakeRelayClient()
    c.set_response("chat.abort", _ok({"aborted": True}))
    impl, _ = _impl(c)
    out = await impl.chat_abort(session_key="s", run_id="r1")
    assert out["success"] is True
    method, args, _ = c.calls[-1]
    assert method == "send_request" and args[0] == "chat.abort"


async def test_chat_abort_returns_synthetic_failure_on_rpc_error():
    c = _FakeRelayClient()
    c.set_response("chat.abort", _err("INTERNAL", "boom"))
    impl, _ = _impl(c)
    out = await impl.chat_abort(session_key="s", run_id="r1")
    assert out["success"] is False
    assert out["error"]["code"] == "INTERNAL"


async def test_chat_inject_maps_to_chat_inject_rpc():
    c = _FakeRelayClient()
    c.set_response("chat.inject", _ok({"ok": True}))
    impl, _ = _impl(c)
    out = await impl.chat_inject(session_key="s", message="hi", label="L")
    assert out["success"] is True


async def test_resolve_exec_approval_uses_interaction_resolve():
    c = _FakeRelayClient()
    c.set_response("interaction.resolve", (_ok({}), []))
    impl, _ = _impl(c)
    out = await impl.resolve_exec_approval(
        session_key="s", run_id="r1", decision="allow-once", message="ok")
    assert out["success"] is True
    method, args, kw = c.calls[-1]
    assert method == "send_request_with_events"
    assert args[0] == "interaction.resolve"
    assert kw["params"]["interactionId"] == "r1"
    assert kw["params"]["decision"] == "allow-once"


async def test_resolve_interaction_uses_interaction_resolve_submit():
    c = _FakeRelayClient()
    c.set_response("interaction.resolve", (_ok({}), []))
    impl, _ = _impl(c)
    out = await impl.resolve_interaction(
        session_key="s", run_id="r1", response="answer")
    assert out["success"] is True
    method, _, kw = c.calls[-1]
    assert kw["params"]["action"] == "submit"
    assert kw["params"]["message"] == "answer"


async def test_resolve_mode_transition_uses_mode_transition_resolve():
    c = _FakeRelayClient()
    c.set_response("mode_transition.resolve", (_ok({}), []))
    impl, _ = _impl(c)
    out = await impl.resolve_mode_transition(
        session_key="s", run_id="r1", decision="proceed")
    assert out["success"] is True
    method, args, _ = c.calls[-1]
    assert method == "send_request_with_events" and args[0] == "mode_transition.resolve"


# ── session ──────────────────────────────────────────────────────────────────


async def test_sessions_list_returns_raw_dicts():
    c = _FakeRelayClient()
    c.set_response("sessions.list", _ok({"sessions": [{"key": "a"}, {"key": "b"}]}))
    impl, _ = _impl(c)
    out = await impl.sessions_list(token=None, offset=0, limit=50, agent_id=None)
    assert [s["key"] for s in out] == ["a", "b"]


async def test_session_create_calls_sessions_patch():
    c = _FakeRelayClient()
    c.set_response("sessions.patch", _ok({"key": "k"}))
    impl, _ = _impl(c)
    await impl.session_create(key="k", label="L", model="m", cwd="/x")
    _, args, kw = c.calls[-1]
    assert args[0] == "sessions.patch"
    assert kw["params"]["key"] == "k"
    assert kw["params"]["label"] == "L"
    assert kw["params"]["cwd"] == "/x"


async def test_session_delete_returns_bool():
    c = _FakeRelayClient()
    c.set_response("sessions.delete", _ok({"deleted": True}))
    impl, _ = _impl(c)
    assert await impl.session_delete(key="k") is True


async def test_session_get_history_calls_chat_history():
    c = _FakeRelayClient()
    c.set_response("chat.history", _ok({"messages": [{"role": "user"}]}))
    impl, _ = _impl(c)
    out = await impl.session_get_history(key="k", limit=50)
    assert len(out) == 1


async def test_session_reset_in_band_success():
    c = _FakeRelayClient()
    c.set_response("sessions.reset", _ok({"ok": True}))
    impl, _ = _impl(c)
    out = await impl.session_reset(key="k")
    assert out["success"] is True


# ── models ───────────────────────────────────────────────────────────────────


async def test_models_list_extracts_models_array():
    c = _FakeRelayClient()
    c.set_response("models.list", _ok({"models": [{"id": "claude-x"}]}))
    impl, _ = _impl(c)
    out = await impl.models_list()
    assert out == [{"id": "claude-x"}]


async def test_models_list_providers_extracts_providers():
    c = _FakeRelayClient()
    c.set_response("providers.list", _ok({"providers": [{"id": "anthropic"}]}))
    impl, _ = _impl(c)
    out = await impl.models_list_providers()
    assert out == [{"id": "anthropic"}]


# ── commands ─────────────────────────────────────────────────────────────────


async def test_commands_list_calls_commands_list():
    c = _FakeRelayClient()
    c.set_response("commands.list", _ok({"commands": [{"id": "c1"}]}))
    impl, _ = _impl(c)
    out = await impl.commands_list(scope="builtin")
    assert out == [{"id": "c1"}]


async def test_commands_get_returns_none_when_not_found():
    c = _FakeRelayClient()
    c.set_response("commands.get", _err("NOT_FOUND", "nope"))
    impl, _ = _impl(c)
    assert await impl.commands_get(command_id="c1") is None


# ── relay ────────────────────────────────────────────────────────────────────


async def test_relay_forward_request_returns_dict():
    c = _FakeRelayClient()
    c.set_response("foo.bar", _ok({"done": True}))
    impl, _ = _impl(c)
    out = await impl.relay_forward_request(method="foo.bar", params={"a": 1})
    assert out["success"] is True
    assert out["payload"] == {"done": True}


# ── cron ─────────────────────────────────────────────────────────────────────


async def test_cron_list_jobs_returns_jobs_array():
    c = _FakeRelayClient()
    c.set_response("cron.list", _ok({"jobs": [{"id": "j1"}]}))
    impl, _ = _impl(c)
    out = await impl.cron_list_jobs()
    assert out == [{"id": "j1"}]


async def test_cron_remove_job_returns_bool():
    c = _FakeRelayClient()
    c.set_response("cron.remove", _ok({"removed": True}))
    impl, _ = _impl(c)
    assert await impl.cron_remove_job(job_id="j1") is True


# ── mcp ──────────────────────────────────────────────────────────────────────


async def test_mcp_list_servers_extracts_servers():
    c = _FakeRelayClient()
    c.set_response("mcp.config.list", _ok({"servers": [{"serverCode": "s1"}]}))
    impl, _ = _impl(c)
    out = await impl.mcp_list_servers()
    assert out == [{"serverCode": "s1"}]


async def test_mcp_delete_server_returns_bool():
    c = _FakeRelayClient()
    c.set_response("mcp.config.delete", _ok({"deleted": True}))
    impl, _ = _impl(c)
    assert await impl.mcp_delete_server(server_code="s1") is True


# ── skills ───────────────────────────────────────────────────────────────────


async def test_skills_list_extracts_skills():
    c = _FakeRelayClient()
    c.set_response("skills.list", _ok({"skills": [{"skillId": "x"}]}))
    impl, _ = _impl(c)
    out = await impl.skills_list()
    assert out == [{"skillId": "x"}]


async def test_skills_uninstall_returns_bool():
    c = _FakeRelayClient()
    c.set_response("skills.uninstall", _ok({"removed": True}))
    impl, _ = _impl(c)
    assert await impl.skills_uninstall(skill_id="x") is True


# ── plugin_impl facade ───────────────────────────────────────────────────────


def test_plugin_impl_satisfies_aggregate_port():
    from engine.community.plugin_api.claude_code.plugin import ClaudeCodePlugin
    _, _ = _impl()
    # ClaudeCodePluginImpl explicitly inherits the facade; the type check
    # is structural. Just assert the impl class declares it as a base.
    from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl
    assert ClaudeCodePlugin in ClaudeCodePluginImpl.__mro__


# ── transport robustness (Phase 5) ────────────────────────────────────────────


async def test_disconnect_fails_pending_futures():
    """disconnect() must reject in-flight request futures, not leave callers
    blocked until their own wait_for timeout."""
    client = ClaudeCodeRelayClient()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    client._pending["1"] = fut
    client._connected = True

    class _WS:
        async def close(self) -> None:
            return None

    client._ws = _WS()
    await client.disconnect()
    assert fut.done()
    with pytest.raises(ConnectionError):
        fut.result()


async def test_send_request_on_unconnected_client_raises_connectionerror():
    """send_request on a never-connected client raises ConnectionError, not
    AttributeError on ``self._ws.send``."""
    client = ClaudeCodeRelayClient()  # _ws is None, not connected
    with pytest.raises(ConnectionError):
        await client.send_request("x.y", {})


def test_skills_sync_signatures_match_port_protocol():
    """Community skills sync_* must match the port Protocol (token-only, no
    out-of-contract ``params`` positional)."""
    import inspect

    from engine.community.plugin_api.claude_code.skills import ClaudeCodeSkillsPort
    from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl

    for name in ("skills_sync_symlinks", "skills_sync_bindpaths",
                 "skills_clean_symlinks", "skills_ensure_center"):
        proto = list(inspect.signature(getattr(ClaudeCodeSkillsPort, name)).parameters)
        impl = list(inspect.signature(getattr(ClaudeCodePluginImpl, name)).parameters)
        assert impl == proto, f"{name}: impl {impl} != protocol {proto}"


async def test_chat_stream_forwards_attachments():
    """attachments passed to the port must reach the relay client (not dropped)."""
    c = _FakeRelayClient()
    c._events = [{"state": "final", "runId": "r1"}]
    impl, client = _impl(c)
    _ = [f async for f in impl.chat_stream(
        session_key="s", message="m", attachments=[{"path": "a.png"}])]
    call = next(kw for (n, _a, kw) in client.calls if n == "chat_stream")
    assert call.get("attachments") == [{"path": "a.png"}]
