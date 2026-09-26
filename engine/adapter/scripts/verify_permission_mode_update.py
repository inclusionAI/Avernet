#!/usr/bin/env python3
"""Targeted verification for the permission_mode-update-not-applied fix.

Reuses ws_connect() from test_local_claude_code (engine WS: client sends
connect first, no challenge). Reproduces the bug scenario end-to-end:

  Case A (normal user): create+update permission_mode=default, then chat.send
    WITHOUT permissionMode -> engine must forward permission_mode=None so relay
    falls back to the persisted binding (default), NOT bypassPermissions.

  Case B (BCS source): chat.send with group_id and no permissionMode -> engine
    must seed bypassPermissions.

Assertions read the engine log line `[CC] _to_chat_request: ...`.
Run with the local stack already started (engine :20003).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import requests
import websockets

ENGINE_HTTP = "http://localhost:20003"
ENGINE_WS = "ws://localhost:20003/api/claude-code/ws"
ENGINE_LOG = Path(__file__).parent / ".local_logs" / "engine.log"


async def ws_connect():
    ws = await websockets.connect(ENGINE_WS, max_size=16 * 1024 * 1024)
    await ws.send(json.dumps({
        "type": "req", "id": uuid.uuid4().hex[:8], "method": "connect",
        "params": {
            "client": {"id": "verify-pm", "version": "1.0.0",
                       "platform": "python-test", "mode": "test"},
            "minProtocol": 3, "maxProtocol": 3,
        },
    }))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
        m = json.loads(raw)
        if m.get("type") == "event":
            continue
        if m.get("type") == "res" and m.get("ok"):
            return ws
        raise RuntimeError(f"connect rejected: {m.get('error')}")
    raise RuntimeError("no connect res")


async def send_chat(ws, session_key, extra_params=None):
    params = {"sessionKey": session_key, "message": "echo hi", "timeoutMs": 6000}
    if extra_params:
        params.update(extra_params)
    await ws.send(json.dumps({
        "type": "req", "id": uuid.uuid4().hex, "method": "chat.send", "params": params,
    }))
    try:
        for _ in range(3):
            raw = await asyncio.wait_for(ws.recv(), timeout=6)
            m = json.loads(raw)
            if m.get("type") == "res" and m.get("ok"):
                return True
    except asyncio.TimeoutError:
        pass
    return False


def log_line_for(session_token: str) -> str | None:
    if not ENGINE_LOG.exists():
        return None
    matches = [ln for ln in ENGINE_LOG.read_text().splitlines()
               if "_to_chat_request" in ln and session_token in ln]
    return matches[-1] if matches else None


async def main() -> int:
    print("=" * 60)
    print("  permission_mode fix verification")
    print("=" * 60)
    h = requests.get(f"{ENGINE_HTTP}/health", timeout=5).json()
    assert h.get("engine") == "claude_code", f"engine not ready: {h}"

    failures = []

    # ── Case A: normal user, update -> chat.send without permissionMode ──
    tok_a = uuid.uuid4().hex[:8]
    sk_a = f"agent:verify-pm:session:{tok_a}:user:verify-user"
    upd = requests.post(f"{ENGINE_HTTP}/api/sessions/{sk_a}/update",
                        params={"permission_mode": "default"}, timeout=10).json()
    pm_persisted = upd.get("data", {}).get("permission_mode")
    print(f"  [A] /update -> success={upd.get('success')} persisted permission_mode={pm_persisted}")
    if pm_persisted != "default":
        failures.append("A: /update did not persist permission_mode=default")

    ws = await ws_connect()
    await send_chat(ws, sk_a)  # NO permissionMode
    await ws.close()
    time.sleep(1)
    line_a = log_line_for(tok_a)
    print(f"  [A] engine log: {line_a}")
    if not line_a or "permission_mode=-" not in line_a:
        failures.append("A: normal-user chat.send should forward permission_mode=- (None)")
    else:
        print("  [A] PASS: engine forwarded None -> relay falls back to persisted binding")

    # ── Case B: explicit permissionMode (e.g. BCS sends bypassPermissions) -> forwarded ──
    tok_b = uuid.uuid4().hex[:8]
    sk_b = f"agent:verify-pm:session:{tok_b}:user:verify-user"
    ws = await ws_connect()
    await send_chat(ws, sk_b, extra_params={"permissionMode": "bypassPermissions"})
    await ws.close()
    time.sleep(1)
    line_b = log_line_for(tok_b)
    print(f"  [B] engine log: {line_b}")
    if not line_b or "permission_mode=bypassPermissions" not in line_b:
        failures.append("B: explicit permissionMode=bypassPermissions should be forwarded")
    else:
        print("  [B] PASS: explicit permissionMode forwarded as-is")

    print("=" * 60)
    if failures:
        for f in failures:
            print(f"  [FAIL] {f}")
        print("  RESULT: FAILED")
        return 1
    print("  RESULT: ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
