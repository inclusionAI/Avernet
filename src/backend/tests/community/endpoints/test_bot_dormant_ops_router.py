"""Endpoint coverage for dormant-bot ops routes.

Every case is decided by real bot state in the per-test database: a bot's
``status`` and ``bot_type`` are exactly what ``DormantOpsService`` and
``ActivateBotService`` check, so the happy and rejected paths here are the
ones an operator would hit. The single exception is the passport failure,
which belongs to AgentPass — driven through the passport plugin's DI seam,
the boundary the ops service calls.
"""
from __future__ import annotations

from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService
from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.plugin_api.passport import PassportPlugin
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_AUTH_HEADERS = {"Authorization": "Bearer singlebox-dormant-token-local"}
_BOT_ID = "bot-ops"
_OWNER_ID = "owner-ops"


def _seed_bot(world, *, status: str, bot_type: str = "personal") -> None:
    make_staff_user(world, user_id=_OWNER_ID)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER_ID,
        owner_name=_OWNER_ID,
        bot_type=bot_type,
        status=status,
    )


def _seed_recycle_ok(world) -> None:
    """An ACTIVE personal bot — the only shape manual recycle accepts."""
    _seed_bot(world, status="ACTIVE")


def _seed_recycle_error(world) -> None:
    """A bot that is already RECYCLED — the guard the route exists to enforce."""
    _seed_bot(world, status="RECYCLED")


def _seed_unfreeze_passport_ok(world) -> None:
    _seed_bot(world, status="RECYCLED")


def _seed_unfreeze_passport_error(world) -> None:
    """AgentPass refuses to bring the credential online."""
    _seed_bot(world, status="RECYCLED")

    def _passport_unavailable(*_args, **_kwargs):
        raise RuntimeError("passport unavailable")

    world.get(PassportPlugin).set_override(
        "unfreeze_agent_passport", _passport_unavailable,
    )


def _seed_activate_ok(world) -> None:
    """A bot already REACTIVATING — activation is idempotent for that state."""
    _seed_bot(world, status="REACTIVATING")


def _seed_activate_error(world) -> None:
    """An ACTIVE bot — activation only applies to a recycled one."""
    _seed_bot(world, status="ACTIVE")


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/recycle-one",
    scenario="ok",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "dry_run": False,
            "reason": "endpoint coverage",
        },
    ),
    seed=_seed_recycle_ok,
    expect=ExpectSuccess(status=200, json_contains={"ok": True}),
)
def recycle_one_ok():
    """Happy path: ops recycle-one returns the manual recycle result."""
    pass


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/recycle-one",
    scenario="err",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={"bot_id": _BOT_ID, "owner_id": _OWNER_ID},
    ),
    seed=_seed_recycle_error,
    expect=ExpectError(
        status=400,
        json_contains={
            "detail": "only ACTIVE bot can be manually recycled, current: RECYCLED",
        },
    ),
)
def recycle_one_err():
    """Error path: domain validation is surfaced as HTTP 400."""
    pass


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/unfreeze-passport-one",
    scenario="ok",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "reason": "recover license",
        },
    ),
    seed=_seed_unfreeze_passport_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"ok": True, "data": {"status": "passport_online"}},
    ),
)
def unfreeze_passport_one_ok():
    """Happy path: passport-only ops returns the online status."""
    pass


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/unfreeze-passport-one",
    scenario="err",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "reason": "recover license",
        },
    ),
    seed=_seed_unfreeze_passport_error,
    expect=ExpectError(
        status=500,
        json_contains={"detail": "passport unavailable"},
    ),
)
def unfreeze_passport_one_err():
    """Error path: Passport failures are surfaced as HTTP 500."""
    pass


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/activate-one",
    scenario="ok",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={
            "bot_id": _BOT_ID,
            "owner_id": _OWNER_ID,
            "nick_name": "ops",
        },
    ),
    seed=_seed_activate_ok,
    expect=ExpectSuccess(status=200, json_contains={"ok": True}),
)
def activate_one_ok():
    """Happy path: ops activate-one reuses ActivateBotService."""
    pass


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/activate-one",
    scenario="err",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={"bot_id": _BOT_ID, "owner_id": _OWNER_ID},
    ),
    seed=_seed_activate_error,
    expect=ExpectError(status=400, json_contains={"detail": "only RECYCLED bot can be activated, current: ACTIVE"}),
)
def activate_one_err():
    """Error path: invalid activation state is surfaced as HTTP 400."""
    pass
