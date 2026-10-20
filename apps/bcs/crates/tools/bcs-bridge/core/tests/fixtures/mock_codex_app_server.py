#!/usr/bin/env python3
"""Minimal Codex app-server JSON-RPC peer for bridge-provider tests."""

import json
import sys


def send(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


questions = {}

for raw in sys.stdin:
    request = json.loads(raw)
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method is None and request_id in questions:
        thread_id, turn_id = questions.pop(request_id)
        send({"method":"item/agentMessage/delta","params":{"threadId":thread_id,"turnId":turn_id,
            "delta":json.dumps(request.get("result",request.get("error")))}})
        send({"method":"turn/completed","params":{"threadId":thread_id,"turnId":turn_id,
            "turn":{"status":"completed"}}})
        continue
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
        if text.startswith("ASYNC_USER:"):
            item = {"type":"agentMessage","id":"async-question-item","delivery":"async","text":"Choose a design?",
                "questions":[{"title":"Choose a design?","options":["A","B"]},{"title":"Any details?","options":None}]}
            send({"method":"item/completed","params":{"threadId":"t-1","turnId":turn_id,"item":item}})
            send({"method":"turn/completed","params":{"threadId":"t-1","turnId":turn_id,
                "turn":{"status":"completed","items":[item,{"type":"agentMessage","id":"final-question",
                    "phase":"final_answer","text":"Question sent."}]}}})
            continue
        if text.startswith("ASK_USER:"):
            questions["ask-1"] = ("t-1", turn_id)
            send({"jsonrpc":"2.0","id":"ask-1","method":"item/tool/requestUserInput","params":{
                "threadId":"t-1","turnId":turn_id,"itemId":"question-item","isBlocking":True,
                "questions":[{"id":"design","header":"Design","question":"Choose a design?","isOther":True,
                    "options":[{"label":"A","description":"simple"},{"label":"B","description":"advanced"}]},
                    {"id":"details","header":"Details","question":"Any details?","options":None}]}})
            continue
        if text.startswith("WEB_SEARCH_FINAL:"):
            searches = []
            for index, action in enumerate(("search", "openPage", "findInPage")):
                item_id = f"web-{index}"
                send({"jsonrpc": "2.0", "method": "item/started", "params": {
                    "threadId": "t-1", "turnId": turn_id,
                    "item": {"type": "webSearch", "id": item_id, "query": "", "action": None, "results": None}}})
                searches.append({"type": "webSearch", "id": item_id, "query": "Codex events",
                    "action": {"type": action}, "results": [{"title": "Codex docs"}]})
            # No individual item/completed notifications; only authoritative final items.
            send({"jsonrpc": "2.0", "method": "turn/completed", "params": {
                "threadId": "t-1", "turnId": turn_id, "turn": {"status": "completed", "items": searches + [
                    {"type": "agentMessage", "phase": "final_answer", "text": "search complete"}]}}})
            continue
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
        bulk_final = text.startswith("BULK_FINAL:")
        if bulk_final:
            # No streaming agentMessage deltas at all; the answer arrives only
            # as a whole agentMessage item inside turn.completed. Exercises
            # the driver's bulk-final fallback on the real JSON-RPC path
            # (mirrors how real review turns have answered without deltas).
            send({
                "jsonrpc": "2.0",
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turnId": turn_id,
                    "turn": {
                        "status": "completed",
                        "items": [
                            {"type": "agentMessage", "phase": "final_answer",
                             "text": text[len("BULK_FINAL:"):].lstrip()},
                        ],
                    },
                },
            })
            continue
        progress_final = text.startswith("PROGRESS_FINAL:")
        if progress_final:
            # Progress commentary streams first, then the real answer arrives
            # inside turn.completed.items. Exercises that an in-flight delta
            # never shadows the authoritative final message.
            send({
                "jsonrpc": "2.0",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "t-1", "turnId": turn_id,
                    "delta": "Checking the PR...",
                },
            })
            send({
                "jsonrpc": "2.0",
                "method": "turn/completed",
                "params": {
                    "threadId": "t-1",
                    "turnId": turn_id,
                    "turn": {
                        "status": "completed",
                        "items": [
                            {"type": "agentMessage", "phase": "final_answer",
                             "text": text[len("PROGRESS_FINAL:"):].lstrip()},
                        ],
                    },
                },
            })
            continue
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
