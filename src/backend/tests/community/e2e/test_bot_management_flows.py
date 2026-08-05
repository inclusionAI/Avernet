"""bot_management e2e business flows (real endpoints, LOCAL+SQLITE).

BOT_MANAGEMENT_LIFECYCLE_FLOWS is the single source of truth for this runner
and the E3 coverage guard.

Route-A keeps the cheap in-process paths here. Live-only flows that allocate
a real BaaS-backed singlebox device are skipped and covered by
tests/community/acceptance/bot_management.

This file additionally exercises a seeded round-trip: insert an ac_bots row directly via
DatabasePlugin.session() raw SQL (BotModel uses canonical Base, bootstrap
already creates ac_bots), then query list/get/check-name and assert the API
surfaces the seeded row.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community._flows.bot_management.api_lifecycle import BOT_MANAGEMENT_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow

IN_PROCESS_BOT_MANAGEMENT_FLOWS = [
    case for case in BOT_MANAGEMENT_LIFECYCLE_FLOWS if not case.live_only
]


def _seed_bot(world, *, bot_id: str, bot_name: str, entity_id: str) -> None:
    """Insert one ac_bots row via raw SQL.

    BotModel (plugin_api/models.py:26-53) has many NOT NULL columns with
    Python-side default= callables. Raw SQL bypasses those defaults, so the
    seed populates them explicitly with sensible values.
    """
    plugin = world.get(DatabasePlugin)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with plugin.session() as s:
        s.execute(text("""
            INSERT INTO ac_bots
              (bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id,
               owner_id, engine_types, active_engine, status, gmt_create,
               gmt_modified, is_delete, public, env, call_type,
               caller_config_revision)
            VALUES (:bot_id, :bot_name, :bot_desc, :entity_id, :entity_type,
                    :creator_id, :owner_id, :engine_types, :active_engine,
                    :status, :gmt_create, :gmt_modified, :is_delete,
                    :public, :env, :call_type, :caller_config_revision)
        """), {
            "bot_id": bot_id,
            "bot_name": bot_name,
            "bot_desc": f"E2E seeded bot {bot_name}",
            "entity_id": entity_id,
            "entity_type": "staff",
            "creator_id": entity_id,
            "owner_id": entity_id,
            "engine_types": json.dumps(["openclaw"]),
            "active_engine": "openclaw",
            "status": "PENDING",
            "gmt_create": now,
            "gmt_modified": now,
            "is_delete": 0,
            "public": "0",
            "env": "dev",
            "call_type": "owner",
            "caller_config_revision": 0,
        })
        s.commit()


@pytest.mark.e2e
@pytest.mark.parametrize("case", IN_PROCESS_BOT_MANAGEMENT_FLOWS, ids=lambda c: c.name)
def test_bot_management_flow(case, app_with_testing_modules, world):
    """3 read-only flows: list-empty / check-name-available / get-missing."""
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None


@pytest.mark.e2e
def test_bot_management_seeded_get_roundtrip(app_with_testing_modules, world):
    """Seed an ac_bots row; assert list + get-by-bot_id + check-name all
    surface it. Not a FlowCase because the seed is out-of-band and the
    list/check-name expects depend on the seeded value."""
    seeded_bot_id = "bot_e2e_seeded"
    seeded_bot_name = "E2E_Seeded_Bot"

    _seed_bot(
        world, bot_id=seeded_bot_id, bot_name=seeded_bot_name,
        entity_id="e2e_user",
    )

    client = TestClient(app_with_testing_modules, headers={"x-user-id": "e2e_user"})

    # 1) GET /api/bots/{bot_id} — must surface the seeded row.
    r = client.get(f"/api/bots/{seeded_bot_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    # The data envelope contains the bot's fields; verify bot_id is present.
    assert seeded_bot_id in json.dumps(body["data"]), body

    # 2) GET /api/bots/check/name?bot_name=<seeded> — exists=True.
    r = client.get(f"/api/bots/check/name?bot_name={seeded_bot_name}")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["exists"] is True, body

    # 3) GET /api/bots — list contains the seeded bot.
    # Envelope confirmed by Task 0 probe: data: {total, items}.
    r = client.get("/api/bots")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["total"] >= 1, body
    bot_ids_in_list = [item.get("bot_id") for item in body["data"]["items"]]
    assert seeded_bot_id in bot_ids_in_list, bot_ids_in_list
