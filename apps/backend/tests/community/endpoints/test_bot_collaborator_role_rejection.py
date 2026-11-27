"""Invalid collaborator role rejections answer 400 at WARNING, not 500 at ERROR.

The legacy ``/api/bot/collaborator/*`` write endpoints route
``InvalidCollaboratorRoleError`` (raised by ``CollaboratorService._normalize_role``
for any role outside ``admin``/``member``) through the generic
``except CollaboratorServiceError`` fallback, which logs at ERROR and answers
``error_code=500``. That misclassified client-input rejections — including the
AEPIS probe batch of 2026-11-27 — as server faults, feeding the CDO ONLINE_ERROR
pipeline. The openapi_v1 surface already maps the same domain error to 400
(``openapi_v1/responses.py``), and the sibling handlers here (``BotNotServiceTypeError``,
``PermissionDeniedError``, ...) answer their 4xx at WARNING.

These cases drive the real router/service stack over the per-test app and pin
both halves of the classification: the 400 envelope (AC-1) and the log level
(ERROR-free, AC-2) — legit roles keep their existing behaviour (AC-3, covered
by the endpoint_test suite beside this file).
"""
from __future__ import annotations

import logging

import pytest

from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot, make_collaborator

_INVALID_ROLE = "__aepis_bad_role__"


def _seed_bot_owner_with_service_bot(world):
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_collab")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


def _seed_collaborator(world):
    _seed_bot_owner_with_service_bot(world)
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        role="member",
        operator_id="u_owner",
    )


def _assert_rejected_at_warning(response, caplog, tag):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == 400, f"Expected 400 classification, got {body.get('error_code')}"
    assert "unsupported collaborator role" in body["message"]
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and f"[{tag}] Invalid collaborator role" in r.getMessage()
    ]
    assert warnings, f"Expected a WARNING record from [{tag}], saw: {[r.getMessage() for r in caplog.records]}"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"Invalid role must not reach ERROR logs, saw: {[r.getMessage() for r in errors]}"


@pytest.mark.asyncio
async def test_add_collaborator_invalid_role_answers_400_at_warning(world, async_client, caplog):
    _seed_bot_owner_with_service_bot(world)
    with caplog.at_level(logging.WARNING, logger="start"):
        response = await async_client.post(
            "/api/bot/collaborator/add",
            headers={"x-user-id": "u_owner"},
            json={
                "bot_id": "bot_test",
                "owner_id": "u_owner",
                "user_id": "u_collab",
                "role": _INVALID_ROLE,
            },
        )
    _assert_rejected_at_warning(response, caplog, "add_collaborator")


@pytest.mark.asyncio
async def test_add_collaborator_for_others_invalid_role_answers_400_at_warning(world, async_client, caplog):
    _seed_bot_owner_with_service_bot(world)
    with caplog.at_level(logging.WARNING, logger="start"):
        response = await async_client.post(
            "/api/bot/collaborator/add_for_others",
            headers={"x-user-id": "100000"},  # whitelisted operator, as in the suite's ok case
            json={
                "bot_id": "bot_test",
                "owner_id": "u_owner",
                "user_id": "u_collab",
                "role": _INVALID_ROLE,
            },
        )
    _assert_rejected_at_warning(response, caplog, "add_collaborator_for_others")


@pytest.mark.asyncio
async def test_update_collaborator_invalid_role_answers_400_at_warning(world, async_client, caplog):
    _seed_collaborator(world)
    listed = await async_client.get(
        "/api/bot/collaborator/list",
        headers={"x-user-id": "u_owner"},
        params={"bot_id": "bot_test", "owner_id": "u_owner"},
    )
    assert listed.status_code == 200, listed.text
    collaborators = listed.json()["data"]["collaborators"]
    assert collaborators, "Seed collaborator missing; cannot address an update"
    target_id = collaborators[0]["id"]

    with caplog.at_level(logging.WARNING, logger="start"):
        response = await async_client.post(
            "/api/bot/collaborator/update",
            headers={"x-user-id": "u_owner"},
            json={"id": target_id, "role": _INVALID_ROLE},
        )
    _assert_rejected_at_warning(response, caplog, "update_collaborator")

    # The rejected update must not have touched the stored role (AC-3 adjacency:
    # refusal happens before the repository write, so the record stays intact).
    after = await async_client.get(
        "/api/bot/collaborator/list",
        headers={"x-user-id": "u_owner"},
        params={"bot_id": "bot_test", "owner_id": "u_owner"},
    )
    assert after.status_code == 200, after.text
    rows = after.json()["data"]["collaborators"]
    target = next(r for r in rows if r["id"] == target_id)
    assert target["role"] == "member", f"Rejected update changed stored role: {target['role']}"
