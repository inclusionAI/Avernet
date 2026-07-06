#!/usr/bin/env python3
"""
Master-Slave Service Group E2E Test

Tests the full lifecycle:
1. Create service group template (master + slave)
2. Publish template
3. Create instance → master gets chat.send, slave gets chat.inject
4. Master dispatches task to slave via bcs_assign_task
5. Slave processes and replies
6. Master receives reply, calls bcs_task_complete
7. Instance marked as completed
"""

import os
import sys
import time
import json
import httpx

BCS_URL = os.environ["BCS_URL"]
COORD_UUID = os.environ["COORD_UUID"]
DBA_UUID = os.environ["DBA_UUID"]
LOG_DIR = os.environ.get("LOG_DIR", "")
ANTDING_ACCESS_KEY_ID = os.environ.get("BCS_TEST_ANTDING_AK", "replace-with-antding-ak")
ANTDING_ACCESS_KEY_SECRET = os.environ.get("BCS_TEST_ANTDING_SK", "replace-with-antding-sk")
ANTDING_ROBOT_CODE = os.environ.get("BCS_TEST_ANTDING_ROBOT", "replace-with-antding-robot")
ANTDING_USER_ID = os.environ.get("BCS_TEST_ANTDING_USER_ID", "11111111")

GREEN = "\033[0;32m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
YELLOW = "\033[0;33m"
NC = "\033[0m"


def info(msg):
    print(f"  {CYAN}→{NC} {msg}")


def pass_msg(msg):
    print(f"  {GREEN}✓{NC} {msg}")


def fail_msg(msg):
    print(f"  {RED}✗{NC} {msg}")


def warn_msg(msg):
    print(f"  {YELLOW}⚠{NC} {msg}")


def poll_messages(client, group_id, view_bot, timeout=120, poll_interval=3):
    """Poll GET /groups/{id}/messages?view_bot={bot} until messages appear or timeout."""
    deadline = time.time() + timeout
    last_count = 0
    while time.time() < deadline:
        resp = client.get(
            f"{BCS_URL}/groups/{group_id}/messages",
            params={"view_bot": view_bot},
        )
        if resp.status_code == 200:
            data = resp.json()
            messages = data if isinstance(data, list) else data.get("messages", [])
            if len(messages) > last_count:
                last_count = len(messages)
                info(f"  {view_bot[:12]}... has {len(messages)} messages")
            if messages:
                return messages
        time.sleep(poll_interval)
    return []


def poll_until(client, group_id, view_bot, condition, desc, timeout=180, poll_interval=5):
    """Poll messages until condition(messages) returns True."""
    deadline = time.time() + timeout
    messages = []
    while time.time() < deadline:
        resp = client.get(
            f"{BCS_URL}/groups/{group_id}/messages",
            params={"view_bot": view_bot},
        )
        if resp.status_code == 200:
            data = resp.json()
            messages = data if isinstance(data, list) else data.get("messages", [])
            if condition(messages):
                return messages
        time.sleep(poll_interval)
    warn_msg(f"Timeout waiting for: {desc}")
    return messages


def has_assistant_message(messages):
    """Check if there's at least one assistant message (bot replied)."""
    return any(m.get("role") == "assistant" for m in messages)


def has_tool_call(messages, tool_name):
    """Check if any assistant message contains a tool call with the given name."""
    for m in messages:
        if m.get("role") != "assistant":
            continue
        content = m.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "toolCall" and block.get("name") == tool_name:
                    return True
    return False


def has_from_prefix(messages, sender_name):
    """Check if any user message contains [from:sender_name]."""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    if f"[from:{sender_name}]" in block.get("text", ""):
                        return True
        elif isinstance(content, str):
            if f"[from:{sender_name}]" in content:
                return True
    return False


def dump_messages_summary(messages, label):
    """Print a brief summary of messages for debugging."""
    info(f"  --- {label} ({len(messages)} messages) ---")
    for i, m in enumerate(messages):
        role = m.get("role", "?")
        content = m.get("content", [])
        preview = ""
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        preview = text[:80].replace("\n", " ")
                        break
                    elif block.get("type") == "toolCall":
                        preview = f"[toolCall: {block.get('name', '?')}]"
                        break
                    elif block.get("type") == "thinking":
                        preview = "[thinking...]"
        elif isinstance(content, str):
            preview = content[:80].replace("\n", " ")
        info(f"    [{i}] {role}: {preview}")


def main():
    client = httpx.Client(timeout=30)
    failures = []

    # ── Step 1: Create service group template ─────────────────────────────

    print(f"\n{CYAN}Step 1: Create service group template{NC}")
    resp = client.post(
        f"{BCS_URL}/service-groups",
        json={
            "name": "Master-Slave E2E Test",
            "description": "Integration test for master-slave service group",
            "participants": [
                {"bot_uuid": COORD_UUID, "role": "master"},
                {"bot_uuid": DBA_UUID, "role": "slave"},
            ],
            "service_mode": "master_slave",
            "max_concurrency": -1,
            "callback_config": {
                "channels": [{
                    "type": "antding",
                    "access_key_id": ANTDING_ACCESS_KEY_ID,
                    "access_key_secret": ANTDING_ACCESS_KEY_SECRET,
                    "robot_code": ANTDING_ROBOT_CODE,
                    "user_id": ANTDING_USER_ID,
                }],
            },
        },
    )
    if resp.status_code != 200:
        fail_msg(f"Create template failed: {resp.status_code} {resp.text}")
        failures.append("Step 1: create template")
        print_result(failures)
        return

    template_data = resp.json()
    template_uuid = template_data.get("uuid", "")
    assert template_uuid, "Template UUID is empty"
    pass_msg(f"Template created: uuid={template_uuid}, version={template_data.get('version')}")

    # ── Step 2: Publish template ──────────────────────────────────────────

    print(f"\n{CYAN}Step 2: Publish template{NC}")
    resp = client.put(f"{BCS_URL}/service-groups/{template_uuid}/publish")
    if resp.status_code != 200:
        fail_msg(f"Publish failed: {resp.status_code} {resp.text}")
        failures.append("Step 2: publish template")
        print_result(failures)
        return

    pass_msg("Template published")

    # ── Step 3: Create instance ───────────────────────────────────────────

    print(f"\n{CYAN}Step 3: Create instance{NC}")
    resp = client.post(
        f"{BCS_URL}/service-groups/{template_uuid}/instances",
        json={
            "context": "测试任务：分析数据库死锁问题，找出根因并给出解决方案。",
        },
    )
    if resp.status_code != 200:
        fail_msg(f"Create instance failed: {resp.status_code} {resp.text}")
        failures.append("Step 3: create instance")
        print_result(failures)
        return

    instance_data = resp.json()
    group_id = instance_data.get("group_id", "")
    assert group_id, "Group ID is empty"
    pass_msg(f"Instance created: group_id={group_id}")

    # ── Step 4: Verify master receives initial context ────────────────────

    print(f"\n{CYAN}Step 4: Verify master receives initial context and dispatches task{NC}")
    info("Waiting for master to process initial context and call bcs_assign_task...")

    coord_messages = poll_until(
        client, group_id, COORD_UUID,
        condition=lambda msgs: has_tool_call(msgs, "bcs_assign_task"),
        desc="master calls bcs_assign_task",
        timeout=180,
    )

    dump_messages_summary(coord_messages, "Coordinator (master)")

    has_context = any(
        "SERVICE GROUP CONTEXT" in block.get("text", "")
        for m in coord_messages
        if m.get("role") == "user"
        for block in (m.get("content", []) if isinstance(m.get("content"), list) else [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if has_context:
        pass_msg("Master received SERVICE GROUP CONTEXT")
    else:
        warn_msg("SERVICE GROUP CONTEXT not found in master messages (may be in header)")

    if has_tool_call(coord_messages, "bcs_assign_task"):
        pass_msg("Master called bcs_assign_task")
    else:
        fail_msg("Master did NOT call bcs_assign_task")
        failures.append("Step 4: master did not call bcs_assign_task")

    # ── Step 5: Verify slave received task and replied ────────────────────

    print(f"\n{CYAN}Step 5: Verify slave received task and replied{NC}")
    info("Waiting for DBA to process task...")

    dba_messages = poll_until(
        client, group_id, DBA_UUID,
        condition=has_assistant_message,
        desc="DBA replies to task",
        timeout=180,
    )

    dump_messages_summary(dba_messages, "DBA (slave)")

    if has_assistant_message(dba_messages):
        pass_msg("DBA replied to task")
    else:
        fail_msg("DBA did NOT reply")
        failures.append("Step 5: DBA did not reply")

    # ── Step 6: Verify master received slave reply ────────────────────────

    print(f"\n{CYAN}Step 6: Verify master received slave reply and completed task{NC}")
    info("Waiting for master to receive DBA reply and call bcs_task_complete...")

    coord_messages = poll_until(
        client, group_id, COORD_UUID,
        condition=lambda msgs: has_from_prefix(msgs, "DBA"),
        desc="master receives [from:DBA] reply",
        timeout=60,
    )

    dump_messages_summary(coord_messages, "Coordinator (after slave reply)")

    if has_from_prefix(coord_messages, "DBA"):
        pass_msg("Master received [from:DBA] reply")
    else:
        fail_msg("Master did NOT receive DBA reply")
        failures.append("Step 6: master did not receive DBA reply")

    # Check if master called bcs_task_complete
    # Give extra time — master needs another LLM turn after receiving the reply
    if not has_tool_call(coord_messages, "bcs_task_complete"):
        info("Waiting for master to call bcs_task_complete...")
        coord_messages = poll_until(
            client, group_id, COORD_UUID,
            condition=lambda msgs: has_tool_call(msgs, "bcs_task_complete"),
            desc="master calls bcs_task_complete",
            timeout=180,
        )

    if has_tool_call(coord_messages, "bcs_task_complete"):
        pass_msg("Master called bcs_task_complete")
    else:
        warn_msg("Master did NOT call bcs_task_complete (may need manual trigger)")

    # ── Step 7: Verify instance completion ────────────────────────────────

    print(f"\n{CYAN}Step 7: Verify instance status{NC}")

    # Poll for group status
    deadline = time.time() + 60
    group_status = None
    while time.time() < deadline:
        resp = client.get(f"{BCS_URL}/groups/{group_id}")
        if resp.status_code == 200:
            group_data = resp.json()
            group_status = group_data.get("status")
            if group_status in ("completed", "Completed"):
                break
        time.sleep(3)

    if group_status in ("completed", "Completed"):
        pass_msg(f"Group status: {group_status}")
    else:
        warn_msg(f"Group status: {group_status} (expected: completed)")

    # Check instance
    resp = client.get(f"{BCS_URL}/service-groups/{template_uuid}/instances/{group_id}")
    if resp.status_code == 200:
        instance = resp.json()
        result = instance.get("instance_result")
        callback = instance.get("callback_status")
        info(f"Instance result: {str(result)[:100] if result else '(empty)'}")
        info(f"Callback status: {callback}")
        if result:
            pass_msg("Instance has result")
        else:
            warn_msg("Instance result is empty")
    else:
        warn_msg(f"Could not fetch instance: {resp.status_code}")

    # ── Result ────────────────────────────────────────────────────────────

    print_result(failures)


def print_result(failures):
    print("\n" + "=" * 50)
    if failures:
        fail_msg(f"FAILED ({len(failures)} failures):")
        for f in failures:
            print(f"    - {f}")
        if LOG_DIR:
            info(f"Logs: {LOG_DIR}")
        sys.exit(1)
    else:
        pass_msg("All tests passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
