"""Endpoint coverage for dormant-bot ops routes."""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService, InvalidBotStateError
from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_AUTH_HEADERS = {"Authorization": "Bearer singlebox-dormant-token-local"}


def _seed_recycle_ok(world) -> None:
    svc = MagicMock(spec=DormantOpsService)
    svc.recycle_one.return_value = {
        "run_id": "manual-recycle-test",
        "bot_id": "bot-ops",
        "owner_id": "owner-ops",
        "dry_run": False,
        "status": "recycled",
    }
    world.injector.binder.bind(DormantOpsService, to=svc, scope=None)


def _seed_recycle_error(world) -> None:
    svc = MagicMock(spec=DormantOpsService)
    svc.recycle_one.side_effect = ValueError("only ACTIVE bot can be manually recycled")
    world.injector.binder.bind(DormantOpsService, to=svc, scope=None)


def _seed_unfreeze_passport_ok(world) -> None:
    svc = MagicMock(spec=DormantOpsService)
    svc.unfreeze_passport_one.return_value = {
        "bot_id": "default",
        "owner_id": "37565",
        "status": "passport_online",
    }
    world.injector.binder.bind(DormantOpsService, to=svc, scope=None)


def _seed_unfreeze_passport_error(world) -> None:
    svc = MagicMock(spec=DormantOpsService)
    svc.unfreeze_passport_one.side_effect = RuntimeError("passport unavailable")
    world.injector.binder.bind(DormantOpsService, to=svc, scope=None)


def _seed_activate_ok(world) -> None:
    svc = MagicMock(spec=ActivateBotService)
    svc.activate.return_value = {"status": "REACTIVATING", "message": "激活中"}
    world.injector.binder.bind(ActivateBotService, to=svc, scope=None)


def _seed_activate_error(world) -> None:
    svc = MagicMock(spec=ActivateBotService)
    svc.activate.side_effect = InvalidBotStateError("仅回收状态的 Bot 可激活")
    world.injector.binder.bind(ActivateBotService, to=svc, scope=None)


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/recycle-one",
    scenario="ok",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={
            "bot_id": "bot-ops",
            "owner_id": "owner-ops",
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
        json_body={"bot_id": "bot-ops", "owner_id": "owner-ops"},
    ),
    seed=_seed_recycle_error,
    expect=ExpectError(
        status=400,
        json_contains={"detail": "only ACTIVE bot can be manually recycled"},
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
            "bot_id": "default",
            "owner_id": "37565",
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
            "bot_id": "default",
            "owner_id": "37565",
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
            "bot_id": "bot-ops",
            "owner_id": "owner-ops",
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
        json_body={"bot_id": "bot-ops", "owner_id": "owner-ops"},
    ),
    seed=_seed_activate_error,
    expect=ExpectError(status=400, json_contains={"detail": "仅回收状态的 Bot 可激活"}),
)
def activate_one_err():
    """Error path: invalid activation state is surfaced as HTTP 400."""
    pass
