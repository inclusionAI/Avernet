#!/usr/bin/env python3
"""Deterministic Claude stream-json peer for upstream WebSocket tests."""

import json
import os
import sys
import time
import uuid
from pathlib import Path


def emit(value):
    value.setdefault("uuid", str(uuid.uuid4()))
    print(json.dumps(value, ensure_ascii=False), flush=True)


def wait_for(path, timeout=15):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            sys.exit(f"timed out waiting for {path.name}")
        time.sleep(0.01)


args = sys.argv[1:]
assert args[args.index("--input-format") + 1] == "stream-json"
assert args[args.index("--output-format") + 1] == "stream-json"

resume = args[args.index("--resume") + 1] if "--resume" in args else None
session_id = resume or "11111111-1111-4111-8111-111111111111"
init = json.loads(sys.stdin.readline())
assert init["request"]["subtype"] == "initialize"
assert os.environ.get("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS") == "1"
callback = init["request"]["hooks"]["Stop"][0]["hookCallbackIds"][0]
emit({"type": "control_response", "response": {
    "subtype": "success", "request_id": init["request_id"], "response": {}}})
user = json.loads(sys.stdin.readline())
assert user["type"] == "user"
assert user["message"]["role"] == "user"
prompt = "\n".join(part["text"] for part in user["message"]["content"])

with Path("engine-starts.jsonl").open("a", encoding="utf-8") as starts:
    starts.write(json.dumps({"pid": os.getpid(), "resume": resume, "prompt": prompt}, ensure_ascii=False) + "\n")

emit({"type": "system", "subtype": "init", "session_id": session_id, "claude_code_version": "2.1.199"})
emit({"type": "stream_event", "event": {
    "type": "content_block_delta",
    "delta": {"type": "text_delta", "text": "__initial_delta__"},
}})
Path("initial-delta").touch()

if "WAIT_OFFLINE" in prompt:
    wait_for(Path("release-engine"))

if "WAIT_FOR_ABORT" in prompt:
    # The Bridge must terminate this process when chat.abort targets the run.
    while True:
        time.sleep(0.05)

if "REQUEST_PERMISSION" in prompt:
    emit({
        "type": "control_request",
        "request_id": "permission-1",
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "Bash",
            "input": {"command": "printf forbidden"},
            "tool_use_id": "tool-1",
        },
    })
    response = json.loads(sys.stdin.readline())
    assert response["type"] == "control_response"
    assert response["response"]["request_id"] == "permission-1"
    assert response["response"]["response"]["behavior"] == "deny"

emit({"type": "control_request", "request_id": "stop-1", "request": {
    "subtype": "hook_callback", "callback_id": callback, "input": {
        "hook_event_name": "Stop", "session_id": session_id, "prompt_id": "prompt-1",
        "stop_hook_active": False, "background_tasks": []}}})
response = json.loads(sys.stdin.readline())
assert response["type"] == "control_response"
assert response["response"]["request_id"] == "stop-1"
assert response["response"]["response"] == {}

# Mark engine completion before publishing the terminal result. The Bridge may
# reap the child immediately after reading that result, so a marker written
# afterwards races normal process cleanup rather than testing offline progress.
Path("engine-complete").touch()
emit({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": f"prompt={prompt};resume={resume or 'none'}",
    "session_id": session_id,
})
emit({"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": session_id})
