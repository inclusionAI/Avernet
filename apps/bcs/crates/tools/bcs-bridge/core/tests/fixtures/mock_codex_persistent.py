#!/usr/bin/env python3
"""Stateful app-server fixture. Log/PID make process reuse and recovery observable."""
import json
import os
import sys
import time
import threading

log_path = sys.argv[1]
MOCK_DELAY = float(os.environ.get("MOCK_DELAY", "0"))
threads = set()
turns = 0
counter = 0
ignore_interrupts = set()
gated_interrupts = set()
delayed_turns = set()
delayed_completions = {}
questions = {}


def send(value):
    print(json.dumps(value), flush=True)


for raw in sys.stdin:
    request = json.loads(raw)
    method = request.get("method")
    params = request.get("params") or {}
    with open(log_path, "a") as log:
        log.write(json.dumps(dict(request, pid=os.getpid(),
            actor=os.environ.get("BCN_ACTOR_BOT_ID"))) + "\n")
    if MOCK_DELAY:
        time.sleep(MOCK_DELAY)
    response = {}
    if method is None and request.get("id") in questions:
        thread, turn = questions.pop(request["id"])
        send({"method":"item/agentMessage/delta", "params":{"threadId":thread,"turnId":turn,
            "delta":json.dumps(request.get("result", request.get("error")))}})
        send({"method":"turn/completed", "params":{"threadId":thread,"turnId":turn,
            "turn":{"id":turn,"status":"completed"}}})
        continue
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
        if prompt.startswith("gated-start-"):
            while not os.path.exists(log_path + ".release"):
                time.sleep(0.005)
            prompt = prompt.removeprefix("gated-start-")
        if prompt == "start-error":
            send({"id": request["id"], "error": {"message": "start rejected"}})
            continue
        if prompt == "start-malformed":
            send({"id": request["id"], "result": {"turn": {}}})
            continue
        if prompt == "crash":
            os._exit(2)
        turns += 1
        turn = f"turn-{turns}"
        if prompt == "late-start":
            time.sleep(0.15)
        stale = delayed_completions.pop(thread, None)
        # A parked completion is normally consumed by the follow-up turn, but the
        # interrupt that parks it can land late (the engine interrupts a turn
        # whose turn/start ack outlived its client): then a later, non-follow-up
        # turn pops it. Only a real follow-up names a phase/shape; a late
        # consumer still owes the one stale completion so the log count holds.
        if stale is not None and prompt.startswith("followup-"):
            phase, shape = prompt.removeprefix("followup-").split(":", 1)
        else:
            phase, shape = (None, None)
        def stale_completion():
            event_params = {"threadId": thread, "turn": {"id": stale, "status": "interrupted"}}
            if shape == "flat":
                event_params = {"threadId": thread, "turnId": stale}
            elif shape == "conflicting":
                event_params["turnId"] = turn
            send({"method": "turn/completed", "params": event_params})
            with open(log_path, "a") as log:
                log.write(json.dumps({"pid": os.getpid(), "method": "fixture/stale-completion",
                    "params": {"phase": phase, "shape": shape}}) + "\n")

        if stale is not None and phase is None:
            stale_completion()
        if stale and phase == "before-ack":
            stale_completion()
        send({"id": request["id"], "result": {"turn": {"id": turn}}})
        if stale and phase == "after-ack":
            stale_completion()
        if prompt.startswith("async-"):
            item = {"type":"agentMessage","id":"async-item-"+turn,"delivery":"async","text":"Choose a drink?",
                "questions":[{"title":"你今天更想喝什么？","options":["咖啡","茶","白水"]}]}
            if prompt != "async-snapshot-only":
                send({"method":"item/completed","params":{"threadId":thread,"turnId":turn,"item":item}})
            send({"method":"item/agentMessage/delta","params":{"threadId":thread,"turnId":turn,"delta":"Question sent."}})
            send({"method":"turn/completed","params":{"threadId":thread,"turnId":turn,
                "turn":{"id":turn,"status":"completed","items":[item,{"type":"agentMessage","id":"final-"+turn,
                    "phase":"final_answer","text":"Question sent."}]}}})
            continue
        if prompt.startswith("ask-"):
            rpc_id = "question-" + turn
            questions[rpc_id] = (thread, turn)
            form = {"threadId":thread,"turnId":turn,"itemId":"item-"+turn,"isBlocking":True,
                "questions":[{"id":"choice","header":"Choice","question":"Pick one?","isOther":True,
                    "options":[{"label":"A","description":"first"},{"label":"B","description":"second"}]},
                    {"id":"text","header":"Text","question":"Details?","options":None}]}
            if prompt == "ask-secret":
                form["questions"][0]["isSecret"] = True
            if prompt == "ask-wrong-turn":
                form["turnId"] = "not-the-active-turn"
            send({"id":rpc_id,"method":"item/tool/requestUserInput","params":form})
            if prompt == "ask-engine-close":
                send({"method":"serverRequest/resolved","params":{"threadId":thread,"requestId":rpc_id}})
                questions.pop(rpc_id)
                send({"method":"turn/completed","params":{"threadId":thread,"turnId":turn,
                    "turn":{"id":turn,"status":"completed"}}})
            if prompt == "ask-crash":
                time.sleep(0.05)
                os._exit(2)
            continue
        if prompt == "fail":
            send({"method": "turn/completed", "params": {
                "threadId": thread, "turnId": turn,
                "turn": {"id": turn, "status": "failed", "error": {"message": "test failure"}}}})
            continue
        if prompt == "gated-interrupt":
            gated_interrupts.add(turn)
        if prompt == "ignore-interrupt":
            ignore_interrupts.add(turn)
        if prompt == "hold-delayed-completion":
            delayed_turns.add(turn)
        if prompt in ("hold", "late-start", "ignore-interrupt", "hold-delayed-completion", "gated-interrupt"):
            continue
        send({"method": "item/agentMessage/delta", "params": {
            "threadId": thread, "turnId": turn, "delta": f"{os.getpid()}:"}})
        if stale and phase == "during-output":
            stale_completion()
        send({"method": "item/agentMessage/delta", "params": {
            "threadId": thread, "turnId": turn, "delta": prompt}})
        send({"method": "turn/completed", "params": {
            "threadId": thread, "turn": {"id": turn, "status": "completed"}}})
        continue
    elif method == "turn/interrupt":
        if params["turnId"] in gated_interrupts:
            def release_interrupt(rpc_id=request["id"], turn_params=dict(params)):
                while not os.path.exists(log_path + ".interrupt-release"):
                    time.sleep(0.005)
                send({"id": rpc_id, "result": {}})
                send({"method": "turn/completed", "params": dict(turn_params,
                    turn={"id": turn_params["turnId"], "status": "interrupted"})})
            threading.Thread(target=release_interrupt, daemon=True).start()
            continue
        if params["turnId"] in ignore_interrupts:
            continue
        send({"id": request["id"], "result": {}})
        if params["turnId"] in delayed_turns:
            delayed_completions[params["threadId"]] = params["turnId"]
            continue
        send({"method": "turn/completed", "params": dict(params, turn={"id": params["turnId"], "status": "interrupted"})})
        continue
    else:
        raise AssertionError(method)
    send({"id": request["id"], "result": response})
