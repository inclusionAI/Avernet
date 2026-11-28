"""Endpoint coverage for the Admin Bot token-usage APIs."""

from __future__ import annotations

from agentclaw.community.core.repository.implementations.chat.db import (
    BotChatDbRepository,
)
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OPERATOR = "token_usage_operator"
_DAY_MS = 86_400_000
_FROM_MS = 1_700_000_000_000
_TO_MS = _FROM_MS + 3 * _DAY_MS
_SUMMARY_PATH = "/api/v1/bots/token-usage/summary"
_DAILY_PATH = "/api/v1/bots/{bot_id}/token-usage/daily"


def _seed_traces(world) -> None:
    """Seed OTEL traces through the real chat repository."""
    repo = BotChatDbRepository(world.get(DatabasePlugin))
    repo.upsert_ocb_trace(
        {
            "trace_id": "token-usage-a-1",
            "bot_id": "bot-token-a",
            "start_time_ms": _FROM_MS + 1_000,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
            "total_cost": 0.001,
        }
    )
    repo.upsert_ocb_trace(
        {
            "trace_id": "token-usage-a-2",
            "bot_id": "bot-token-a",
            "start_time_ms": _FROM_MS + _DAY_MS + 1_000,
            "usage": {
                "input_tokens": 50,
                "output_tokens": 10,
                "total_tokens": 60,
            },
            "total_cost": 0.0005,
        }
    )
    repo.upsert_ocb_trace(
        {
            "trace_id": "token-usage-b-1",
            "bot_id": "bot-token-b",
            "start_time_ms": _FROM_MS + 2_000,
            "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            "total_cost": 0.0001,
        }
    )


def _deny_operator(world) -> None:
    """Drive the real ``require_operator`` dependency through its DI seam."""
    world.get(AuthPlugin).set_response("is_operator_allowed", False)


@endpoint_test(
    method="GET",
    path=_SUMMARY_PATH,
    scenario="happy",
    input=CaseInput(
        headers={"x-user-id": _OPERATOR},
        query_params={
            "bot_ids": "bot-token-a,bot-token-b",
            "period": "custom",
            "from_ms": _FROM_MS,
            "to_ms": _TO_MS,
        },
    ),
    seed=_seed_traces,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": [
                {
                    "bot_id": "bot-token-a",
                    "total_input_tokens": 150,
                    "total_output_tokens": 30,
                    "total_tokens": 180,
                },
                {"bot_id": "bot-token-b", "total_tokens": 10},
            ],
        },
    ),
)
def token_summary_happy():
    """Aggregates each selected Bot's usage in the requested window."""


@endpoint_test(
    method="GET",
    path=_SUMMARY_PATH,
    scenario="invalid_custom_range",
    input=CaseInput(
        headers={"x-user-id": _OPERATOR},
        query_params={"period": "custom"},
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def token_summary_invalid_custom_range():
    """A custom period without a range is rejected instead of querying all time."""


@endpoint_test(
    method="GET",
    path=_SUMMARY_PATH,
    scenario="forbidden_non_operator",
    input=CaseInput(headers={"x-user-id": _OPERATOR}),
    seed=_deny_operator,
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：您没有操作员权限"},
    ),
)
def token_summary_forbidden_non_operator():
    """The Admin-only endpoint refuses callers outside the operator allowlist."""


@endpoint_test(
    method="GET",
    path=_DAILY_PATH,
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": "bot-token-a"},
        headers={"x-user-id": _OPERATOR},
        query_params={
            "period": "custom",
            "from_ms": _FROM_MS,
            "to_ms": _TO_MS,
        },
    ),
    seed=_seed_traces,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": [
                {"total_tokens": 120},
                {"total_tokens": 60},
            ],
        },
    ),
)
def token_daily_happy():
    """Returns one aggregated row per day for the selected Bot."""


@endpoint_test(
    method="GET",
    path=_DAILY_PATH,
    scenario="invalid_custom_range",
    input=CaseInput(
        path_params={"bot_id": "bot-token-a"},
        headers={"x-user-id": _OPERATOR},
        query_params={"period": "custom"},
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def token_daily_invalid_custom_range():
    """A custom period without a range is rejected instead of querying all time."""


@endpoint_test(
    method="GET",
    path=_DAILY_PATH,
    scenario="forbidden_non_operator",
    input=CaseInput(
        path_params={"bot_id": "bot-token-a"},
        headers={"x-user-id": _OPERATOR},
    ),
    seed=_deny_operator,
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：您没有操作员权限"},
    ),
)
def token_daily_forbidden_non_operator():
    """The Admin-only endpoint refuses callers outside the operator allowlist."""
