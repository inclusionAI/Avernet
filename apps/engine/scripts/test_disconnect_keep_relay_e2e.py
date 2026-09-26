#!/usr/bin/env python3
"""端到端验证: 前端 WS 断连时 relay 是否继续执行(不被 abort)。

场景:
  1. 建立 WS 连接, handshake
  2. 发 chat.send 启动一个 run
  3. 收到几个 stream 事件后, 直接关闭 WS(模拟前端刷新)
  4. 等待几秒, 新建连接查 session.status
  5. 检查 relay 日志确认未收到 chat.abort

判定:
  - session.status 在断连后短期内 processing=true → relay 仍在跑(PASS)
  - relay 日志无 "chat.abort" 针对该 session → 未被叫停(PASS)
"""
import asyncio
import json
import time
import uuid

import websockets

WS_URL = "ws://localhost:20003/api/claude_code/ws"
SESSION_KEY = f"agent:claude-code-ws:session:{uuid.uuid4()}:user:claude-code-ws"


def _connect_frame():
    return {
        "type": "req",
        "id": str(uuid.uuid4()),
        "method": "connect",
        "params": {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {"id": "e2e", "version": "1.0.0", "platform": "test", "mode": "cli"},
        },
    }


def _chat_send_frame():
    return {
        "type": "req",
        "id": str(uuid.uuid4()),
        "method": "chat.send",
        "params": {
            "sessionKey": SESSION_KEY,
            "message": "请用中文写一首关于春天的长诗，至少八段，每段四行，慢慢展开。",
            "cwd": "/tmp",
        },
    }


def _status_frame():
    return {
        "type": "req",
        "id": str(uuid.uuid4()),
        "method": "session.status",
        "params": {"sessionKey": SESSION_KEY},
    }


async def phase1_send_then_disconnect():
    """连接 → handshake → chat.send → 收到若干事件后断开。"""
    ws = await websockets.connect(WS_URL, max_size=None)
    await ws.send(json.dumps(_connect_frame()))
    hello = json.loads(await ws.recv())
    assert hello.get("ok"), f"handshake failed: {hello}"
    print(f"[e2e] handshake ok, connId={hello['payload']['server']['connId']}")

    await ws.send(json.dumps(_chat_send_frame()))
    print(f"[e2e] chat.send sent, sessionKey={SESSION_KEY}")

    events = 0
    t0 = time.monotonic()
    # 收到至少 3 个事件或 20s 后断开
    while events < 3 and (time.monotonic() - t0) < 20:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
        except asyncio.TimeoutError:
            break
        frame = json.loads(raw)
        if frame.get("type") == "event":
            events += 1
            payload = frame.get("payload", {})
            stream = payload.get("stream") or payload.get("state") or ""
            print(f"[e2e] event #{events}: {stream}")
    print(f"[e2e] received {events} events, now HARD-CLOSING ws (simulate refresh)")
    # 硬关闭, 模拟刷新
    await ws.close(code=1001)
    return events


async def phase2_check_status(delay=4.0):
    """断连后等待, 新连接查 session.status。"""
    print(f"[e2e] waiting {delay}s after disconnect...")
    await asyncio.sleep(delay)

    ws = await websockets.connect(WS_URL, max_size=None)
    await ws.send(json.dumps(_connect_frame()))
    hello = json.loads(await ws.recv())
    assert hello.get("ok"), f"handshake failed: {hello}"

    await ws.send(json.dumps(_status_frame()))
    # status 可能夹杂事件, 找 res
    for _ in range(10):
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        frame = json.loads(raw)
        if frame.get("type") == "res":
            await ws.close()
            return frame
    await ws.close()
    return None


async def main():
    events = await phase1_send_then_disconnect()
    status = await phase2_check_status(delay=4.0)
    print("\n========== RESULT ==========")
    print(f"events_before_disconnect: {events}")
    print(f"session.status after reconnect: {json.dumps(status, ensure_ascii=False)}")
    if status and status.get("ok"):
        payload = status.get("payload", {})
        processing = payload.get("processing")
        print(f"processing = {processing}")
        if processing is True:
            print("[PASS] relay 仍在执行 (processing=true), 未被断连 abort")
        else:
            print("[INFO] processing=false — 可能 run 已自然完成或被中止, 需结合 relay 日志判断")
    else:
        print("[WARN] 未拿到有效 session.status")
    print("============================")


if __name__ == "__main__":
    asyncio.run(main())
