"""Port-impl tests for the remaining gateway transport methods: relay, approval,
chat_abort. Preserves the legacy test_plugin_surface coverage for these services
at the port layer (their RPC behavior moved into OpenClawPluginImpl). DTO building
is covered by core/adapters/openclaw/tests/.
"""
from __future__ import annotations

import json
from typing import Any

from engine.community.kernel.frames import ErrorShape, ResponseFrame
from engine.community.openclaw.config import OpenClawConfig, reset_config, set_config
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


class _FakeClient:
    def __init__(self):
        self.connected = True
        self.send_request_calls: list[dict] = []
        self.send_with_id_calls: list[tuple] = []
        self.raw_frames: list[dict] = []
        self.chat_abort_calls: list[dict] = []
        self._send_request_response: ResponseFrame | None = None
        self._send_request_responses: dict[str, list[ResponseFrame]] = {}
        self._send_with_id_response: ResponseFrame | None = None
        self._chat_abort_result: dict = {"success": True, "payload": {}}

    async def send_request(self, method: str, params: dict | None = None, timeout: Any = None):
        self.send_request_calls.append({"method": method, "params": params, "timeout": timeout})
        queued = self._send_request_responses.get(method)
        if queued:
            return queued.pop(0)
        return self._send_request_response

    async def send_request_with_id(self, request_id, method, params, timeout):
        self.send_with_id_calls.append((request_id, method, params, timeout))
        return self._send_with_id_response

    async def send_raw_frame(self, frame: dict):
        self.raw_frames.append(frame)

    async def chat_abort(self, session_key, run_id):
        self.chat_abort_calls.append({"session_key": session_key, "run_id": run_id})
        return self._chat_abort_result


class _FakePool:
    def __init__(self, client: _FakeClient):
        self._client = client

    async def get(self, token: str | None = None) -> _FakeClient:
        return self._client


def _impl() -> tuple[OpenClawPluginImpl, _FakeClient]:
    client = _FakeClient()
    return OpenClawPluginImpl(pool=_FakePool(client)), client


# ── relay ──
async def test_forward_request_passes_through_to_client():
    impl, client = _impl()
    client._send_with_id_response = ResponseFrame(id="req-1", ok=True, payload={"x": 1})
    out = await impl.forward_request("req-1", "some.method", {"p": 1}, token="tok", timeout=12.0)
    assert isinstance(out, ResponseFrame)
    assert out.payload == {"x": 1}
    assert client.send_with_id_calls == [("req-1", "some.method", {"p": 1}, 12.0)]


async def test_forward_raw_frame_passes_through():
    impl, client = _impl()
    await impl.forward_raw_frame({"type": "event", "event": "x"}, token="tok")
    assert client.raw_frames == [{"type": "event", "event": "x"}]


# ── approval ──
async def test_approvals_get_ok():
    impl, client = _impl()
    client._send_request_response = ResponseFrame(id="1", ok=True, payload={"mode": "auto"})
    out = await impl.approvals_get(session_key="s1", token="tok")
    assert out["ok"] is True
    assert out["payload"] == {"mode": "auto"}
    call = client.send_request_calls[0]
    assert call["method"] == "exec.approvals.get"
    assert call["params"] == {"sessionKey": "s1"}


async def test_approvals_get_not_ok_returns_error_dict():
    impl, client = _impl()
    client._send_request_response = ResponseFrame(id="1", ok=False, error=ErrorShape(code="E", message="nope"))
    out = await impl.approvals_get(session_key="s1")
    assert out["ok"] is False
    assert "nope" in out["error"]


async def test_approvals_set_sends_mode():
    impl, client = _impl()
    client._send_request_response = ResponseFrame(id="1", ok=True, payload={})
    out = await impl.approvals_set(session_key="s1", mode="manual", token="tok")
    assert out["ok"] is True
    call = client.send_request_calls[0]
    assert call["method"] == "exec.approvals.set"
    assert call["params"] == {"sessionKey": "s1", "mode": "manual"}


# ── chat_abort ──
async def test_chat_abort_returns_raw_dict():
    impl, client = _impl()
    client._chat_abort_result = {"success": True, "payload": {"runId": "r1", "aborted": True}}
    out = await impl.chat_abort("s1", "r1", token="tok")
    assert out == {"success": True, "payload": {"runId": "r1", "aborted": True}}
    assert client.chat_abort_calls == [{"session_key": "s1", "run_id": "r1"}]


async def test_chat_abort_wraps_exception_in_failure_dict():
    impl, client = _impl()

    async def _boom(session_key, run_id):
        raise RuntimeError("rpc down")

    client.chat_abort = _boom  # type: ignore[assignment]
    out = await impl.chat_abort("s1", "r1")
    assert out["success"] is False
    assert "error" in out


async def test_chat_inject_rewrites_injected_transcript_entry(tmp_path):
    impl, client = _impl()
    session_id = "19db4f1f-92d2-4287-939d-c5bc5d9188e7"
    transcript = tmp_path / f"{session_id}.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": session_id}),
                json.dumps(
                    {
                        "type": "message",
                        "id": "inject-message-1",
                        "parentId": None,
                        "timestamp": "2026-08-05T00:00:00.000Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "observe me"}],
                            "api": "openai-responses",
                            "provider": "openclaw",
                            "model": "gateway-injected",
                            "stopReason": "stop",
                            "usage": {"totalTokens": 0},
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    set_config(OpenClawConfig(session_transcript_dir=str(tmp_path)))
    client._send_request_responses = {
        "chat.inject": [
            ResponseFrame(id="1", ok=True, payload={"ok": True, "messageId": "inject-message-1"})
        ],
        "sessions.describe": [
            ResponseFrame(id="2", ok=True, payload={"session": {"sessionId": session_id}})
        ],
    }

    try:
        out = await impl.chat_inject("sk-1", "observe me", token="tok")

        assert out == {
            "success": True,
            "payload": {"ok": True, "messageId": "inject-message-1", "sessionId": session_id},
        }
        assert [call["method"] for call in client.send_request_calls] == [
            "chat.inject",
            "sessions.describe",
        ]
        injected = json.loads(transcript.read_text(encoding="utf-8").splitlines()[1])
        assert injected["message"]["role"] == "user"
        assert injected["message"]["content"] == [{"type": "text", "text": "observe me"}]
        assert "provider" not in injected["message"]
        assert "model" not in injected["message"]
        assert "api" not in injected["message"]
        assert "stopReason" not in injected["message"]
        assert "usage" not in injected["message"]
    finally:
        reset_config()


async def test_chat_inject_creates_missing_session_then_retries():
    impl, client = _impl()
    client._send_request_responses = {
        "chat.inject": [
            ResponseFrame(
                id="1",
                ok=False,
                error=ErrorShape("INVALID_REQUEST", "session not found"),
            ),
            ResponseFrame(id="3", ok=True, payload={"ok": True, "messageId": "m1"}),
        ],
        "sessions.patch": [
            ResponseFrame(id="2", ok=True, payload={"ok": True}),
        ],
        "sessions.describe": [
            ResponseFrame(id="4", ok=True, payload={"session": {"sessionId": "s1"}}),
        ],
    }

    out = await impl.chat_inject("sk-1", "hello")

    assert out["success"] is True
    assert [call["method"] for call in client.send_request_calls] == [
        "chat.inject",
        "sessions.patch",
        "chat.inject",
        "sessions.describe",
    ]
