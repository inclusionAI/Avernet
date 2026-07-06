#!/usr/bin/env python3
"""
E2E test for BCS state-machine runtime with real OpenClaw bots.

Setup.sh must have been run first to start BCS + 3 OpenClaw instances.
This test creates a real state_machine group, posts a YAML definition to
/groups/{id}/state-machine-runs, and polls the run until BCS completes it from
real bot terminal events.

Usage:
    BCS_URL=... COORD_UUID=... DBA_UUID=... DEVOPS_UUID=... \
    COORD_TOKEN=... python3 test_state_machine_runtime.py
"""

import asyncio
import os
import sys
import textwrap
import time

try:
    import httpx
except ImportError:
    print("ERROR: 'httpx' required. Install: pip3 install httpx")
    sys.exit(1)

BCS_URL = os.environ.get("BCS_URL", "http://127.0.0.1:21000")
COORD_UUID = os.environ.get("COORD_UUID", "")
DBA_UUID = os.environ.get("DBA_UUID", "")
DEVOPS_UUID = os.environ.get("DEVOPS_UUID", "")
COORD_TOKEN = os.environ.get("COORD_TOKEN", "")

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
CYAN = "\033[0;36m"
GRAY = "\033[0;90m"
NC = "\033[0m"

RUN_TIMEOUT_SECONDS = int(os.environ.get("STATE_MACHINE_RUN_TIMEOUT_SECONDS", "240"))


def ok(msg):
    print(f"  {PASS} {msg}")


def fail_msg(msg):
    print(f"  {FAIL} {msg}")


def info(msg):
    print(f"  {CYAN}→{NC} {msg}")


def dim(msg):
    print(f"  {GRAY}{msg}{NC}")


def require_env():
    missing = [
        name
        for name, value in {
            "COORD_UUID": COORD_UUID,
            "DBA_UUID": DBA_UUID,
            "DEVOPS_UUID": DEVOPS_UUID,
            "COORD_TOKEN": COORD_TOKEN,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing required env vars from setup.sh: {', '.join(missing)}")


async def create_state_machine_group(client):
    body = {
        "driver_bot": COORD_UUID,
        "participants": [
            {"bot_uuid": COORD_UUID, "role": "driver"},
            {"bot_uuid": DBA_UUID, "role": "consultant"},
            {"bot_uuid": DEVOPS_UUID, "role": "consultant"},
        ],
        "group_strategy": "state_machine",
        "context": "状态机 runtime E2E：Coordinator 先理解任务，再交给 DBA 给出最终结论。",
    }
    resp = await client.post(
        f"{BCS_URL}/groups",
        headers={"Authorization": f"Bearer {COORD_TOKEN}"},
        json=body,
        timeout=10,
    )
    assert resp.status_code == 200, f"create group failed: {resp.status_code} {resp.text}"
    data = resp.json()
    group_id = data.get("id") or data.get("group_id")
    assert group_id, f"missing group_id in response: {data}"
    return group_id


def state_machine_yaml():
    return textwrap.dedent(
        f"""
        api_version: bcs.collaboration/v1
        id: real_bot_state_machine_smoke
        version: 1
        name: Real Bot State Machine Smoke
        metadata:
          description: Real OpenClaw bots execute a minimal transition-based BCS state machine.
          labels:
            scenario: e2e_state_machine
        participants:
          coordinator:
            bot_id: "{COORD_UUID}"
            bcs_participant_role: driver
            required: true
          dba:
            bot_id: "{DBA_UUID}"
            bcs_participant_role: consultant
            required: true
        runtime:
          kind: state_machine
          state_machine:
            version: 1
            graph_mode: acyclic
            projection:
              default_visibility: private
            defaults:
              node_timeout_ms: 180000
              max_attempts: 1
            nodes:
              understand:
                kind: bot_task
                display_name: Understand Request
                assignee:
                  type: bot_binding
                  binding: coordinator
                instruction: |
                  Read the input question. Output one short sentence describing what the DBA should review.
                transitions:
                  complete:
                    targets: [dba_review]

              dba_review:
                kind: bot_task
                display_name: DBA Review
                assignee:
                  type: bot_binding
                  binding: dba
                instruction: |
                  Use the upstream summary and input question. Output a concise final DBA recommendation.
                final_output: true
        """
    ).strip()


async def start_run(client, group_id):
    resp = await client.post(
        f"{BCS_URL}/groups/{group_id}/state-machine-runs",
        headers={"Authorization": f"Bearer {COORD_TOKEN}"},
        json={
            "definition_yaml": state_machine_yaml(),
            "input": {
                "question": "A production service reports intermittent database deadlocks. Give one DBA recommendation."
            },
        },
        timeout=10,
    )
    assert resp.status_code == 202, f"start run failed: {resp.status_code} {resp.text}"
    data = resp.json()
    run_id = data.get("run", {}).get("run_id")
    assert run_id, f"missing run_id in response: {data}"
    return data


async def get_run(client, run_id):
    resp = await client.get(
        f"{BCS_URL}/state-machine-runs/{run_id}",
        headers={"Authorization": f"Bearer {COORD_TOKEN}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"get run failed: {resp.status_code} {resp.text}"
    return resp.json()


async def wait_for_completed_run(client, run_id):
    started = time.monotonic()
    last_status = None
    while time.monotonic() - started < RUN_TIMEOUT_SECONDS:
        view = await get_run(client, run_id)
        run = view.get("run", {})
        status = run.get("status")
        nodes = view.get("nodes", [])
        if status != last_status:
            info(f"run status = {status}")
            last_status = status
        for node in nodes:
            dim(
                f"  node {node.get('node_id')}: status={node.get('status')} "
                f"attempt={node.get('attempt')} bot={str(node.get('assignee_bot_id'))[:8]}"
            )
        if status == "completed":
            return view
        if status in ("failed", "aborted"):
            raise RuntimeError(f"state-machine run ended with {status}: {view}")
        await asyncio.sleep(5)
    raise TimeoutError(f"state-machine run did not complete within {RUN_TIMEOUT_SECONDS}s")


async def main():
    require_env()
    info("Creating state_machine group with real bots...")
    async with httpx.AsyncClient() as client:
        group_id = await create_state_machine_group(client)
        ok(f"Group created: {group_id}")

        info("Starting state-machine run from YAML...")
        started = await start_run(client, group_id)
        run_id = started["run"]["run_id"]
        ok(f"Run started: {run_id}")

        info("Waiting for real bot terminal events to drive the state machine...")
        completed = await wait_for_completed_run(client, run_id)

    nodes = completed.get("nodes", [])
    by_id = {node.get("node_id"): node for node in nodes}
    final_output = completed.get("run", {}).get("output")
    assert final_output, f"completed run missing output: {completed}"
    assert by_id["understand"]["status"] == "completed", completed
    assert by_id["dba_review"]["status"] == "completed", completed
    assert by_id["dba_review"].get("artifact_text"), completed

    ok("State-machine run completed from real bot events")
    dim(f"Final output: {final_output[:300]}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        fail_msg(str(exc))
        sys.exit(1)
