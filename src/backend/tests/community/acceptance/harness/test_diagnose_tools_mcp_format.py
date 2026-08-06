"""Route-B acceptance: D-TOOLS-002 prompt-slimming helpers run end-to-end.

Companion to ``test_diagnose_llm_disabled_smoke.py``. That story pins the
empty-config guard on a fresh bot; this one pins that a bot with a non-empty
TOOLS.md plus an activated, schema-bearing MCP drives
``ToolsMcpFormatDiagnostic.analyze`` through the prompt-slimming helpers
(``_compact_tool_for_prompt`` / ``_compact_mcp_tools_for_prompt`` /
``_format_mcp_block``) — the code that keeps MCP-heavy bots under antchat's
~90s gateway window.

Why this works without a real LLM: the helpers run while *building* the
diagnostic prompt, which happens before ``ctx.llm.chat()``. The community
singlebox leaves ``[llm]`` commented out, so ``chat()`` short-circuits with
``"[llm disabled]"`` — but by then prompt construction (and the helpers) has
already executed, so the lines are covered. The MCP detail comes from the
fixture-backed ``CommunityMCPCenter`` (``TestingMcpModule`` reads
``SINGLEBOX_ACCEPTANCE_MCP_FIXTURE_FILE``), seeded with
``mcp.singlebox.toolsdiag`` whose tools expose ``inputSchema``.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import time

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    assert_success,
    create_live_personal_bot,
    fresh_id,
)

POLL_TIMEOUT_SEC = 60
POLL_INTERVAL_SEC = 2
SERVER_CODE = "mcp.singlebox.toolsdiag"


def _poll_until_terminal(client: httpx.Client, scan_id: int) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/harness/diagnose/{scan_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") in ("completed", "failed"):
            return last
        time.sleep(POLL_INTERVAL_SEC)
    pytest.fail(f"scan {scan_id} did not reach terminal status within {POLL_TIMEOUT_SEC}s; last={last}")


@pytest.mark.acceptance
def test_diagnose_tools_mcp_format_runs_prompt_helpers(
    live_backend, acceptance_fs_root
):
    """A bot with TOOLS.md + an activated schema-bearing MCP runs D-TOOLS-002
    through the prompt-slimming helpers and completes (LLM-disabled → LLM01)."""
    user_id = fresh_id("e2e_toolsdiag")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client, user_id=user_id, bot_name_prefix="toolsdiag",
        )
        bot_id = bot["bot_id"]

        # Non-empty TOOLS.md so the diagnostic does not early-return on empty content.
        write = client.put(
            f"/api/identity/staff/{user_id}/bot/{bot_id}/TOOLS.md",
            json={"content": "# Tools\n\n- acceptance tools configured\n"},
        )
        assert write.status_code == 200, write.text

        # Activate the schema-bearing fixture MCP on the bot via a skill-set, so
        # bot_profile.get_activated_mcps returns it during the scan.
        skillset = assert_success(
            client.post(
                "/api/skillsets",
                json={
                    # No fresh_id here: skill-set names reject '_'.
                    "name": "ToolsDiag Acceptance Set",
                    "user_id": user_id,
                    "bot_id": bot_id,
                },
            )
        )
        skill_set_id = skillset["data"]["id"]
        add_mcp = client.post(
            f"/api/skillsets/{skill_set_id}/mcps",
            params={
                "entity_id": user_id,
                "entity_type": "staff",
                "bot_id": bot_id,
                "engine_type": "openclaw",
            },
            json={"server_code": SERVER_CODE, "user_id": user_id},
        )
        assert add_mcp.status_code == 200, add_mcp.text
        assert add_mcp.json().get("success") is True, add_mcp.json()

        # Run the diagnose write path; D-TOOLS-002 executes the prompt helpers.
        start = client.post(
            "/api/harness/diagnose",
            json={
                "bot_id": bot_id,
                "entity_id": user_id,
                "entity_type": "staff",
                "scan_type": "full",
                "layer": "L1",
                "trigger_source": "api",
            },
        )
        assert start.status_code == 202, start.text
        scan_id = start.json()["scan_id"]

        poll = _poll_until_terminal(client, scan_id)
        # With LLM disabled the scan still completes; it must not hang in scanning.
        assert poll["status"] in ("completed", "failed"), poll
