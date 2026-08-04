"""Route-B acceptance: harness diagnose write-path smoke on a fresh bot.

The community singlebox config leaves the ``[llm]`` block commented out, so
``LLM.chat()`` short-circuits with ``"[llm disabled]"`` (no network). A freshly
created personal bot has no synced AGENTS/SOUL/TOOLS content yet, so
``POST /api/harness/diagnose`` drives ``_run_scan`` into its pre-check guard
(router.py: ``all_empty`` → status ``failed`` with "请耐心等待配置文件同步完成后，
再执行诊断"), without entering the LLM diagnostic body, and without generating
any patch.

This story is the first singlebox acceptance test to exercise the diagnose
write path end-to-end (handler → background ``_run_scan`` → scan-record
persistence → poll endpoint). It covers the previously-uncovered ``_run_scan``
orchestration and the empty-config guard, and pins the contract that diagnose
never fabricates patches for a bot whose config files are not yet synced.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import time

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
)

USER_ID = "e2e_user"
HEADERS = {"x-user-id": USER_ID}
POLL_TIMEOUT_SEC = 60
POLL_INTERVAL_SEC = 2


def _poll_until_terminal(client: httpx.Client, scan_id: int) -> dict:
    """Poll GET /api/harness/diagnose/{scan_id} until status is terminal."""
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
def test_diagnose_empty_bot_guarded_without_patches(live_backend, acceptance_fs_root):
    """POST /diagnose on a freshly created bot (no synced config files) is
    guarded: the scan fails fast with the empty-config reason and produces no
    patches — exercising the diagnose write-path orchestration end-to-end."""
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=30.0) as client:
        bot = create_live_personal_bot(
            client, user_id=USER_ID, bot_name_prefix="diag",
        )
        bot_id = bot["bot_id"]

        start = client.post(
            "/api/harness/diagnose",
            json={
                "bot_id": bot_id,
                "entity_id": USER_ID,
                "entity_type": "staff",
                "scan_type": "full",
                "layer": "L1",
                "trigger_source": "api",
            },
        )
        # Diagnose start returns 202 Accepted.
        assert start.status_code == 202, start.text
        start_body = start.json()
        assert start_body["success"] is True, start_body
        scan_id = start_body["scan_id"]

        poll = _poll_until_terminal(client, scan_id)
        # A fresh bot with no synced config files is guarded: the scan must
        # reach a terminal state (failed fast), never hang in scanning.
        assert poll["status"] in ("completed", "failed"), poll
        # The empty-config pre-check surfaces a synchronous reason when it fires.
        failed_reason = poll.get("failed_reason") or ""
        if poll["status"] == "failed":
            assert "同步" in failed_reason, (
                f"expected empty-config guard reason, got: {failed_reason!r}"
            )
            # Each file-type probe is marked empty/error, not left pending.
            for item in poll.get("diagnose_progress") or []:
                assert item["status"] == "error", item
                assert item["result"] == "error", item

        # No patches may be generated for a guarded/empty-config diagnose.
        assert poll.get("patch_progress") == [], (
            f"no patches expected, got: {poll.get('patch_progress')}"
        )
        records = client.get(
            "/api/harness/patch-records",
            params={"bot_id": bot_id, "entity_id": USER_ID},
        )
        assert records.status_code == 200, records.text
        records_body = records.json()
        assert records_body.get("success") is True, records_body
        assert records_body.get("items") == [], (
            f"no patch records expected, got: {records_body.get('items')}"
        )
