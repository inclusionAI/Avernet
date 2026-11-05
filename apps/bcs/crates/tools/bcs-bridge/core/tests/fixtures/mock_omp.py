#!/usr/bin/env python3
"""Minimal omp `--mode rpc` JSONL peer for bridge-provider tests.

Speaks the omp dialect: `ready` frame at startup, sessions bound through
`open_session {sessionDir}`, `prompt_result` + `session_settled` terminals
(never `agent_settled`).

Prompt-prefix modes:

- default   ack -> events -> prompt_result(completed, sessionSettled=true)
- `BG:`     prompt_result(completed, sessionSettled=false) -> follow-up run
            -> session_settled (background async work wakes the agent)
- `LOCAL:`  builtin slash command finished locally (agentInvoked=false)
- `ABORT:`  prompt_result(status=aborted)
- `ERROR:`  prompt_result(status=error) with a structured error
- `SLOW:`   sleeps 30s after the acknowledgement (abort tests)

`open_session` reports `resumed=true` when the requested directory already
holds a session file, and streams `dir=<sessionDir>`/`resumed=<bool>` as the
turn's first deltas so tests can assert directory binding across turns.
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


def stream_run(opened_dir: str, resumed: bool, text: str) -> None:
    send({"type": "agent_start"})
    send({"type": "message_update", "usage": {}, "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0,
          "delta": "dir=" + opened_dir + " resumed=" + str(resumed).lower()}})
    send({"type": "tool_execution_start", "toolCallId": "call-1", "toolName": "read", "args": {"path": "a.txt"}})
    send({"type": "tool_execution_end", "toolCallId": "call-1", "toolName": "read",
          "result": {"content": [{"type": "text", "text": "内容"}]}, "isError": False, "durationMs": 3})
    send({"type": "message_end", "message": {"role": "assistant",
          "content": [{"type": "text", "text": "Hello omp: " + text}], "stopReason": "stop"}})
    send({"type": "agent_end", "messages": [], "willRetry": False})


send({"type": "ready", "protocolVersion": 1, "supportedProtocolVersions": [1, 2],
      "maxFrameBytes": 1048576, "maxReassembledFrameBytes": 67108864})
send({"type": "available_commands_update", "commands": []})

opened_dir = ""
resumed = False

for raw in sys.stdin:
    command = json.loads(raw)
    kind = command.get("type")
    request_id = command.get("id")

    if kind == "open_session":
        opened_dir = command.get("sessionDir", "")
        marker = os.path.join(opened_dir, "session.jsonl")
        resumed = os.path.exists(marker)
        if not resumed:
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        response(request_id, "open_session",
                 {"cancelled": False, "resumed": resumed, "sessionId": "omp-internal-1",
                  "sessionFile": marker})
    elif kind == "get_state":
        response(request_id, "get_state", {"sessionId": "omp-internal-1", "isStreaming": False, "isSettled": True})
    elif kind == "prompt":
        message = command.get("message", "")
        if message.startswith("LOCAL:"):
            response(request_id, "prompt", {"agentInvoked": False})
        elif message.startswith("ABORT:"):
            response(request_id, "prompt", {"agentInvoked": True})
            send({"type": "prompt_result", "id": request_id, "agentInvoked": True,
                  "status": "aborted", "sessionSettled": True})
        elif message.startswith("ERROR:"):
            response(request_id, "prompt", {"agentInvoked": True})
            send({"type": "prompt_result", "id": request_id, "agentInvoked": True,
                  "status": "error", "sessionSettled": True,
                  "error": {"message": "provider timeout", "retryable": False}})
        elif message.startswith("BG:"):
            response(request_id, "prompt", {"agentInvoked": True})
            stream_run(opened_dir, resumed, message)
            send({"type": "prompt_result", "id": request_id, "agentInvoked": True,
                  "status": "completed", "sessionSettled": False})
            # A background job wakes the agent for one more run before the
            # session goes quiet.
            send({"type": "message_end", "message": {"role": "assistant",
                  "content": [{"type": "text", "text": "后台结论"}], "stopReason": "stop"}})
            send({"type": "session_settled"})
        elif message.startswith("SLOW:"):
            response(request_id, "prompt", {"agentInvoked": True})
            time.sleep(30)
            stream_run(opened_dir, resumed, message)
            send({"type": "prompt_result", "id": request_id, "agentInvoked": True,
                  "status": "completed", "sessionSettled": True})
        else:
            response(request_id, "prompt", {"agentInvoked": True})
            stream_run(opened_dir, resumed, message)
            send({"type": "prompt_result", "id": request_id, "agentInvoked": True,
                  "status": "completed", "sessionSettled": True})
    elif kind == "abort":
        response(request_id, "abort", {})
    else:
        response(request_id, kind or "parse", {}, success=False, error="unknown command")
