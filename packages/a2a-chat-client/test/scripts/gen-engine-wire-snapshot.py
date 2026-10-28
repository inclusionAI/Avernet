#!/usr/bin/env python3
"""Regenerate test/fixtures/engine-wire-snapshot.json from the REAL engine kernel.

用法（在 Avernet 引擎 adapter 仓运行，本脚本自身不 import 引擎之外的任何东西）：

    cd <Avernet>/engine/adapter
    PYTHONPATH=src python3 <Avernet>/packages/a2a-chat-client/test/scripts/gen-engine-wire-snapshot.py \
        --out <Avernet>/packages/a2a-chat-client/test/fixtures/engine-wire-snapshot.json

产出 = 真引擎 A2aKernel 的一次完整 chat run 的**真实发布序**（engine_bridge
execute_and_stream 同构驱动）：
    open_run       → task 快照事件（成员 metadata={}，客户端 seq=null）
    publish_status → TASK_STATE_WORKING 状态帧
    publish_delta  → 增量（首块 append=false，后续 append=true）
    publish_final  → 终稿全量（append=false + lastChunk=true）
    complete_run   → 终态两连发：state-change statusUpdate(COMPLETED)@N+1（非 final）
                     + **同一 seq N+1** 的 final marker（kernel.py complete_run:156-180：
                     marker 以 r.last_seq 构造，而 r.last_seq 刚被 state-change 的
                     _publish 推到 N+1——共 seq 是引擎事实，不是缺陷）

序号收集自 open_run 后立即 subscribe(since_seq=0) 的订阅队列（backlog 的 task 事件
+ 后续 publish 事件按真实 publish order 进队），信封用 router SSE 同形
wire.kernel_event_to_wire(e) → {"jsonrpc":"2.0","id":<ENVELOPE_ID>,"result":e}。

⚠️ fixture 永不手改（快照漂移=fail-fast 测试抓）：改动引擎行为后重新跑本脚本。
运行 id 是 kernel 生成的 12-hex 随机 id——消费测试一律从 fixture 实际内容派生，
不得硬编码。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from engine.community.api.transport.a2a import wire
from engine.community.api.transport.a2a.kernel import A2aKernel

ENVELOPE_ID = 1
CONTEXT_ID = "c1"
IDEMPOTENCY_KEY = "msg-kernel-fixture-1"
DELTAS = ["你", "好"]
FINAL_TEXT = "你好！"

FIXTURE_HEADER = (
    "ENGINE-GENERATED SNAPSHOT — DO NOT HAND-EDIT. Regenerate via "
    "packages/a2a-chat-client/test/scripts/gen-engine-wire-snapshot.py "
    "(cd <Avernet>/engine/adapter && PYTHONPATH=src python3 <script> --out <this file>). "
    "Sequence = real A2aKernel publish order of one completed chat run "
    "(engine_bridge.execute_and_stream isomorphic drive): task snapshot, "
    "TASK_STATE_WORKING status, delta(你), delta(好), final(你好！ full-replacement), "
    "then complete_run's state-change statusUpdate(COMPLETED)@N+1 (non-final) followed "
    "by the SAME-SEQ terminal marker (kernel.complete_run:156-180). "
    "Consumer note: the run id inside members is kernel-generated (12 hex) — derive it "
    "from this file, never hardcode."
)


async def build_events() -> list[dict[str, Any]]:
    k = A2aKernel()
    rid, _created = await k.open_run(CONTEXT_ID, IDEMPOTENCY_KEY, "first")
    backlog, q = await k.subscribe(rid, since_seq=0)   # open 后即订阅：捕获真实发布序
    events = list(backlog)
    await k.publish_status(rid, "TASK_STATE_WORKING")  # engine_bridge 启动位（:96）
    for chunk in DELTAS:                              # chat delta → publish_delta
        await k.publish_delta(rid, chunk)
    await k.publish_final(rid, FINAL_TEXT)            # chat final → publish_final 全量终稿
    await k.complete_run(rid)                         # 终态：state-change@N+1 + 同 seq marker
    while not q.empty():
        events.append(q.get_nowait())
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="fixture 输出路径（engine-wire-snapshot.json）")
    args = ap.parse_args()

    events = asyncio.run(build_events())
    envelopes = [
        {"jsonrpc": "2.0", "id": ENVELOPE_ID,
         "result": wire.kernel_event_to_wire(e)}
        for e in events
    ]
    fixture = {"_header": FIXTURE_HEADER, "envelopes": envelopes}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 生成序一览（stdout 供评审；类型/seq 与 fixture 同源）
    print(f"wrote {args.out}: {len(envelopes)} events")
    for e in envelopes:
        result = e["result"]
        member = next(iter(result))
        meta = result[member].get("metadata", {})
        state = result[member].get("status", {}).get("state", "")
        print(f"  seq={meta.get('seq')} type={member} final={meta.get('final')!r} "
              f"state={state!r} append={result[member].get('append')!r} "
              f"lastChunk={result[member].get('lastChunk')!r}")


if __name__ == "__main__":
    main()
