#!/usr/bin/env python3
"""Stateful app-server fixture. Log/PID make process reuse and recovery observable."""
import json
import os
import sys
import time

log_path = sys.argv[1]
threads = set()
turns = 0
counter = 0
ignore_interrupts = set()


def send(value):
    print(json.dumps(value), flush=True)


for raw in sys.stdin:
    request = json.loads(raw)
    method = request.get("method")
    params = request.get("params") or {}
    with open(log_path, "a") as log:
        log.write(json.dumps(dict(request, pid=os.getpid())) + "\n")
    response = {}
    if method == "initialize":
        response = {"capabilities": {}}
    elif method == "initialized":
        continue
    elif method in ("thread/start", "thread/resume"):
        if method == "thread/start":
            counter += 1
            thread = f"thread-{os.getpid()}-{counter}"
        else:
            thread = params["threadId"]
            if thread in threads or thread == "external-owned":
                send({"id": request["id"], "error": {"message": "already has an active writer"}})
                continue
        threads.add(thread)
        response = {"thread": {"id": thread}}
    elif method == "turn/start":
        thread = params["threadId"]
        assert thread in threads
        prompt = params["input"][0]["text"]
        if prompt == "crash":
            os._exit(2)
        turns += 1
        turn = f"turn-{turns}"
        if prompt == "late-start":
            time.sleep(0.15)
        send({"id": request["id"], "result": {"turn": {"id": turn}}})
        if prompt == "fail":
            send({"method": "turn/completed", "params": {
                "threadId": thread, "turnId": turn,
                "turn": {"id": turn, "status": "failed", "error": {"message": "test failure"}}}})
            continue
        if prompt == "ignore-interrupt":
            ignore_interrupts.add(turn)
        if prompt in ("hold", "late-start", "ignore-interrupt"):
            continue
        send({"method": "item/agentMessage/delta", "params": {
            "threadId": thread, "turnId": turn, "delta": f"{os.getpid()}:{prompt}"}})
        send({"method": "turn/completed", "params": {
            "threadId": thread, "turnId": turn, "turn": {"id": turn, "status": "completed"}}})
        continue
    elif method == "turn/interrupt":
        if params["turnId"] in ignore_interrupts:
            continue
        send({"id": request["id"], "result": {}})
        send({"method": "turn/completed", "params": dict(params, turn={"id": params["turnId"], "status": "interrupted"})})
        continue
    else:
        raise AssertionError(method)
    send({"id": request["id"], "result": response})
