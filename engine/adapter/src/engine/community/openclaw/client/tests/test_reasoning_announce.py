import asyncio
import json
from uuid import uuid4

import pytest

from engine.community.openclaw.client.tests.test_gateway_client import (
    ResponseFrame, _accepted_sessions_spawn_data, _fire_event, _make_client, _text_message,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "retry", "duplicate", "keyless", "wire_final", "wrong_session", "wrong_run",
    "no_end", "other_branch", "wrong_text", "tool_use", "silent", "aborted", "error",
    "other_receipt", "missing_transcript", "ordinary_empty", "earlier_lifecycle_end",
    "stock", "wrong_header", "wrong_history_key", "second_retry", "keyless_error",
    "no_followup", "already_persisted", "describe_error", "large_stock", "parent_resumed",
])
async def test_yielded_announce(case, tmp_path, monkeypatch):
    from engine.community.openclaw.client import reasoning_announce
    monkeypatch.setattr(reasoning_announce, "RETRY_TIMEOUT", 0.22)
    client = _make_client()
    client._config.session_transcript_dir = str(tmp_path)
    parent = "agent:main:session:test"
    sid = str(uuid4())
    path = tmp_path / f"{sid}.jsonl"
    spawn = _accepted_sessions_spawn_data()
    receipt = dict(spawn["result"]["details"])
    child = receipt["childSessionKey"]
    run = f"announce:v1:{child}:{receipt['runId']}"
    wire = {"runId": run, "sessionKey": parent}
    meta = {"sessionKey": parent}
    if case in {"stock", "wrong_header", "large_stock"}:
        meta.clear()
    if case == "wrong_history_key":
        meta["sessionKey"] = "other"
    if case == "other_receipt":
        receipt["runId"] = "different"
    rows = [
        {"type": "session", "id": sid if case != "wrong_header" else str(uuid4())},
        {"id": "s", "message": {"role": "toolResult", "toolName": "sessions_spawn",
                                "content": _text_message(json.dumps(receipt))["content"]}},
        {"id": "y", "parentId": "s", "customType": "openclaw.sessions_yield"},
        {"id": "t", "parentId": "y", "message": {"role": "assistant", "stopReason": "stop",
         "historyMeta": meta, "content": [{"type": "thinking", "thinking": "fixture"}]}},
    ]
    if case == "ordinary_empty":
        rows[-1]["message"]["content"] = []
    if case == "large_stock":
        rows.insert(1, {"type": "custom", "data": "x" * (512 * 1024)})
    if case != "missing_transcript":
        path.write_text("\n".join(map(json.dumps, rows)) + "\n")
    final_text = "NO_REPLY" if case == "silent" else "verified reply"

    def continuation():
        if case == "parent_resumed":
            _fire_event(client, "agent", {"sessionKey": parent, "runId": "resumed", "stream": "lifecycle",
                                          "data": {"phase": "start"}})
            _fire_event(client, "chat", dict(wire, state="final"))
            asyncio.get_running_loop().call_later(
                0.3, _fire_event, client, "chat", {"sessionKey": parent, "runId": "resumed",
                                                  "state": "final", "message": _text_message("parent answer")})
            return
        if case in {"error", "aborted"}:
            _fire_event(client, "chat", dict(wire, state=case))
            return
        if case in {"ordinary_empty", "missing_transcript", "other_receipt", "wrong_header",
                    "wrong_history_key", "no_followup"}:
            return
        if case == "wire_final":
            _fire_event(client, "chat", dict(wire, state="final", message=_text_message("real final")))
            return
        if case == "keyless_error":
            _fire_event(client, "agent", {"runId": run, "stream": "lifecycle", "data": {"phase": "error"}})
            return
        obs = dict(wire)
        if case == "keyless":
            obs.pop("sessionKey")
        elif case == "wrong_session":
            obs["sessionKey"] = "other"
        elif case == "wrong_run":
            obs["runId"] = "announce:foreign"
        if case == "earlier_lifecycle_end":
            _fire_event(client, "agent", dict(obs, stream="lifecycle", data={"phase": "end"}))
        _fire_event(client, "agent", dict(obs, stream="assistant", data={"text": final_text}))
        reply = dict(_text_message("different" if case == "wrong_text" else final_text),
                     historyMeta=meta, stopReason="toolUse" if case == "tool_use" else "stop")
        with path.open("a") as f:
            predecessor = "other" if case == "other_branch" else "t"
            if case == "second_retry":
                f.write(json.dumps(dict(rows[-1], id="t2", parentId="t")) + "\n")
                predecessor = "t2"
            f.write(json.dumps({"id": "r", "parentId": predecessor,
                                "message": reply}) + "\n")
        if case not in ("no_end", "earlier_lifecycle_end"):
            _fire_event(client, "agent", dict(obs, stream="lifecycle", data={"phase": "end"}))

    async def rpc(method, *args, **kwargs):
        if method == "sessions.describe":
            if case == "describe_error":
                raise ConnectionError("lookup failed")
            if case == "already_persisted":
                continuation()
            else:
                asyncio.get_running_loop().call_later(0.025, continuation)
            return ResponseFrame.ok_response("desc", {"session": {"key": parent, "sessionId": sid}})
        assert method == "chat.send"
        _fire_event(client, "agent", {"runId": "p", "sessionKey": parent, "stream": "tool", "data": spawn})
        _fire_event(client, "chat", {"runId": "p", "sessionKey": parent, "state": "final"})
        _fire_event(client, "chat", {"runId": receipt["runId"], "sessionKey": child, "state": "final"})
        _fire_event(client, "chat", dict(wire, state="final"))
        if case == "duplicate":
            _fire_event(client, "chat", dict(wire, state="final"))
        return ResponseFrame.ok_response("ok", {"runId": "p"})

    client.send_request = rpc
    events = [e async for e in client.chat_stream(parent, "test", idempotency_key="p", timeout_ms=1200)]
    terminals = [e for e in events if e.get("state") in {"final", "aborted", "error"}]
    assert len(terminals) == 1
    e = terminals[0]
    if case == "parent_resumed":
        assert e["runId"] == "resumed" and e["message"] == _text_message("parent answer")
        return
    if case in {"retry", "duplicate", "keyless", "wire_final", "stock", "second_retry",
                "already_persisted", "large_stock"}:
        assert e["state"] == "final"
        assert e["message"] == _text_message("real final" if case == "wire_final" else final_text)
    elif case in {"silent", "ordinary_empty", "missing_transcript", "other_receipt",
                  "wrong_header", "wrong_history_key", "describe_error"}:
        assert e["state"] == "final" and not e.get("message")
    elif case == "no_followup":
        assert e["state"] == "error" and e["errorCode"] == "AGENT_TIMEOUT"
    else:
        assert e["state"] == ("aborted" if case == "aborted" else "error")


@pytest.mark.asyncio
async def test_cancelled_describe_removes_pending_rpc():
    client = _make_client()
    sent = asyncio.Event()

    class Socket:
        async def send(self, message):
            sent.set()

    client._ws = Socket()
    task = asyncio.create_task(
        client.send_request_with_id("lookup", "sessions.describe", {"key": "parent"})
    )
    await asyncio.wait_for(sent.wait(), timeout=1)
    assert "lookup" in client._pending_requests
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client._pending_requests


@pytest.mark.asyncio
async def test_two_separate_announces_with_silent_first_never_enter_recovery():
    client = _make_client()
    parent = "agent:main:two-children"
    calls = []

    async def rpc(method, *args, **kwargs):
        calls.append(method)
        assert method == "chat.send"
        for i in ("1", "2"):
            _fire_event(client, "agent", {"runId": "p", "sessionKey": parent,
                        "stream": "tool", "data": _accepted_sessions_spawn_data(i)})
        _fire_event(client, "chat", {"runId": "p", "sessionKey": parent, "state": "final"})
        for i in ("1", "2"):
            child = f"agent:main:subagent:child-{i}"
            _fire_event(client, "chat", {"runId": f"child-run-{i}", "sessionKey": child, "state": "final"})
            frame = {"runId": f"announce:v1:{child}:child-run-{i}", "sessionKey": parent, "state": "final"}
            if i == "2":
                frame["message"] = _text_message("second report")
            _fire_event(client, "chat", frame)
        return ResponseFrame.ok_response("sent", {"runId": "p"})

    client.send_request = rpc
    finals = [e async for e in client.chat_stream(parent, "test", idempotency_key="p", timeout_ms=250)
              if e.get("state") == "final"]
    assert calls == ["chat.send"]
    assert len(finals) == 1 and finals[0]["message"] == _text_message("second report")
