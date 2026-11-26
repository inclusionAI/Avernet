"""Endpoint coverage for dormant-bot ops routes.

Every case is decided by real bot state in the per-test database: a bot's
``status`` and ``bot_type`` are exactly what ``DormantOpsService`` and
``ActivateBotService`` check, so the happy and rejected paths here are the
ones an operator would hit. The single exception is the passport failure,
which belongs to AgentPass — driven through the passport plugin's DI seam,
the boundary the ops service calls.
"""
from __future__ import annotations

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
    """A PENDING bot — neither active nor eligible for reactivation."""
    _seed_bot(world, status="PENDING")


def _assert_sync_recycle_has_no_queue(response, world):
    from agentclaw.community.core.repository.protocols.bot.recycle import BotRecycleRepositoryProtocol
    from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
    from agentclaw.community.plugin_api.database import DatabasePlugin

    data = response.json()["data"]
    assert data["operation_id"] is None and data["phase"] is None
    bot = world.get(BotRecycleRepositoryProtocol).read(_BOT_ID, _OWNER_ID)
    assert bot["status"] == "RECYCLED" and bot["binding_id"] is None
    with world.get(DatabasePlugin).orm_session() as session:
        assert session.query(TaskQueueModel).filter(TaskQueueModel.task_type == "bot.recycle").count() == 0


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
    expect=ExpectSuccess(status=200, json_contains={"ok": True, "data": {
        "status": "recycled", "operation_id": None, "phase": None}}),
    extra_assertions=(_assert_sync_recycle_has_no_queue,),
)
def recycle_one_ok():
    """A non-fastdisk Bot completes synchronously without a queue or handle."""
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
    expect=ExpectError(
        status=400,
        json_contains={
            "detail": "Bot 必须处于回收状态才能激活，当前状态: PENDING"
        },
    ),
)
def activate_one_err():
    """Error path: invalid activation state is surfaced as HTTP 400."""
    pass


def _seed_recycle_operation(world):
    from agentclaw.community.core.repository.protocols.bot.recycle import BotRecycleRepositoryProtocol
    from agentclaw.community.di.config import TaskQueueConfig
    from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
    from agentclaw.community.utils.env_utils import get_current_env

    _seed_recycle_ok(world)
    store = world.get(BotRecycleRepositoryProtocol)
    bot = store.read(_BOT_ID, _OWNER_ID)
    store.ensure({
        "bot_id": _BOT_ID, "owner_id": _OWNER_ID, "bot_pk": bot["id"],
        "operation_id": "ops-query", "app": world.get(TaskQueueConfig).app,
        "tenant": get_current_avernet_tenant(), "env": get_current_env(),
        "engine": bot["active_engine"], "source": "manual_ops",
        "target": {"binding_id": None, "provider": None, "device_id": None},
    }, 1)


@endpoint_test(
    method="GET",
    path="/api/internal/dormant/bots/{bot_id}/recycle-operations/{operation_id}",
    scenario="ok",
    input=CaseInput(headers=_AUTH_HEADERS,
                    path_params={"bot_id": _BOT_ID, "operation_id": "ops-query"},
                    query_params={"owner_id": _OWNER_ID}),
    seed=_seed_recycle_operation,
    expect=ExpectSuccess(status=200, json_contains={"ok": True, "data": {
        "bot_id": _BOT_ID, "operation_id": "ops-query", "phase": "QUEUED"}}),
)
def recycle_operation_ok():
    """The real shared repository supplies the read-only operation projection."""


@endpoint_test(
    method="GET",
    path="/api/internal/dormant/bots/{bot_id}/recycle-operations/{operation_id}",
    scenario="not_found",
    input=CaseInput(headers=_AUTH_HEADERS,
                    path_params={"bot_id": _BOT_ID, "operation_id": "old"},
                    query_params={"owner_id": _OWNER_ID}),
    seed=_seed_recycle_operation,
    expect=ExpectError(status=404, json_contains={"detail": "Recycle operation not found"}),
)
def recycle_operation_not_found():
    """Replaced or mismatched handles do not expose another operation."""


@endpoint_test(
    method="POST",
    path="/api/internal/dormant/recycle-one",
    scenario="already_queued",
    input=CaseInput(
        headers=_AUTH_HEADERS,
        json_body={"bot_id": _BOT_ID, "owner_id": _OWNER_ID, "dry_run": False},
    ),
    seed=_seed_recycle_operation,
    expect=ExpectSuccess(status=202, json_contains={"ok": True, "data": {
        "operation_id": "ops-query", "phase": "QUEUED", "changed": False}}),
)
def recycle_one_already_queued():
    """Existing async admission wins over the synchronous fallback on retries."""
