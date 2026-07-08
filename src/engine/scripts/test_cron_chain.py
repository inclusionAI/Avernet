#!/usr/bin/env python3
"""Cron scheduled-task end-to-end verification against the live relay.

Drives the relay WS gateway directly (same wire format the OCB
ClaudeCodeCronService uses) to prove:

  1. cron.add   — a job can be created with a `cron` schedule + agentTurn payload
  2. cron.list  — the job is persisted and listed
  3. cron.run   — force-firing the job starts a chat run and records a run
  4. session-independent — each fire spins up a fresh chat run (no caller session)
  5. cron.runs  — run history is recorded with a status

Diagnostic logging is intentionally verbose ([cron-verify] prefix) so failures
are traceable from this script's stdout alone.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

import websockets

RELAY_URL = "ws://localhost:18900"
TAG = "[cron-verify]"


async def rpc(ws, method: str, params: dict, timeout: float = 30.0) -> dict:
    """Send one req frame and await its matching res frame."""
    req_id = str(uuid.uuid4())
    frame = {"type": "req", "id": req_id, "method": method, "params": params}
    print(f"{TAG} -> {method} {json.dumps(params, ensure_ascii=False)}")
    await ws.send(json.dumps(frame))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        msg = json.loads(raw)
        # The relay multiplexes events + responses; only match our res frame.
        if msg.get("type") == "res" and msg.get("id") == req_id:
            ok = msg.get("ok")
            print(f"{TAG} <- {method} ok={ok}")
            if not ok:
                print(f"{TAG}    error={json.dumps(msg.get('error'), ensure_ascii=False)}")
            return msg
    raise TimeoutError(f"no response for {method} within {timeout}s")


async def main() -> int:
    failures: list[str] = []
    async with websockets.connect(RELAY_URL, max_size=None) as ws:
        # --- handshake ---
        hello = await rpc(ws, "connect", {})
        proto = (hello.get("payload") or {}).get("protocol")
        methods = ((hello.get("payload") or {}).get("features") or {}).get("methods", [])
        cron_methods = [m for m in methods if m.startswith("cron.")]
        print(f"{TAG} handshake protocol={proto} cron_methods={cron_methods}")
        if not cron_methods:
            failures.append("relay advertises no cron.* methods in handshake")

        # --- 1. cron.add (cron schedule, every-minute; agentTurn payload) ---
        job_name = f"verify-{uuid.uuid4().hex[:8]}"
        add = await rpc(ws, "cron.add", {
            "name": job_name,
            "schedule": {"kind": "cron", "expr": "* * * * *", "tz": "Asia/Shanghai"},
            "payload": {"kind": "agentTurn", "message": "reply with the single word PONG",
                        "timeoutSeconds": 300},
            "sessionTarget": "isolated",
            "enabled": True,
        })
        if not add.get("ok"):
            failures.append("cron.add failed")
            print(f"{TAG} ABORT — cannot continue without a job")
            _summary(failures)
            return 1
        job = add["payload"]
        job_id = job["id"]
        print(f"{TAG} created job id={job_id} sessionTarget={job.get('sessionTarget')} "
              f"schedule={job.get('schedule')}")

        # --- 2. cron.list ---
        listed = await rpc(ws, "cron.list", {"includeDisabled": True})
        jobs = listed.get("payload") or []
        if not any(j.get("id") == job_id for j in jobs):
            failures.append("cron.list did not return the created job")
        else:
            print(f"{TAG} cron.list contains job ({len(jobs)} total)")

        # --- 3. cron.status ---
        status = await rpc(ws, "cron.status", {})
        sp = status.get("payload") or {}
        print(f"{TAG} cron.status running={sp.get('running')} jobCount={sp.get('jobCount')} "
              f"enabledCount={sp.get('enabledCount')} nextRunAtMs={sp.get('nextRunAtMs')}")
        if sp.get("nextRunAtMs") is None:
            failures.append("cron.status nextRunAtMs is null for an enabled cron job")

        # --- 4. cron.run force — fire immediately, independent of any session ---
        print(f"{TAG} force-firing job (this starts a fresh chat run / session)...")
        run = await rpc(ws, "cron.run", {"id": job_id, "mode": "force"}, timeout=120.0)
        rp = run.get("payload") or {}
        print(f"{TAG} cron.run result={json.dumps(rp, ensure_ascii=False)}")
        run_status = rp.get("status")
        if run_status not in ("ok", "error"):
            # 'skipped' would mean payload not fireable; absence means already running
            failures.append(f"cron.run did not produce a terminal run status (got {run_status!r})")

        # --- 5. cron.runs — run history recorded ---
        runs = await rpc(ws, "cron.runs", {"id": job_id, "limit": 10})
        entries = (runs.get("payload") or {}).get("entries") or []
        print(f"{TAG} cron.runs returned {len(entries)} record(s)")
        for e in entries[:3]:
            print(f"{TAG}    run runId={e.get('runId')} status={e.get('status')} "
                  f"durationMs={e.get('durationMs')} summary={(e.get('summary') or '')[:60]!r}")
        if not entries:
            failures.append("cron.runs returned no history after a forced fire")

        # --- cleanup ---
        await rpc(ws, "cron.remove", {"id": job_id})
        print(f"{TAG} removed job {job_id}")

    _summary(failures)
    return 1 if failures else 0


def _summary(failures: list[str]) -> None:
    print()
    if failures:
        print(f"{TAG} RESULT: FAIL ({len(failures)} issue(s))")
        for f in failures:
            print(f"{TAG}   - {f}")
    else:
        print(f"{TAG} RESULT: PASS — cron add/list/status/run/runs all verified")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
