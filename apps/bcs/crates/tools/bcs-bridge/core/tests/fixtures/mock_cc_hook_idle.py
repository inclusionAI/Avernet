"""Synthetic protocol peer. All identities and content are invented."""
import json
import os
import sys
import time

case = sys.argv[1]
sid = "synthetic-session"
seq = 0
def emit(ty, **fields):
    global seq
    seq += 1
    fields.setdefault("uuid", f"frame-{seq}")
    print(json.dumps(dict(type=ty, session_id=sid, **fields)), flush=True)
def read():
    line = sys.stdin.readline()
    assert line, "driver closed before protocol completed"
    return json.loads(line)

init = read()
if case == "legacy":
    assert init["type"] == "user", "explicit legacy must skip hook initialization"
    emit("system", subtype="init")
    emit("result", subtype="success", is_error=False, result="DONE")
    sys.exit(0)
if case in ("observe_init_failure", "observe_init_timeout"):
    if init["type"] == "user":
        emit("system", subtype="init")
        emit("result", subtype="success", result="DONE")
        sys.exit(0)
    if case == "observe_init_timeout": time.sleep(60)
    emit("control_response", response=dict(subtype="error", request_id=init["request_id"], error="unsupported"))
    sys.exit(0)
assert init["request"]["subtype"] == "initialize"
assert os.environ.get("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS") == "1"
callback = init["request"]["hooks"]["Stop"][0]["hookCallbackIds"][0]
emit("control_response", response=dict(subtype="success", request_id=init["request_id"], response={}))
assert read()["type"] == "user"

def epoch():
    emit("system", subtype="init", claude_code_version="2.1.199" if case != "unknown_version" else "0.0.0")
def result(text):
    emit("result", subtype="success", is_error=False, result=text)
def idle():
    emit("system", subtype="session_state_changed", state="idle")
def hook(n, tasks):
    data = dict(hook_event_name="Stop", session_id=sid, prompt_id="prompt", stop_hook_active=n > 1,
                background_tasks=tasks, last_assistant_message="PRIVATE")
    if case == "null_tasks": data["background_tasks"] = None
    if case == "unknown_task": data["background_tasks"] = [dict(id="m", type="monitor", status="running")]
    if case == "scheduled": data["session_crons"] = [dict(id="cron")]
    if case == "too_many_tasks": data["background_tasks"] = [dict(id=str(i), type="shell", status="running") for i in range(600)]
    if case == "missing_tasks": del data["background_tasks"]
    if case == "wrong_session": data["session_id"] = "other"
    rid = f"hook-{n}"
    emit("control_request", request_id=rid, request=dict(subtype="hook_callback",
        callback_id=callback if case != "wrong_callback" else "other", input=data))
    answer = read()
    assert answer["response"]["request_id"] == rid
    assert answer["response"]["response"] == {}, "observation must not block or change model input"

epoch()
if case == "rate_limit_error":
    # A failed turn emits no successful Stop/idle evidence. The driver must
    # surface the cause immediately rather than waiting for completion hooks.
    emit("result", subtype="success", is_error=True,
         result="API Error: Request rejected (429) · provider rate limit; PRIVATE credential")
    time.sleep(60)
if case == "malformed":
    print('PRIVATE malformed control frame', flush=True)
    sys.exit(0)
if case == "unbounded_line":
    sys.stdout.write("x" * (3 * 1024 * 1024)); sys.stdout.flush(); time.sleep(60)
if case == "observe_large":
    emit("assistant", message=dict(content=[dict(type="text", text="x" * (3 * 1024 * 1024))]))
if case == "nonreading_callback":
    emit("control_request", request_id="r" * (128 * 1024), request=dict(subtype="hook_callback", callback_id="wrong", input={}))
    time.sleep(60)
if case == "replayed_idle":
    hook(1, [dict(id="t", type="shell", status="running")])
    result("OLD")
    emit("system", subtype="session_state_changed", state="idle", uuid="old-idle")
    epoch()
    hook(2, [])
    result("DONE")
    emit("system", subtype="session_state_changed", state="idle", uuid="old-idle")
    sys.exit(0)
if case == "midturn":
    for i in range(7):
        emit("system", subtype="task_started", task_id=f"t{i}", tool_use_id=f"tool{i}", task_type="local_agent" if i < 3 else "local_bash")
        emit("system", subtype="task_notification", task_id=f"t{i}", tool_use_id=f"tool{i}", status="completed")
if case == "missing_hook":
    result("PRIVATE")
    idle()
elif case in ("alive", "queued", "observe"):
    hook(1, [dict(id="task", type="subagent", status="running")] if case == "alive" else [])
    result("INTERMEDIATE")
    if case == "observe":
        # Legacy returns on result; any attempt to wait for idle fails on EOF.
        sys.exit(0)
    if case == "alive":
        idle()
        emit("system", subtype="task_notification", task_id="task", status="completed")
    epoch()
    hook(2, [])
    result("DONE")
    idle()
else:
    hook(1, [])
    if case == "duplicate_hook": hook(1, [])
    if case == "blocked":
        emit("assistant", message=dict(content=[dict(type="text", text="continuation")]))
        hook(2, [])
    if case == "late_task":
        emit("system", subtype="task_started", task_id="late", tool_use_id="late", task_type="local_bash")
    result("" if case == "empty" else "DONE")
    if case == "late_hook": hook(2, [])
    if case == "duplicate_result": result("STALE")
    if case == "no_idle": time.sleep(60)
    if case == "old_idle": epoch()
    idle()
