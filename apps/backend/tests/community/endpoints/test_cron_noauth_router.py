"""Endpoint tests for cron public routes.

Covers:
- POST /api/public/cron/auto-initiate/run (happy + error)
- POST /api/public/cron/auto-initiate/run-single (happy + error)

The relay itself runs for real against the in-memory adapter transport
(``InMemoryDeviceAdapterTransport``, the LOCAL ``DeviceAdapterTransport``
impl): the happy ``run`` case seeds an actual autoInitiate cron job through
the relay, so the endpoint has to resolve the device, list the jobs, pick the
one tagged ``kind:autoInitiate`` and trigger it — the whole point of the
route. Both error cases come from a bot that genuinely has no device binding,
which is what makes the relay raise ``ValueError`` and the router answer 400.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.factories.devices import make_active_local_device
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_BOT_ID = "bot-1"
_USER_ID = "user-1"
_DIMA_URL = (
    "https://project.teamclaw.com/space/W1/requirement?openWorkItemId=123"
)


def _seed_bot_with_device(world) -> None:
    """A bot whose ACTIVE local binding resolves to the in-memory adapter."""
    make_staff_user(world, user_id=_USER_ID)
    binding_id = make_active_local_device(world, owner_id=_USER_ID)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_USER_ID,
        owner_name=_USER_ID,
        bot_type="service",
        status="ACTIVE",
        binding_id=binding_id,
    )


def _seed_bot_without_device(world) -> None:
    """A bot with no device binding — the relay's own precondition failure."""
    make_staff_user(world, user_id=_USER_ID)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_USER_ID,
        owner_name=_USER_ID,
        bot_type="service",
        status="ACTIVE",
    )


def _seed_auto_initiate_job(world) -> None:
    """Register a real autoInitiate cron job on the bot's adapter.

    Created through the relay's own ``forward_request``, so the job lands in
    the adapter exactly as a user-created one would — including the
    ``|kind:autoInitiate|`` marker in its message that the endpoint under test
    scans for.
    """
    _seed_bot_with_device(world)
    relay = world.get(CronRelayServiceProtocol)
    asyncio.run(
        relay.forward_request(
            bot_id=_BOT_ID,
            user_id=_USER_ID,
            nick_name=_USER_ID,
            method="POST",
            path="/api/cron",
            body={
                "name": "auto initiate",
                "schedule": "0 9 * * *",
                "command": "daily sweep |kind:autoInitiate|",
            },
        )
    )


def _seed_engine_accepts_run_single(world) -> None:
    """Have the adapter answer the engine's run-single path.

    ``/api/cron/auto-initiate/run-single`` is served by the engine, not by the
    cron store the in-memory adapter models, so the transport seam supplies
    that one upstream reply. Everything before it — bot lookup, device status,
    workflow resolution from ``template_config``, request-body assembly — is
    the relay's real code.
    """
    _seed_bot_with_device(world)
    world.get(DeviceAdapterTransport).set_override(
        "invoke",
        lambda *_args, **_kwargs: {
            "success": True,
            "data": {"total": 1, "created": 1, "errors": []},
        },
    )


# ---- POST /api/public/cron/auto-initiate/run ----

@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run",
    scenario="ok",
    input=CaseInput(query_params={"bot_id": _BOT_ID, "user_id": _USER_ID}),
    seed=_seed_auto_initiate_job,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"ok": True, "ran": True}},
    ),
)
def run_auto_initiate_ok():
    """Happy path: the bot's autoInitiate job is found and triggered."""


@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run",
    scenario="err",
    input=CaseInput(query_params={"bot_id": _BOT_ID, "user_id": _USER_ID}),
    seed=_seed_bot_without_device,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 400,
            "message": f"Bot {_BOT_ID} has no device binding",
        },
    ),
)
def run_auto_initiate_err():
    """Error path: an unbound bot has nowhere to run — ValueError → 400."""


# ---- POST /api/public/cron/auto-initiate/run-single ----

@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run-single",
    scenario="ok",
    input=CaseInput(query_params={
        "bot_id": _BOT_ID,
        "user_id": _USER_ID,
        "dima_url": _DIMA_URL,
    }),
    seed=_seed_engine_accepts_run_single,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 1, "created": 1}},
    ),
)
def run_single_auto_initiate_ok():
    """Happy path: single requirement session."""


@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run-single",
    scenario="err",
    input=CaseInput(query_params={
        "bot_id": _BOT_ID,
        "user_id": _USER_ID,
        "dima_url": _DIMA_URL,
    }),
    seed=_seed_bot_without_device,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 400,
            "message": f"Bot {_BOT_ID} has no device binding",
        },
    ),
)
def run_single_auto_initiate_err():
    """Error path: an unbound bot has nowhere to run — ValueError → 400."""
