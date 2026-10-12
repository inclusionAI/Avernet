#!/usr/bin/env python3
"""Minimal Codex app-server JSON-RPC peer for bridge-provider tests."""

import json
import sys


def send(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


for raw in sys.stdin:
    request = json.loads(raw)
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"capabilities": {}}})
    elif method == "initialized":
        continue
    elif method in ("thread/start", "thread/resume"):
        # Real Codex may emit unrelated status notifications before the RPC
        # response. The bridge must keep reading stdout instead of spinning on
        # the first buffered notification forever.
        send({"jsonrpc": "2.0", "method": "configWarning", "params": {"summary": "test"}})
        send({"jsonrpc": "2.0", "method": "remoteControl/status/changed", "params": {"status": "disabled"}})
        send({"jsonrpc": "2.0", "id": request_id, "result": {"thread": {"id": "t-1"}}})
    elif method == "turn/start":
        turn_id = "turn-1"
        send({"jsonrpc": "2.0", "id": request_id, "result": {"turn": {"id": turn_id}}})
        input_items = params.get("input") or []
        text = "\n".join(item.get("text", "") for item in input_items)
        send({
            "jsonrpc": "2.0",
            "method": "item/started",
            "params": {
                "threadId": "t-1",
                "turnId": turn_id,
                "item": {
                    "type": "commandExecution",
                    "id": "cmd-1",
                    "command": "printf tool-output",
                    "cwd": "/tmp",
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "item/commandExecution/outputDelta",
            "params": {
                "threadId": "t-1",
                "turnId": turn_id,
                "itemId": "cmd-1",
                "delta": "tool-output",
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {
                "threadId": "t-1",
                "turnId": turn_id,
                "item": {
                    "type": "commandExecution",
                    "id": "cmd-1",
                    "command": "printf tool-output",
                    "cwd": "/tmp",
                    "status": "completed",
                    "exitCode": 0,
                    "durationMs": 3,
                    "aggregatedOutput": "tool-output",
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "item/reasoning/textDelta",
            "params": {
                "threadId": "t-1",
                "turnId": turn_id,
                "delta": "先处理工具结果，再回答。",
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {"threadId": "t-1", "turnId": turn_id, "delta": text},
        })
        send({
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {
                "threadId": "t-1",
                "turnId": turn_id,
                "turn": {"status": "completed"},
            },
        })
