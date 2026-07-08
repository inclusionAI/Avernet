"""bot_collaborator e2e business flows (real endpoints, LOCAL+SQLITE).

BOT_COLLABORATOR_LIFECYCLE_FLOWS is the single source of truth for this
runner and the E3 coverage guard.

This file additionally exercises 2 stateful round-trips that can't be
modeled as FlowCases (they need seeded ac_bots row + chained mutation):
  - Collaborator CRUD: seed bot -> add user -> list shows user -> remove
    -> list empty
  - Lock lifecycle: seed bot -> acquire -> info shows held -> release
    -> info shows no lock

The 2 exempt paths (AgentPass admin sync, cross-process lock concurrency)
are NOT exercised - see findings/bot_collaborator-passport-and-concurrency.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community._flows.bot_collaborator.api_lifecycle import BOT_COLLABORATOR_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow


def _seed_service_bot(world, *, bot_id: str, owner_id: str) -> None:
    """Insert an ac_bots row with bot_type='service' so collaborator
    operations pass validation. Raw SQL bypasses Python-side defaults so
    all NOT NULL columns are populated explicitly.

    Schema: src/agentclaw/community/plugin_api/models.py:26-55 (BotModel).
    The collaborator_service requires bot.bot_type == 'service' (see
    collaborator_service.py:197) - otherwise BotNotServiceTypeError.
    """
    plugin = world.get(DatabasePlugin)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with plugin.session() as s:
        s.execute(text("""
            INSERT INTO ac_bots
              (bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id,
               owner_id, engine_types, active_engine, status, gmt_create,
               gmt_modified, is_delete, public, env, bot_type)
            VALUES (:bot_id, :bot_name, :bot_desc, :entity_id, :entity_type,
                    :creator_id, :owner_id, :engine_types, :active_engine,
                    :status, :gmt_create, :gmt_modified, :is_delete,
                    :public, :env, :bot_type)
        """), {
            "bot_id": bot_id,
            "bot_name": f"E2E Collab Bot {bot_id}",
            "bot_desc": "E2E seeded service bot",
            "entity_id": owner_id,
            "entity_type": "staff",
            "creator_id": owner_id,
            "owner_id": owner_id,
            "engine_types": json.dumps(["openclaw"]),
            "active_engine": "openclaw",
            "status": "ACTIVE",
            "gmt_create": now,
            "gmt_modified": now,
            "is_delete": 0,
            "public": "0",
            "env": "dev",
            "bot_type": "service",
        })
        s.commit()


@pytest.mark.e2e
@pytest.mark.parametrize("case", BOT_COLLABORATOR_LIFECYCLE_FLOWS, ids=lambda c: c.name)
def test_bot_collaborator_flow(case, app_with_testing_modules, world):
    """3 read-only flows: list-no-bot / check-permission-no-bot /
    lock-info-not-held."""
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None


@pytest.mark.e2e
def test_bot_collaborator_add_list_remove_roundtrip(app_with_testing_modules, world):
    """Seed a service bot, add collaborator, list shows them, remove,
    list empty.

    Exercises CRUD against real DB. Remove request body uses the
    collaborator record `id` from the add response (per
    RemoveCollaboratorRequest schema: just {id: int}).
    """
    bot_id = "bot_e2e_collab_crud"
    owner_id = "e2e_user"
    collab_user = "collab_user_001"

    _seed_service_bot(world, bot_id=bot_id, owner_id=owner_id)

    client = TestClient(app_with_testing_modules, headers={"x-user-id": owner_id})

    # 1) Add collaborator - capture the record id for remove later.
    r = client.post("/api/bot/collaborator/add", json={
        "bot_id": bot_id,
        "owner_id": owner_id,
        "user_id": collab_user,
        "user_name": "Collab User",
        "role": "member",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert isinstance(body.get("data"), dict), body
    collab_record_id = body["data"].get("id")
    assert collab_record_id is not None, f"add response missing data.id: {body}"

    # 2) List shows the collaborator.
    r = client.get(
        f"/api/bot/collaborator/list?bot_id={bot_id}&owner_id={owner_id}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True, body
    assert collab_user in json.dumps(body), \
        f"list does not contain {collab_user}: {body}"

    # 3) Remove - use the record id (RemoveCollaboratorRequest is {id}).
    r = client.post(
        "/api/bot/collaborator/remove", json={"id": collab_record_id}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body

    # 4) List no longer contains the collaborator.
    r = client.get(
        f"/api/bot/collaborator/list?bot_id={bot_id}&owner_id={owner_id}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert collab_user not in json.dumps(body), body


@pytest.mark.e2e
def test_bot_collaborator_lock_lifecycle(app_with_testing_modules, world):
    """Seed bot + collaborator, acquire lock, info shows held, release, info shows not held.

    Single-process reentry path; cross-process concurrency exempt per finding.
    Lock request bodies per schemas.py:
      - AcquireLockRequest: {bot_id, owner_id} (user_id from auth)
      - ReleaseLockRequest: {bot_id, owner_id, force?}
      - GetLockInfoRequest: {bot_id, owner_id}

    Gotcha (collaborator_lock_service.py:285-291): get_lock_info short-circuits
    to lock=None when the bot has no collaborators. The lock record itself
    persists fine — info just doesn't surface it. So the lifecycle must seed
    a collaborator first, otherwise both "held" and "released" info calls
    return locked=False and the test can't tell them apart.
    """
    bot_id = "bot_e2e_collab_lock"
    owner_id = "e2e_user"

    _seed_service_bot(world, bot_id=bot_id, owner_id=owner_id)

    client = TestClient(app_with_testing_modules, headers={"x-user-id": owner_id})

    # 0) Add a collaborator so lock/info reports the actual lock state.
    r = client.post("/api/bot/collaborator/add", json={
        "bot_id": bot_id,
        "owner_id": owner_id,
        "user_id": "collab_lock_observer",
        "user_name": "Lock Observer",
        "role": "member",
    })
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True, r.text

    # 1) Acquire - {acquired: True, lock: {...}}
    r = client.post("/api/bot/collaborator/lock/acquire", json={
        "bot_id": bot_id,
        "owner_id": owner_id,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert body["data"]["acquired"] is True, body

    # 2) Info shows lock held by owner.
    r = client.get(
        f"/api/bot/collaborator/lock/info?bot_id={bot_id}&owner_id={owner_id}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["locked"] is True, body
    assert body["data"]["lock"]["holder_user_id"] == owner_id, body

    # 3) Release - {released: True}
    r = client.post("/api/bot/collaborator/lock/release", json={
        "bot_id": bot_id,
        "owner_id": owner_id,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["released"] is True, body

    # 4) Info shows no lock - {locked: False, lock: None}
    r = client.get(
        f"/api/bot/collaborator/lock/info?bot_id={bot_id}&owner_id={owner_id}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["locked"] is False, body
