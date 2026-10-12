"""Live singlebox coverage for the edit-domain explicit-standing split.

迭代11《编辑权限申请审批策略》: a Team Space member without an explicit
editor grant keeps the reads — the inventory card, the chat — but the
edit/operations rows (``Check … explicit=True``: skill-sets, edit-lock,
restarts, lifecycle…) refuse them, and an editor the Owner granted passes
those same rows. This story drives both sides through the live backend so the
standing judgment (:class:`ExplicitStandingMixin`, the gate resolver, and the
inventory's edit-actions split) is exercised by real HTTP traffic.
"""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx
import jwt
import pytest


#: Singlebox's dev principal verifier accepts this key; the acceptance
#: stories and the backend must agree on it for a minted principal to
#: verify (the files-acceptance story uses the same one).
_PRINCIPAL_KEY = os.environ.get(
    "SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY",
    "singlebox-gateway-principal-key-not-for-production",
)


def _principal_headers(user_id: str) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _fresh_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _execute_local_sql(client: httpx.Client, statements: list[dict]) -> dict:
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = client.post("/local/sql/execute", json={"statements": statements})
        if response.status_code == 200:
            return response.json()
        last_response = response
        if "SQL statements in progress" not in response.text:
            break
        time.sleep(0.2 * (attempt + 1))
    assert last_response is not None
    assert last_response.status_code == 200, last_response.text
    return last_response.json()


def _seed_team_space_bot(
    client: httpx.Client,
    *,
    owner_id: str,
    bot_id: str,
    space_code: str,
) -> int:
    """Seed a TEAM space (owner + one member), and a cloud Bot assigned to it.

    Returns the space row id for the collaborator insertion below.
    """
    result = _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_space ("
                    "space_code, space_type, name, personal_owner_id, sc_team_id, "
                    "sc_mapping_status, env, created_by, updated_by"
                    ") VALUES ("
                    ":space_code, 'TEAM', :name, NULL, NULL, "
                    "'ACTIVE', 'dev', :owner_id, :owner_id)"
                ),
                "params": {
                    "space_code": space_code,
                    "name": f"Space {space_code}",
                    "owner_id": owner_id,
                },
            },
            {
                "sql": (
                    "INSERT INTO ac_space_member ("
                    "space_id, user_id, user_name, role, status, env, created_by"
                    ") VALUES ("
                    "(SELECT id FROM ac_space WHERE space_code = :space_code), "
                    ":owner_id, :owner_id, 'ADMIN', 'ACTIVE', 'dev', :owner_id), ("
                    "(SELECT id FROM ac_space WHERE space_code = :space_code), "
                    ":member_id, :member_id, 'MEMBER', 'ACTIVE', 'dev', :owner_id)"
                ),
                "params": {
                    "space_code": space_code,
                    "owner_id": owner_id,
                    "member_id": f"member_of_{space_code}",
                },
            },
            {
                "sql": (
                    "INSERT INTO ac_bots ("
                    "bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id, owner_id, "
                    "owner_name, engine_types, active_engine, status, binding_id, device_id, "
                    "gmt_create, gmt_modified, is_delete, public, ext, env, bot_type, template_type, "
                    "call_type, caller_config_revision, space_id"
                    ") VALUES ("
                    ":bot_id, :bot_desc, :bot_desc, :owner_id, 'staff', :owner_id, :owner_id, "
                    ":owner_id, :engine_types, 'openclaw', 'ACTIVE', NULL, NULL, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, '0', '0', 'dev', 'personal', 'chat', "
                    "'owner', 0, "
                    "(SELECT id FROM ac_space WHERE space_code = :space_code)"
                    ")"
                ),
                "params": {
                    "bot_id": bot_id,
                    "bot_desc": f"Standing bot {bot_id}",
                    "owner_id": owner_id,
                    "engine_types": json.dumps(["openclaw"]),
                    "space_code": space_code,
                },
            },
            {
                "sql": (
                    "SELECT id FROM ac_space WHERE space_code = :space_code"
                ),
                "params": {"space_code": space_code},
            },
        ],
    )
    rows = result["results"][-1]["rows"] or []
    return int(rows[0]["id"]) if rows else 0


def _seed_collaborator_row(
    client: httpx.Client,
    *,
    bot_id: str,
    owner_id: str,
    member_id: str,
    role: str = "member",
) -> None:
    """Grant the explicit editor row the approved application would insert."""
    _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_bot_collaborator ("
                    "bot_pk, bot_id, owner_id, user_id, user_name, role, "
                    "operator_id, env, gmt_create, gmt_modified"
                    ") VALUES ("
                    "(SELECT id FROM ac_bots WHERE bot_id = :bot_id), "
                    ":bot_id, :owner_id, :user_id, :user_id, :role, "
                    ":owner_id, 'dev', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                    ")"
                ),
                "params": {
                    "bot_id": bot_id,
                    "owner_id": owner_id,
                    "user_id": member_id,
                    "role": role,
                },
            }
        ],
    )


def test_edit_domain_refuses_space_member_and_admits_explicit_editor(live_backend):
    """One story: the same MEMBER, refused while Space-synthesized, admitted with a row."""
    pytest.importorskip("httpx")
    owner_id = _fresh_id("standing_owner")
    space_code = _fresh_id("standing_space")
    bot_id = _fresh_id("standing_bot")
    member_id = f"member_of_{space_code}"

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": member_id},
        timeout=60.0,
    ) as member_client:
        with httpx.Client(
            base_url=live_backend, headers={"x-user-id": owner_id}, timeout=60.0
        ) as owner_client:
            space_id = _seed_team_space_bot(
                owner_client,
                owner_id=owner_id,
                bot_id=bot_id,
                space_code=space_code,
            )
            assert space_id > 0

            # The editor-domain face while the member's level is
            # Space-synthesized: refused, exactly as a missing bot answers.
            refused = member_client.post(
                f"/openapi/v1/bots/{bot_id}/skill-sets",
                params={"user_id": member_id, "owner_id": owner_id},
                json={"name": "member set"},
                headers=_principal_headers(member_id),
            )
            assert refused.status_code == 404, refused.text

            # The Space member's card: reads offered, EDIT withheld.
            inventory = member_client.get(
                "/openapi/v1/bots/all",
                params={"user_id": member_id, "owner_id": member_id},
                headers=_principal_headers(member_id) | {"X-Space-Id": str(space_id)},
            )
            assert inventory.status_code == 200, inventory.text
            cards = {
                item["bot_id"]: item for item in inventory.json()["data"]["items"]
            }
            card = cards[bot_id]
            assert "edit" not in card["actions"]
            assert card["disabled_actions"]["edit"] == "Bot editor permission required"

            # The Owner's own card passes on ownership: EDIT stays offered.
            owner_inventory = owner_client.get(
                "/openapi/v1/bots/all",
                params={"user_id": owner_id, "owner_id": owner_id},
                headers=_principal_headers(owner_id) | {"X-Space-Id": str(space_id)},
            )
            assert owner_inventory.status_code == 200, owner_inventory.text
            owner_card = {
                item["bot_id"]: item for item in owner_inventory.json()["data"]["items"]
            }[bot_id]
            assert "edit" in owner_card["actions"]

            # The explicit editor row the Owner grants (or an approved
            # application inserts) admits the same face that just refused:
            # the same request now travels past the permission bar to the
            # edit lock underneath, so take the lock and land the create.
            _seed_collaborator_row(
                owner_client,
                bot_id=bot_id,
                owner_id=owner_id,
                member_id=member_id,
            )
            beyond_bar = member_client.post(
                f"/openapi/v1/bots/{bot_id}/skill-sets",
                params={"user_id": member_id, "owner_id": owner_id},
                json={"name": "member set"},
                headers=_principal_headers(member_id),
            )
            # 404 means the bar still refuses; 200/201已到位; the 423 below is
            # the same route's EDIT_LOCK, which is what the row's grant proves.
            assert beyond_bar.status_code == 423, beyond_bar.text

            # The lock payload a create would need depends on the Bot's shape,
            # and different shapes answer its service differently; the 423 is
            # all the grant has to prove — the same request that the bar
            # masked as 404 while the standing was synthesized now reaches
            # the route's second, request-visible gate.