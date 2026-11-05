#!/usr/bin/env python3
"""Minimal pi `--mode rpc` JSONL peer for bridge-provider tests.

Speaks the upstream pi dialect: no ready frame, `agent_settled` terminal.
Prompt-prefix modes mirror the codex mock (mock_codex_app_server.py):

- default          stream deltas + tool run + final message, then settled
- `HANDLED:`       prompt consumed by an extension command (disposition)
- `FAIL:`          prompt rejected before acceptance
- `ERROR_RUN:`     assistant message ends with stopReason=error, then settles
- `RETRY_FAIL:`    auto_retry_end reports a final error
- `SETTLED_ABORT:` agent_settled with aborted=true
- `SLOW:`          sleeps 30s after the acknowledgement (abort tests)

`--session-id <id>` in argv is echoed as the active session id so resume
turns observe the bridge's persisted id; without it the session is `sess-42`.
"""

import json
import os
import sys
import time


def send(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def response(request_id: str, command: str, data: dict, success: bool = True, error: str = None) -> None:
    frame = {"type": "response", "id": request_id, "command": command, "success": success}
    if error is not None:
        frame["error"] = error
    if success:
        frame["data"] = data
    send(frame)


def stream_turn(text: str) -> None:
    send({"type": "agent_start"})
    send({"type": "message_start", "message": {"role": "assistant", "content": [], "stopReason": "pending"}})
    send({"type": "message_update", "usage": {}, "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "你好 "}})
    send({"type": "message_update", "usage": {}, "assistantMessageEvent": {"type": "thinking_delta", "contentIndex": 1, "delta": "推理中"}})
    send({"type": "message_update", "usage": {}, "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "pi"}})
    send({"type": "tool_execution_start", "toolCallId": "call-1", "toolName": "bash", "args": {"command": "ls"}})
    send({"type": "tool_execution_update", "toolCallId": "call-1", "toolName": "bash", "args": {"command": "ls"},
          "partialResult": {"content": [{"type": "text", "text": "file-a"}]}})
    send({"type": "tool_execution_end", "toolCallId": "call-1", "toolName": "bash",
          "result": {"content": [{"type": "text", "text": "file-a\nfile-b"}]}, "isError": False, "durationMs": 12})
    send({"type": "message_end", "message": {"role": "assistant",
          "content": [{"type": "text", "text": "Hello pi: " + text}], "stopReason": "stop"}})
    send({"type": "agent_end", "messages": [], "willRetry": False})
    send({"type": "agent_settled", "aborted": False})


argv = sys.argv[1:]
resumed_session = None
if "--session-id" in argv:
    resumed_session = argv[argv.index("--session-id") + 1]

for raw in sys.stdin:
    command = json.loads(raw)
    kind = command.get("type")
    request_id = command.get("id")

    if kind == "get_state":
        # Without --session-id the session is per-process, so a resume turn
        # only observes the same id when the bridge actually passes it.
        session_id = resumed_session if resumed_session is not None else "sess-%d" % os.getpid()
        response(request_id, "get_state", {"sessionId": session_id, "isStreaming": False})
    elif kind == "prompt":
        message = command.get("message", "")
        if message.startswith("HANDLED:"):
            response(request_id, "prompt", {"disposition": "handled"})
        elif message.startswith("FAIL:"):
            response(request_id, "prompt", {}, success=False, error="Model not found: invalid/model")
        elif message.startswith("ERROR_RUN:"):
            response(request_id, "prompt", {"disposition": "started"})
            send({"type": "message_end", "message": {"role": "assistant", "content": [],
                  "stopReason": "error", "errorMessage": "provider 500"}})
            send({"type": "agent_settled", "aborted": False})
        elif message.startswith("RETRY_FAIL:"):
            response(request_id, "prompt", {"disposition": "started"})
            send({"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 0, "errorMessage": "529 overloaded"})
            send({"type": "auto_retry_end", "success": False, "attempt": 3, "finalError": "529 overloaded"})
            send({"type": "agent_settled", "aborted": False})
        elif message.startswith("SETTLED_ABORT:"):
            response(request_id, "prompt", {"disposition": "started"})
            send({"type": "agent_settled", "aborted": True})
        elif message.startswith("SLOW:"):
            response(request_id, "prompt", {"disposition": "started"})
            time.sleep(30)
            stream_turn(message)
        else:
            response(request_id, "prompt", {"disposition": "started"})
            stream_turn(message)
    elif kind == "abort":
        response(request_id, "abort", {})
    else:
        response(request_id, kind or "parse", {}, success=False, error="unknown command")
