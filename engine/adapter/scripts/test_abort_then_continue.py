#!/usr/bin/env python3
"""
test_abort_then_continue.py — 测试 abort 后能否继续发送新消息

场景:
  1. 发送 chat.send: "创建3个文件,并每个文件打印hello world"
  2. 等收到 accepted + 几个事件后, 发送 chat.abort
  3. 等 abort 确认 + stream 结束 (state=aborted)
  4. 再发送 chat.send: "你好"
  5. 验证第二次 chat.send 正常执行并返回响应

Prerequisites:
  - relay on :18900 with ANTHROPIC_AUTH_TOKEN set
  - engine on :20003 with ZERO_CHECK_ENABLED=false
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

try:
    import websockets
except ImportError:
    print("ERROR: websockets package required")
    sys.exit(1)


HOST = "localhost"
PORT = 20003
SESSION_PREFIX = "agent:claude-code-ws:session"
USER_SUFFIX = "user:claude-code-ws"

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"


def make_session_key(name: str) -> str:
    return f"{SESSION_PREFIX}:{name}:user:claude-code-ws"


async def ws_connect():
    """Connect and handshake, return ws."""
    uri = f"ws://{HOST}:{PORT}/api/claude-code/ws"
    ws = await websockets.connect(uri, max_size=16 * 1024 * 1024)
    connect_req = {
        "type": "req",
        "id": uuid.uuid4().hex[:8],
        "method": "connect",
        "params": {
            "client": {"id": "abort-test", "version": "1.0.0", "platform": "python", "mode": "test"},
            "minProtocol": 3,
            "maxProtocol": 3,
        },
    }
    await ws.send(json.dumps(connect_req))
    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    if not resp.get("ok"):
        raise RuntimeError(f"Handshake failed: {resp}")
    return ws


async def send_chat(ws, session_key: str, message: str) -> str:
    """Send chat.send, return request id."""
    req_id = uuid.uuid4().hex[:8]
    await ws.send(json.dumps({
        "type": "req",
        "id": req_id,
        "method": "chat.send",
        "params": {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": uuid.uuid4().hex,
            "cwd": "/tmp",
        },
    }))
    return req_id


async def send_abort(ws, session_key: str) -> str:
    """Send chat.abort, return request id."""
    req_id = uuid.uuid4().hex[:8]
    await ws.send(json.dumps({
        "type": "req",
        "id": req_id,
        "method": "chat.abort",
        "params": {"sessionKey": session_key},
    }))
    return req_id


async def collect_until_state(ws, target_states: set, timeout: float = 180.0) -> dict:
    """Collect frames until we hit a target state. Returns summary."""
    events = []
    responses = []
    final_state = ""
    full_text = ""
    t0 = time.monotonic()

    while time.monotonic() - t0 < timeout:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
        except asyncio.TimeoutError:
            break
        frame = json.loads(raw)

        if frame.get("type") == "res":
            responses.append(frame)
            if not frame.get("ok"):
                err = frame.get("error", {})
                return {
                    "error": err.get("message", "unknown"),
                    "events": events,
                    "responses": responses,
                    "state": "error",
                    "text": full_text,
                }
        elif frame.get("type") == "event":
            payload = frame.get("payload", {})
            state = payload.get("state", "")
            events.append(payload)

            # Extract text from final message
            if state == "final":
                msg = payload.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "text":
                        full_text += block.get("text", "")

            if state in target_states:
                final_state = state
                break

    return {
        "state": final_state,
        "events": events,
        "responses": responses,
        "text": full_text,
        "elapsed": time.monotonic() - t0,
    }


async def run_test():
    print(f"\n{'='*60}")
    print(f"  Abort-then-Continue Test")
    print(f"  Target: ws://{HOST}:{PORT}/api/claude-code/ws")
    print(f"{'='*60}\n")

    # Connect
    print(f"  {INFO} Connecting...")
    ws = await ws_connect()
    print(f"  {PASS} Handshake OK\n")

    session_key = make_session_key(f"abort-test-{uuid.uuid4().hex[:6]}")

    # ================================================================
    # Step 1: Send first chat (long task)
    # ================================================================
    print(f"  {INFO} Step 1: Sending chat.send (long task)...")
    print(f"        message: '创建3个文件,并每个文件打印hello world'")
    chat1_id = await send_chat(ws, session_key, "创建3个文件,并每个文件打印hello world")

    # Wait for accepted response + a few events
    accepted = False
    event_count = 0
    t0 = time.monotonic()

    while time.monotonic() - t0 < 30:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
        except asyncio.TimeoutError:
            break
        frame = json.loads(raw)

        if frame.get("type") == "res" and frame.get("id") == chat1_id:
            if frame.get("ok"):
                accepted = True
                print(f"  {PASS} chat.send accepted")
            else:
                err = frame.get("error", {})
                print(f"  {FAIL} chat.send rejected: {err.get('message')}")
                await ws.close()
                return 1
        elif frame.get("type") == "event":
            event_count += 1
            payload = frame.get("payload", {})
            state = payload.get("state", "")
            stream = payload.get("stream", "")
            if event_count <= 3:
                print(f"        event #{event_count}: stream={stream} state={state}")
            # Wait for at least 2 events before aborting
            if event_count >= 2 and accepted:
                break

    if not accepted:
        print(f"  {FAIL} Never received accepted response")
        await ws.close()
        return 1

    print(f"        received {event_count} events before abort")

    # ================================================================
    # Step 2: Send abort
    # ================================================================
    print(f"\n  {INFO} Step 2: Sending chat.abort...")
    abort_id = await send_abort(ws, session_key)

    # Collect until stream ends (state=aborted or final or error)
    abort_responded = False
    stream_ended = False

    t0 = time.monotonic()
    while time.monotonic() - t0 < 60:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
        except asyncio.TimeoutError:
            print(f"        [timeout waiting for abort completion]")
            break
        frame = json.loads(raw)

        if frame.get("type") == "res" and frame.get("id") == abort_id:
            abort_responded = True
            ok = frame.get("ok")
            print(f"  {PASS if ok else FAIL} chat.abort responded (ok={ok})")
        elif frame.get("type") == "event":
            payload = frame.get("payload", {})
            state = payload.get("state", "")
            if state in ("aborted", "final", "error"):
                stream_ended = True
                print(f"  {PASS} Stream ended: state={state}")
                break

    if not abort_responded:
        print(f"  {FAIL} No abort response received")
    if not stream_ended:
        print(f"  {FAIL} Stream did not end after abort (waited 60s)")
        # Try to continue anyway

    # Wait for relay to fully clean up after abort
    print(f"        Waiting 3s for relay cleanup...")
    await asyncio.sleep(3.0)

    # Drain any leftover frames from the first chat
    drained = 0
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            drained += 1
        except asyncio.TimeoutError:
            break
    if drained:
        print(f"        Drained {drained} leftover frame(s)")

    # ================================================================
    # Step 3: Send second chat ("你好")
    # ================================================================
    print(f"\n  {INFO} Step 3: Sending new chat.send after abort...")
    print(f"        message: '你好'")

    # Use same session key (tests session reuse after abort — the real scenario)
    chat2_id = await send_chat(ws, session_key, "你好")

    # Collect response — filter by chat2's request id and ignore stale events
    accepted2 = False
    chat2_run_id = None
    full_text2 = ""
    state2 = ""
    t0 = time.monotonic()

    while time.monotonic() - t0 < 180:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
        except asyncio.TimeoutError:
            print(f"        [timeout 60s]")
            break
        frame = json.loads(raw)

        if frame.get("type") == "res":
            if frame.get("id") == chat2_id:
                if frame.get("ok"):
                    accepted2 = True
                    print(f"  {PASS} Second chat.send accepted")
                else:
                    err = frame.get("error", {})
                    print(f"  {FAIL} Second chat.send rejected: {err.get('message')}")
                    await ws.close()
                    return 1
        elif frame.get("type") == "event":
            payload = frame.get("payload", {})
            state = payload.get("state", "")
            run_id = payload.get("runId", "")

            # Track run ID of the second chat
            if chat2_run_id is None and run_id and accepted2:
                chat2_run_id = run_id
                print(f"        runId={run_id}")

            # Skip events that belong to the first (aborted) run
            if chat2_run_id and run_id and run_id != chat2_run_id:
                continue

            # Extract text from final event
            if state == "final":
                msg = payload.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "text":
                        full_text2 += block.get("text", "")
                state2 = "final"
                break
            elif state == "error":
                state2 = "error"
                break
            elif state == "aborted":
                # Only count as aborted if it's our run
                if chat2_run_id and run_id == chat2_run_id:
                    state2 = "aborted"
                    break
                # Otherwise skip stale abort from first run

    elapsed2 = time.monotonic() - t0

    if state2 == "final" and full_text2:
        print(f"  {PASS} Second chat completed: state={state2}, elapsed={elapsed2:.1f}s")
        print(f"        Response: {full_text2[:200]}")
    elif state2 == "final":
        print(f"  {PASS} Second chat completed: state={state2} (no text in final frame)")
    elif state2 == "aborted":
        print(f"  {FAIL} Second chat was also aborted (runId={chat2_run_id})")
    elif state2 == "error":
        print(f"  {FAIL} Second chat ended with error")
    else:
        print(f"  {FAIL} Second chat did not complete: state='{state2}', elapsed={elapsed2:.1f}s")
        await ws.close()
        return 1

    await ws.close()

    # ================================================================
    # Summary
    # ================================================================
    print(f"\n{'='*60}")
    all_pass = accepted and abort_responded and stream_ended and state2 == "final"
    if all_pass:
        print(f"  {PASS} ABORT-THEN-CONTINUE TEST PASSED")
        print(f"        First chat aborted successfully, second chat completed normally")
    elif state2 == "aborted":
        print(f"  {FAIL} TEST FAILED — second chat was aborted (abort-then-continue broken)")
        print(f"        This is a known relay/engine bug: abort kills the subsequent run")
    else:
        print(f"  {FAIL} TEST FAILED (state2={state2})")
    print(f"{'='*60}\n")

    return 0 if all_pass else 1


def main():
    # Quick connectivity check
    import urllib.request
    try:
        urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=3)
    except Exception:
        print(f"\n  {FAIL} Cannot reach engine at http://{HOST}:{PORT}/health")
        print(f"        Start services first (with ZERO_CHECK_ENABLED=false)\n")
        sys.exit(1)

    sys.exit(asyncio.run(run_test()))


if __name__ == "__main__":
    main()
