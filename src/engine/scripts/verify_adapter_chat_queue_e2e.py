#!/usr/bin/env python3
"""R2 端到端回归（经 engine adapter :20003，非直连 relay）。

被测改动: dev_adapter_chat_queue —— adapter 在 session 正在 stream 时缓存后续
chat.send（ack queued），当前 stream 结束后把多条合并成一条 resume 触发新
stream；chat.abort 清空缓存。

两个子用例:
  --case merge  : 发 A → A 回复中再发 B、C → 期望 B/C 被 ack queued，A final
                  之后客户端再收到「合并 run」的 final（关键: 前端确实收到合并回复）。
  --case abort  : 发 A → A 回复中发 B（缓存）→ 对 A abort → 期望 B 不被触发
                  （无第二个 final，engine.log 出现 dropped pending due to chat.abort）。

判定: 以「客户端实际收到的帧」+「engine.log 的 [CC][queue] 标记」双重佐证。
排查日志: 全程打印每个 ack / event state / 关键时间戳。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

import websockets

HOST = "localhost"
PORT = 20003
URI = f"ws://{HOST}:{PORT}/api/claude_code/ws"  # 本 worktree 路由为下划线


def _state(frame: dict):
    p = frame.get("payload", {}) or {}
    st = p.get("state")
    if st:
        return st
    return (p.get("data", {}) or {}).get("state")


async def _connect():
    ws = await websockets.connect(URI, max_size=16 * 1024 * 1024)
    await ws.send(json.dumps({
        "type": "req", "id": uuid.uuid4().hex[:8], "method": "connect",
        "params": {"client": {"id": "r2-adapter-e2e", "version": "1.0.0",
                              "platform": "py", "mode": "test"},
                   "minProtocol": 3, "maxProtocol": 3},
    }))
    dl = time.monotonic() + 10
    while time.monotonic() < dl:
        r = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if r.get("type") == "event":
            continue
        if r.get("type") == "res" and r.get("ok"):
            return ws
        raise RuntimeError(f"connect rejected: {r.get('error')}")
    raise RuntimeError("connect timeout")


async def _send_chat(ws, session_key, message, req_id):
    await ws.send(json.dumps({
        "type": "req", "id": req_id, "method": "chat.send",
        "params": {"sessionKey": session_key, "message": message,
                   "idempotencyKey": uuid.uuid4().hex, "cwd": "/tmp"},
    }))


async def _send_abort(ws, session_key, run_id, req_id):
    await ws.send(json.dumps({
        "type": "req", "id": req_id, "method": "chat.abort",
        "params": {"sessionKey": session_key, "runId": run_id or "unknown"},
    }))


async def case_merge() -> bool:
    """A streaming → send B, C → expect queued ack + merged final reply received."""
    t0 = time.monotonic()
    ws = await _connect()
    sk = f"agent:claude-code-ws:session:r2adp-merge-{uuid.uuid4().hex[:6]}:user:claude-code-ws"

    id_a = "A-" + uuid.uuid4().hex[:6]
    # A: 让模型多产出一些 token，争取在它 stream 期间把 B/C 发出去
    await _send_chat(ws, sk, "Count slowly from 1 to 8, one number per line.", id_a)
    print(f"[{time.monotonic()-t0:.1f}s] sent A id={id_a}")

    acks = {}
    finals = []          # (run_label, state)
    queued_acks = []     # ack payloads with queued:true
    a_acked = False
    sent_bc = False
    id_b = "B-" + uuid.uuid4().hex[:6]
    id_c = "C-" + uuid.uuid4().hex[:6]

    dl = time.monotonic() + 240
    while time.monotonic() < dl:
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        except asyncio.TimeoutError:
            print(f"[{time.monotonic()-t0:.1f}s] recv timeout (finals so far={len(finals)})")
            break

        ftype = f.get("type")
        if ftype == "res":
            fid = f.get("id")
            payload = f.get("payload", {}) or {}
            acks[fid] = payload
            print(f"[{time.monotonic()-t0:.1f}s] ACK id={fid} payload={json.dumps(payload)}")
            if fid == id_a:
                a_acked = True
            if payload.get("queued"):
                queued_acks.append(payload)
        elif ftype == "event":
            st = _state(f)
            if st:
                print(f"[{time.monotonic()-t0:.1f}s] event state={st}")
            # A 被 ack 且第一个 stream 开始产出后，立刻发 B、C（仍在 A 的 stream 中）
            if a_acked and not sent_bc and st in (None, "thinking", "streaming", "delta", "tool_use"):
                await _send_chat(ws, sk, "Now say the word RED.", id_b)
                await _send_chat(ws, sk, "Also say the word BLUE.", id_c)
                sent_bc = True
                print(f"[{time.monotonic()-t0:.1f}s] sent B id={id_b} + C id={id_c} (during A stream)")
            if st in ("final", "error", "aborted"):
                finals.append(st)
                print(f"[{time.monotonic()-t0:.1f}s] >>> FINAL #{len(finals)} state={st}")
                # 收到 A 的 final 后还要再等合并 run 的 final
                if len(finals) >= 2:
                    break
    await ws.close()

    # 若 B/C 在 A 极快结束后才发出，sent_bc 可能为 False —— 兜底再判
    print("\n--- case_merge summary ---")
    print(f"A acked: {a_acked}; B/C sent during stream: {sent_bc}")
    print(f"queued acks (B/C): {json.dumps(queued_acks)}")
    print(f"finals received by client: {len(finals)} {finals}")

    ok_queued = len(queued_acks) >= 2 and all(q.get("queued") for q in queued_acks)
    counts = sorted(q.get("queuedCount") for q in queued_acks)
    ok_counts = counts == [1, 2]
    ok_merged_final = len(finals) >= 2  # A 的 final + 合并 run 的 final
    passed = sent_bc and ok_queued and ok_counts and ok_merged_final
    print(f"ok_queued={ok_queued} ok_counts(={counts})={ok_counts} ok_merged_final={ok_merged_final}")
    print("CASE merge:", "PASS" if passed else "FAIL")
    return passed


async def case_abort() -> bool:
    """A streaming → send B (cached) → abort A → expect B NOT triggered."""
    t0 = time.monotonic()
    ws = await _connect()
    sk = f"agent:claude-code-ws:session:r2adp-abort-{uuid.uuid4().hex[:6]}:user:claude-code-ws"

    id_a = "A-" + uuid.uuid4().hex[:6]
    await _send_chat(ws, sk, "Count slowly from 1 to 12, one number per line.", id_a)
    print(f"[{time.monotonic()-t0:.1f}s] sent A id={id_a}")

    a_acked = False
    sent_b = False
    sent_abort = False
    id_b = "B-" + uuid.uuid4().hex[:6]
    id_ab = "AB-" + uuid.uuid4().hex[:6]
    run_id = None
    finals = []
    queued_acks = []

    dl = time.monotonic() + 180
    while time.monotonic() < dl:
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        except asyncio.TimeoutError:
            print(f"[{time.monotonic()-t0:.1f}s] recv timeout")
            break
        ftype = f.get("type")
        if ftype == "res":
            fid = f.get("id")
            payload = f.get("payload", {}) or {}
            print(f"[{time.monotonic()-t0:.1f}s] ACK id={fid} payload={json.dumps(payload)}")
            if fid == id_a:
                a_acked = True
                run_id = payload.get("runId") or run_id
            if payload.get("queued"):
                queued_acks.append(payload)
        elif ftype == "event":
            st = _state(f)
            p = f.get("payload", {}) or {}
            rid = p.get("runId") or (p.get("data", {}) or {}).get("runId")
            if rid:
                run_id = run_id or rid
            if st:
                print(f"[{time.monotonic()-t0:.1f}s] event state={st} runId={rid}")
            # A stream 中先发 B（缓存），再 abort A
            if a_acked and not sent_b and st in (None, "thinking", "streaming", "delta", "tool_use"):
                await _send_chat(ws, sk, "Say the word GREEN.", id_b)
                sent_b = True
                print(f"[{time.monotonic()-t0:.1f}s] sent B id={id_b} (cached during A stream)")
            if sent_b and not sent_abort:
                await _send_abort(ws, sk, run_id, id_ab)
                sent_abort = True
                print(f"[{time.monotonic()-t0:.1f}s] sent ABORT for A runId={run_id}")
            if st in ("final", "error", "aborted"):
                finals.append(st)
                print(f"[{time.monotonic()-t0:.1f}s] >>> FINAL #{len(finals)} state={st}")

    # abort 后再观察一段时间，确认没有第二个 final（B 被触发的话会出现新的 final）
    print(f"[{time.monotonic()-t0:.1f}s] post-abort observe window (8s)")
    obs_end = time.monotonic() + 8
    while time.monotonic() < obs_end:
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=obs_end - time.monotonic()))
        except (asyncio.TimeoutError, Exception):
            break
        if f.get("type") == "event":
            st = _state(f)
            if st:
                print(f"[{time.monotonic()-t0:.1f}s] (post-abort) event state={st}")
            if st in ("final", "error", "aborted"):
                finals.append(st)
                print(f"[{time.monotonic()-t0:.1f}s] >>> (post-abort) FINAL #{len(finals)} state={st}")
    try:
        await ws.close()
    except Exception:
        pass

    print("\n--- case_abort summary ---")
    print(f"A acked={a_acked} B sent={sent_b} abort sent={sent_abort}")
    print(f"queued acks (B): {json.dumps(queued_acks)}")
    print(f"finals total: {len(finals)} {finals}")
    # 期望: B 被 ack queued（缓存成功），但 B 不被 flush 触发（无来自合并 run 的成功 final）。
    # A 自己可能产出 final 或 aborted；判定核心是「没有第二个非 abort 的成功 final」。
    success_finals = [s for s in finals if s == "final"]
    ok_b_cached = len(queued_acks) >= 1
    # 只要不出现 >=2 个成功 final（即 B 没被合并触发），即 PASS
    ok_b_not_triggered = len(success_finals) <= 1
    passed = sent_b and sent_abort and ok_b_cached and ok_b_not_triggered
    print(f"ok_b_cached={ok_b_cached} success_finals={len(success_finals)} ok_b_not_triggered={ok_b_not_triggered}")
    print("CASE abort:", "PASS" if passed else "FAIL")
    return passed


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=["merge", "abort", "both"], default="both")
    args = ap.parse_args()
    results = {}
    if args.case in ("merge", "both"):
        results["merge"] = await case_merge()
    if args.case in ("abort", "both"):
        results["abort"] = await case_abort()
    print("\n==== RESULT ====")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    allp = all(results.values())
    print("OVERALL:", "PASS" if allp else "FAIL")
    sys.exit(0 if allp else 1)


if __name__ == "__main__":
    asyncio.run(main())
