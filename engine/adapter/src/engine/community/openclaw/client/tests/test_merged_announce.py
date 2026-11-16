import asyncio
import json
from uuid import uuid4

import pytest

from engine.community.openclaw.client.tests.test_gateway_client import (
    ResponseFrame, _accepted_sessions_spawn_data, _fire_event, _make_client, _text_message,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "merged", "duplicate", "running", "wrong_origin", "wrong_envelope",
    "other_round", "unrelated_receipt", "wrong_text", "ordinary_user",
    "stale_branch", "independent", "missing_file", "other_session", "superseded", "tool", "delayed",
])
async def test_two_children_one_summary(tmp_path, case):
    client = _make_client()
    client._config.session_transcript_dir = str(tmp_path)
    parent = "agent:main:merge-test"
    child = "agent:main:subagent:child-"
    first, second = child + "1", child + "2"
    sid = str(uuid4())
    wire = _text_message("summary")
    meta = {"conversationRoundId": "r", "sessionKey": parent}
    envelope = (
        "<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>\nOpenClaw runtime context (internal):\n"
        "This context is runtime-generated, not user-authored. Keep internal details private.\n\n"
        f"[Internal task completion event]\nsource: subagent\nsession_key: {second}\nsession_id: {uuid4()}\n"
        "type: subagent task\n<<<END_OPENCLAW_INTERNAL_CONTEXT>>>"
    )
    notice = dict(_text_message(envelope), role="user", provenance={
        "kind": "inter_session", "sourceTool": "subagent_announce", "sourceSessionKey": first,
    }, historyMeta=meta)
    reply = dict(wire, stopReason="stop", historyMeta=dict(meta))
    receipt = _accepted_sessions_spawn_data("2")["result"]["details"]
    if case == "wrong_origin":
        notice["provenance"]["sourceSessionKey"] = "foreign"
    if case == "wrong_envelope":
        notice["content"][0]["text"] = envelope.replace("session_key: " + second, "session_key: other")
    if case == "other_round":
        reply["historyMeta"]["conversationRoundId"] = "old"
    if case == "unrelated_receipt":
        receipt["runId"] = "other"
    if case == "wrong_text":
        reply.update(_text_message("different"))
    if case == "ordinary_user":
        notice.pop("provenance")
    rows = [
        {"id": "s", "message": {"role": "toolResult", "toolName": "sessions_spawn",
                               "content": _text_message(json.dumps(receipt))["content"]}},
        {"id": "n", "parentId": "s", "message": notice},
        {"id": "f", "parentId": "old" if case == "stale_branch" else "n", "message": reply},
    ]
    if case == "tool":
        rows.insert(-1, {"id": "t", "parentId": "n", "message": {"role": "toolResult", "historyMeta": meta}})
        rows[-1]["parentId"] = "t"
    def persist():
        (tmp_path / f"{sid}.jsonl").write_text("\n".join(map(json.dumps, rows)))
    if case == "delayed":
        asyncio.get_running_loop().call_later(0.04, persist)
    elif case != "missing_file":
        persist()

    probes = []
    cancelled = asyncio.Event()

    def final(i):
        _fire_event(client, "chat", {"sessionKey": parent, "runId": f"announce:v1:{child}{i}:child-run-{i}",
                                     "state": "final", "message": wire})

    async def rpc(method, *args, **kwargs):
        if method == "sessions.describe":
            probes.append(method)
            if case == "superseded":
                final(2)
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            return ResponseFrame.ok_response("lookup", {"session": {
                "key": "foreign" if case == "other_session" else parent, "sessionId": sid,
            }})
        assert method == "chat.send"
        for i in ("1", "2"):
            _fire_event(client, "agent", {"sessionKey": parent, "runId": "parent", "stream": "tool",
                                         "data": _accepted_sessions_spawn_data(i)})
        _fire_event(client, "chat", {"sessionKey": parent, "runId": "parent", "state": "final"})
        for i in ("1", "2") if case != "running" else ("1",):
            _fire_event(client, "chat", {"sessionKey": child + i, "runId": f"child-run-{i}", "state": "final"})
        final(1)
        if case == "duplicate":
            final(1)
        if case == "independent":
            final(2)
        return ResponseFrame.ok_response("send", {"runId": "parent"})

    client.send_request = rpc
    async with asyncio.timeout(3):
        events = [e async for e in client.chat_stream(parent, "test", idempotency_key="parent", timeout_ms=350)]
    finals = [e for e in events if e.get("state") == "final"]
    if case in {"merged", "duplicate", "independent", "superseded", "tool", "delayed"}:
        assert len(finals) == 1
        assert finals[0]["message"] == wire
        assert finals[0]["runId"].endswith("2" if case in {"independent", "superseded"} else "1")
    else:
        assert not finals
        assert events[-1]["errorCode"] == "AGENT_TIMEOUT"
    assert len(probes) == (0 if case in {"running", "independent"} else 1)
    assert cancelled.is_set() == (case == "superseded")


@pytest.mark.asyncio
async def test_ordinary_chat_does_not_probe_history():
    client = _make_client()
    calls = []
    async def rpc(method, *args, **kwargs):
        calls.append(method)
        _fire_event(client, "chat", {"sessionKey": "sk", "runId": "parent",
                                     "state": "final", "message": _text_message("normal reply")})
        return ResponseFrame.ok_response("send", {"runId": "parent"})

    client.send_request = rpc
    events = [e async for e in client.chat_stream("sk", "test", idempotency_key="parent")]
    assert calls == ["chat.send"]
    assert [(e["state"], e["message"]) for e in events] == [("final", _text_message("normal reply"))]
