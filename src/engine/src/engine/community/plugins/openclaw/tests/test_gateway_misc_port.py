"""Port-impl tests for the remaining gateway transport methods: relay, approval,
chat_abort. Preserves the legacy test_plugin_surface coverage for these services
at the port layer (their RPC behavior moved into OpenClawPluginImpl). DTO building
is covered by core/adapters/openclaw/tests/.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from engine.community.kernel.frames import ErrorShape, ResponseFrame
from engine.community.openclaw.config import OpenClawConfig, reset_config, set_config
from engine.community.plugins.openclaw import _chat as chat_mod
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
        self._send_request_exc: Exception | None = None
        self._send_with_id_response: ResponseFrame | None = None
        self._chat_abort_result: dict = {"success": True, "payload": {}}

    async def send_request(self, method: str, params: dict | None = None, timeout: Any = None):
        self.send_request_calls.append({"method": method, "params": params, "timeout": timeout})
        if self._send_request_exc is not None:
            raise self._send_request_exc
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
    def __init__(self, client: _FakeClient, exc: Exception | None = None):
        self._client = client
        self._exc = exc

    async def get(self, token: str | None = None) -> _FakeClient:
        if self._exc is not None:
            raise self._exc
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


async def test_chat_inject_connect_failure_returns_internal_error():
    client = _FakeClient()
    impl = OpenClawPluginImpl(pool=_FakePool(client, exc=RuntimeError("pool down")))

    out = await impl.chat_inject("sk-1", "hello")

    assert out["success"] is False
    assert out["error"]["code"] == "INTERNAL_ERROR"
    assert "pool down" in out["error"]["message"]


async def test_chat_inject_sends_label_when_present():
    impl, client = _impl()
    client._send_request_responses = {
        "chat.inject": [ResponseFrame(id="1", ok=True, payload={"ok": True})],
        "sessions.describe": [ResponseFrame(id="2", ok=True, payload={"session": {}})],
    }

    out = await impl.chat_inject("sk-1", "hello", label="BCS")

    assert out["success"] is True
    assert client.send_request_calls[0]["params"] == {
        "sessionKey": "sk-1",
        "message": "hello",
        "label": "BCS",
    }


async def test_chat_inject_patch_failure_returns_gateway_error():
    impl, client = _impl()
    client._send_request_responses = {
        "chat.inject": [
            ResponseFrame(
                id="1",
                ok=False,
                error=ErrorShape("INVALID_REQUEST", "session not found"),
            )
        ],
        "sessions.patch": [
            ResponseFrame(id="2", ok=False, error=ErrorShape("PATCH_FAILED", "no patch")),
        ],
    }

    out = await impl.chat_inject("sk-1", "hello")

    assert out == {
        "success": False,
        "error": {"code": "PATCH_FAILED", "message": "no patch"},
    }


async def test_chat_inject_non_missing_failure_returns_gateway_error():
    impl, client = _impl()
    client._send_request_responses = {
        "chat.inject": [
            ResponseFrame(id="1", ok=False, error=ErrorShape("UNAVAILABLE", "gateway down")),
        ],
    }

    out = await impl.chat_inject("sk-1", "hello")

    assert out == {
        "success": False,
        "error": {"code": "UNAVAILABLE", "message": "gateway down"},
    }


async def test_chat_inject_rpc_exception_returns_internal_error():
    impl, client = _impl()
    client._send_request_exc = RuntimeError("rpc down")

    out = await impl.chat_inject("sk-1", "hello")

    assert out["success"] is False
    assert out["error"]["code"] == "INTERNAL_ERROR"
    assert "rpc down" in out["error"]["message"]


async def test_chat_inject_describe_failure_keeps_success_payload():
    impl, client = _impl()
    client._send_request_responses = {
        "chat.inject": [ResponseFrame(id="1", ok=True, payload={"ok": True, "messageId": "m1"})],
        "sessions.describe": [
            ResponseFrame(id="2", ok=False, error=ErrorShape("UNAVAILABLE", "describe down")),
        ],
    }

    out = await impl.chat_inject("sk-1", "hello")

    assert out == {"success": True, "payload": {"ok": True, "messageId": "m1"}}


async def test_chat_inject_missing_message_id_skips_rewrite():
    impl, client = _impl()
    client._send_request_responses = {
        "chat.inject": [ResponseFrame(id="1", ok=True, payload={"ok": True})],
        "sessions.describe": [
            ResponseFrame(id="2", ok=True, payload={"session": {"sessionId": "safe-session"}}),
        ],
    }

    out = await impl.chat_inject("sk-1", "hello")

    assert out == {"success": True, "payload": {"ok": True, "sessionId": "safe-session"}}


def test_failure_from_response_falls_back_without_error():
    out = chat_mod._failure_from_response(
        ResponseFrame(id="1", ok=False),
        "fallback",
    )

    assert out == {"success": False, "error": {"code": "UNKNOWN", "message": "fallback"}}


def test_is_session_not_found_false_for_ok_or_different_error():
    assert chat_mod._is_session_not_found_response(ResponseFrame(id="1", ok=True)) is False
    assert (
        chat_mod._is_session_not_found_response(
            ResponseFrame(id="2", ok=False, error=ErrorShape("UNAVAILABLE", "session not found"))
        )
        is False
    )


def test_resolve_inject_transcript_path_rejects_unsafe_session_id():
    assert chat_mod._resolve_inject_transcript_path("../bad") is None


def test_resolve_inject_transcript_path_rejects_escaped_path():
    with patch.object(chat_mod.Path, "relative_to", side_effect=ValueError):
        assert chat_mod._resolve_inject_transcript_path("safe-session") is None


def test_rewrite_injected_transcript_message_handles_empty_and_malformed_lines(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "version": 3, "id": "s1"}),
                "",
                "{bad-json",
                json.dumps(
                    {
                        "type": "message",
                        "id": "m1",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "hello"}],
                            "model": "gateway-injected",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert chat_mod._rewrite_injected_transcript_message(transcript, "m1") is True

    lines = transcript.read_text(encoding="utf-8").splitlines()
    assert lines[1] == ""
    assert lines[2] == "{bad-json"
    entry = json.loads(lines[3])
    assert entry["message"]["role"] == "user"
    assert "model" not in entry["message"]


def test_rewrite_injected_transcript_message_returns_false_when_message_missing(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text(
        json.dumps({"type": "session", "version": 3, "id": "s1"}) + "\n",
        encoding="utf-8",
    )

    assert chat_mod._rewrite_injected_transcript_message(transcript, "missing") is False


def test_rewrite_injected_transcript_message_returns_false_when_read_fails(tmp_path):
    missing = tmp_path / "missing.jsonl"

    assert chat_mod._rewrite_injected_transcript_message(missing, "m1") is False


def test_rewrite_injected_transcript_message_cleans_tmp_on_write_failure(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "m1",
                "message": {"role": "assistant", "content": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with patch.object(chat_mod.os, "replace", side_effect=OSError("replace failed")):
        assert chat_mod._rewrite_injected_transcript_message(transcript, "m1") is False

    assert [path for path in tmp_path.iterdir() if path.name.endswith(".tmp")] == []


def test_rewrite_injected_transcript_message_ignores_tmp_cleanup_failure(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "m1",
                "message": {"role": "assistant", "content": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with (
        patch.object(chat_mod.os, "replace", side_effect=OSError("replace failed")),
        patch.object(chat_mod.os, "unlink", side_effect=OSError("unlink failed")),
    ):
        assert chat_mod._rewrite_injected_transcript_message(transcript, "m1") is False
