"""Endpoint tests for cron noauth routes.

Covers:
- POST /api/public/cron/auto-initiate/run (happy + error)
- POST /api/public/cron/auto-initiate/run-single (happy + error)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol


def _seed_run_ok(world):
    svc = MagicMock()
    svc.find_auto_initiate_and_run = AsyncMock(return_value={
        "success": True,
        "data": {"job_id": "auto-1"},
    })
    world.injector.binder.bind(CronRelayServiceProtocol, to=svc, scope=None)


def _seed_run_error(world):
    svc = MagicMock()
    svc.find_auto_initiate_and_run = AsyncMock(
        side_effect=ValueError("No autoInitiate cron job found")
    )
    world.injector.binder.bind(CronRelayServiceProtocol, to=svc, scope=None)


def _seed_run_single_ok(world):
    svc = MagicMock()
    svc.run_single_auto_initiate = AsyncMock(return_value={
        "success": True,
        "data": {"total": 1, "created": 1, "errors": []},
    })
    world.injector.binder.bind(CronRelayServiceProtocol, to=svc, scope=None)


def _seed_run_single_error(world):
    svc = MagicMock()
    svc.run_single_auto_initiate = AsyncMock(
        side_effect=ValueError("Bot bot-1 has no device binding")
    )
    world.injector.binder.bind(CronRelayServiceProtocol, to=svc, scope=None)


# ---- POST /api/public/cron/auto-initiate/run ----

@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run",
    scenario="ok",
    input=CaseInput(query_params={"bot_id": "bot-1", "user_id": "user-1"}),
    seed=_seed_run_ok,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def run_auto_initiate_ok():
    """Happy path: trigger autoInitiate."""


@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run",
    scenario="err",
    input=CaseInput(query_params={"bot_id": "bot-1", "user_id": "user-1"}),
    seed=_seed_run_error,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 400}),
)
def run_auto_initiate_err():
    """Error path: ValueError → 400."""


# ---- POST /api/public/cron/auto-initiate/run-single ----

@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run-single",
    scenario="ok",
    input=CaseInput(query_params={
        "bot_id": "bot-1",
        "user_id": "user-1",
        "dima_url": "https://project.teamclaw.com/space/W1/requirement?openWorkItemId=123",
    }),
    seed=_seed_run_single_ok,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def run_single_auto_initiate_ok():
    """Happy path: single requirement session."""


@endpoint_test(
    method="POST",
    path="/api/public/cron/auto-initiate/run-single",
    scenario="err",
    input=CaseInput(query_params={
        "bot_id": "bot-1",
        "user_id": "user-1",
        "dima_url": "https://project.teamclaw.com/space/W1/requirement?openWorkItemId=123",
    }),
    seed=_seed_run_single_error,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 400}),
)
def run_single_auto_initiate_err():
    """Error path: ValueError → 400."""
