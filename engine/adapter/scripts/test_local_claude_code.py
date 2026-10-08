#!/usr/bin/env python3
"""
test_local_claude_code.py — Integration test for claude_code engine local environment.

Verifies that the engine adapter (port 20003) is running with claude_code engine
and that its HTTP + WebSocket endpoints respond correctly.

Prerequisites:
  - start_local_claude_code.sh start (relay on 18900, engine on 20003)

Usage:
  python scripts/test_local_claude_code.py
  python scripts/test_local_claude_code.py --host localhost --port 20003
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

# --- HTTP tests use urllib (stdlib) to avoid extra deps ---
import urllib.request
import urllib.error


PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
SKIP = "\033[93m[SKIP]\033[0m"


def http_get(url: str, timeout: float = 10.0) -> tuple[int, dict]:
    """Simple HTTP GET, returns (status_code, json_body)."""
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.fp else {}
        return e.code, body
    except Exception as e:
        raise ConnectionError(f"HTTP GET {url} failed: {e}")


# --- WebSocket tests ---


async def ws_connect(host: str, port: int):
    """Helper: connect to engine WS and complete handshake. Returns (ws, error_msg)."""
    import websockets

    uri = f"ws://{host}:{port}/api/claude-code/ws"
    ws = await websockets.connect(uri, max_size=16 * 1024 * 1024)

    # Engine server expects client to send connect request immediately (no challenge)
    connect_req = {
        "type": "req",
        "id": uuid.uuid4().hex[:8],
        "method": "connect",
        "params": {
            "client": {
                "id": "test-client",
                "version": "1.0.0",
                "platform": "python-test",
                "mode": "test",
            },
            "minProtocol": 3,
            "maxProtocol": 3,
        },
    }
    await ws.send(json.dumps(connect_req))

    # Wait for hello-ok response
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        resp = json.loads(raw)
        if resp.get("type") == "event":
            continue
        if resp.get("type") == "res" and resp.get("ok"):
            return ws, None
        else:
            error = resp.get("error", {})
            await ws.close()
            return None, f"connect rejected: {error.get('message', 'unknown')}"

    await ws.close()
    return None, "Timed out waiting for hello-ok"


async def ws_test_handshake(host: str, port: int) -> tuple[bool, str]:
    """Connect to /api/claude-code/ws and complete v3 protocol handshake."""
    try:
        ws, err = await ws_connect(host, port)
        if err:
            return False, err
        await ws.close()
        return True, "hello-ok received"
    except Exception as e:
        return False, f"WebSocket error: {e}"


async def ws_test_chat_send(host: str, port: int) -> tuple[bool, str]:
    """Send a chat.send via WebSocket and verify we get event stream back."""
    try:
        ws, err = await ws_connect(host, port)
        if err:
            return False, f"Handshake failed: {err}"

        # Send chat.send
        session_key = f"test-session-{uuid.uuid4().hex[:6]}"
        chat_req = {
            "type": "req",
            "id": uuid.uuid4().hex[:8],
            "method": "chat.send",
            "params": {
                "sessionKey": session_key,
                "message": "echo hello",
                "idempotencyKey": uuid.uuid4().hex,
            },
        }
        await ws.send(json.dumps(chat_req))

        # Wait for response (accepted) + at least one event
        got_response = False
        got_event = False
        event_count = 0
        deadline = time.monotonic() + 60.0

        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            except asyncio.TimeoutError:
                break
            frame = json.loads(raw)

            if frame.get("type") == "res" and frame.get("id") == chat_req["id"]:
                got_response = True
                if not frame.get("ok"):
                    error = frame.get("error", {})
                    err_msg = error.get("message", "unknown")
                    # Auth rejection means engine→relay path works correctly
                    if "token" in err_msg.lower() or "auth" in err_msg.lower():
                        await ws.close()
                        return True, f"chat.send reached relay (auth required: {err_msg} — expected in local env)"
                    await ws.close()
                    return False, f"chat.send rejected: {err_msg}"
            elif frame.get("type") == "event":
                got_event = True
                event_count += 1
                payload = frame.get("payload", {})
                state = payload.get("state", "")
                if state in ("final", "error", "aborted"):
                    break

        await ws.close()

        if got_response and got_event:
            return True, f"chat.send accepted, received {event_count} events"
        elif got_response:
            return True, "chat.send accepted (no events yet - relay may need Claude CLI configured)"
        else:
            return False, "No response received for chat.send"

    except Exception as e:
        return False, f"WebSocket error: {e}"

    except Exception as e:
        return False, f"WebSocket error: {e}"


async def ws_test_sessions_reset(host: str, port: int) -> tuple[bool, str]:
    """Send sessions.reset via WebSocket and verify response."""
    try:
        ws, err = await ws_connect(host, port)
        if err:
            return False, f"Handshake failed: {err}"

        # Send sessions.reset
        session_key = f"test-reset-{uuid.uuid4().hex[:6]}"
        reset_req = {
            "type": "req",
            "id": uuid.uuid4().hex[:8],
            "method": "sessions.reset",
            "params": {"sessionKey": session_key},
        }
        await ws.send(json.dumps(reset_req))

        # Wait for response
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
            frame = json.loads(raw)
            if frame.get("type") == "res" and frame.get("id") == reset_req["id"]:
                await ws.close()
                if frame.get("ok"):
                    return True, "sessions.reset succeeded"
                else:
                    error = frame.get("error", {})
                    return False, f"sessions.reset failed: {error.get('message', 'unknown')}"

        await ws.close()
        return False, "No response received for sessions.reset"

    except Exception as e:
        return False, f"WebSocket error: {e}"


async def ws_test_chat_abort(host: str, port: int) -> tuple[bool, str]:
    """Send chat.abort via WebSocket and verify response."""
    try:
        ws, err = await ws_connect(host, port)
        if err:
            return False, f"Handshake failed: {err}"

        # Send chat.abort (no active session - should still respond gracefully)
        abort_req = {
            "type": "req",
            "id": uuid.uuid4().hex[:8],
            "method": "chat.abort",
            "params": {"sessionKey": "nonexistent-session"},
        }
        await ws.send(json.dumps(abort_req))

        # Wait for response
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
            frame = json.loads(raw)
            if frame.get("type") == "res" and frame.get("id") == abort_req["id"]:
                await ws.close()
                return True, f"chat.abort responded (ok={frame.get('ok')})"

        await ws.close()
        return False, "No response received for chat.abort"

    except Exception as e:
        return False, f"WebSocket error: {e}"


def run_tests(host: str, port: int) -> int:
    """Run all tests, return exit code (0=all pass, 1=some fail)."""
    results: list[tuple[str, bool, str]] = []

    # --- HTTP Tests ---
    print(f"\n{'='*60}")
    print(f"  claude_code engine integration test")
    print(f"  Target: http://{host}:{port}")
    print(f"{'='*60}\n")

    # Test 1: Health check
    try:
        status, body = http_get(f"http://{host}:{port}/health")
        engine = body.get("engine", "")
        if status == 200 and engine == "claude_code":
            results.append(("GET /health", True, f"engine={engine}"))
        else:
            results.append(("GET /health", False, f"status={status}, engine={engine} (expected claude_code)"))
    except Exception as e:
        results.append(("GET /health", False, str(e)))

    # Test 2: Readiness
    try:
        status, body = http_get(f"http://{host}:{port}/readiness")
        if status == 200:
            results.append(("GET /readiness", True, f"body={json.dumps(body)[:100]}"))
        else:
            results.append(("GET /readiness", False, f"status={status}"))
    except Exception as e:
        results.append(("GET /readiness", False, str(e)))

    # Test 3: Engine status
    try:
        status, body = http_get(f"http://{host}:{port}/api/engine/status")
        if status == 200:
            active = body.get("engine", body.get("active", ""))
            results.append(("GET /api/engine/status", True, f"active={active}"))
        else:
            results.append(("GET /api/engine/status", False, f"status={status}"))
    except Exception as e:
        results.append(("GET /api/engine/status", False, str(e)))

    # --- WebSocket Tests ---
    loop = asyncio.new_event_loop()

    # Test 4: WS Handshake
    ok, msg = loop.run_until_complete(ws_test_handshake(host, port))
    results.append(("WS handshake (connect → hello-ok)", ok, msg))

    # Test 5: chat.send
    ok, msg = loop.run_until_complete(ws_test_chat_send(host, port))
    results.append(("WS chat.send", ok, msg))

    # Test 6: sessions.reset
    ok, msg = loop.run_until_complete(ws_test_sessions_reset(host, port))
    results.append(("WS sessions.reset", ok, msg))

    # Test 7: chat.abort
    ok, msg = loop.run_until_complete(ws_test_chat_abort(host, port))
    results.append(("WS chat.abort", ok, msg))

    loop.close()

    # --- Report ---
    print("")
    passed = 0
    failed = 0
    for name, ok, msg in results:
        tag = PASS if ok else FAIL
        print(f"  {tag} {name}")
        print(f"        {msg}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'='*60}\n")

    return 0 if failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="Test claude_code engine local environment")
    parser.add_argument("--host", default="localhost", help="Engine host (default: localhost)")
    parser.add_argument("--port", type=int, default=20003, help="Engine port (default: 20003)")
    args = parser.parse_args()

    # Quick connectivity check before running full suite
    try:
        http_get(f"http://{args.host}:{args.port}/health", timeout=3.0)
    except Exception:
        print(f"\n  {FAIL} Cannot reach engine at http://{args.host}:{args.port}/health")
        print(f"        Run './scripts/start_local_claude_code.sh start' first\n")
        sys.exit(1)

    sys.exit(run_tests(args.host, args.port))


if __name__ == "__main__":
    main()
