"""Cron query endpoints reject malformed ``bot_id`` values before resolving a Bot.

The CDO incident this covers: an unauthenticated probe sent injection-looking
strings as ``bot_id`` to ``GET /api/cron``; the value flowed into the Bot lookup,
the resulting ``Bot not found`` error was logged at ERROR, and every probe
became an online-error alert. These cases pin the promised behaviors of the
cron router itself:

* a malformed ``bot_id`` answers 400 without consulting the cron relay and is
  not echoed by the router's own logs;
* a well-formed but unknown ``bot_id`` answers 404 (not 500) at WARNING.

The collaborator interceptor still probes the Bot repository before the handler
runs (it logs that miss at WARNING); that pre-existing path is unchanged here.
"""
from __future__ import annotations

import logging

import pytest

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, endpoint_test


_SPEL_PROBE = "${T(java.lang.Runtime).getRuntime().exec('nslookup probe.example')}"
_SEMICOLON_PROBE = "all;nslookup probe.example"
_UNKNOWN_BOT = "20990101_unknownbot"


def _seed_user(world):
    make_staff_user(world, user_id="u001")


# ============================================================================
# GET /api/cron
# ============================================================================

@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="list_rejects_spel_probe_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        query_params={"bot_id": _SPEL_PROBE},
    ),
    seed=_seed_user,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def list_crons_rejects_spel_probe():
    """Spring-EL-looking probe payload is refused with 400."""


@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="list_rejects_semicolon_probe_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        query_params={"bot_id": _SEMICOLON_PROBE},
    ),
    seed=_seed_user,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def list_crons_rejects_semicolon_probe():
    """Semicolon-joined probe payload is refused with 400."""


@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="list_unknown_well_formed_bot_id_is_404",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        query_params={"bot_id": _UNKNOWN_BOT},
    ),
    seed=_seed_user,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def list_crons_unknown_bot_is_not_500():
    """A well-formed id for an unknown Bot answers 404, not 500."""


# ============================================================================
# GET /api/cron/{task_id}/runs
# ============================================================================

@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="runs_rejects_spel_probe_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        path_params={"task_id": "task_1"},
        query_params={"bot_id": _SPEL_PROBE},
    ),
    seed=_seed_user,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def cron_runs_rejects_spel_probe():
    """Run history refuses a malformed bot_id with 400."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="runs_unknown_well_formed_bot_id_is_404",
    input=CaseInput(
        headers={"x-user-id": "u001"},
        path_params={"task_id": "task_1"},
        query_params={"bot_id": _UNKNOWN_BOT},
    ),
    seed=_seed_user,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def cron_runs_unknown_bot_is_not_500():
    """Run history for an unknown Bot answers 404, not 500."""


# ============================================================================
# Handler-level checks (service not reached / log level)
# ============================================================================

def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="u001",
        staffId="u001",
        nickName="Test User",
        operatorName="Test User",
    )


class _UnreachableService:
    async def list_all_crons(self, **kwargs):
        raise AssertionError("cron service must not be called for a rejected bot_id")


class _UnreachableBotService:
    def get_bot(self, bot_id, user_id):
        raise AssertionError("bot lookup must not run for a rejected bot_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_id", [_SPEL_PROBE, _SEMICOLON_PROBE, "", "has space"])
async def test_list_crons_rejects_malformed_bot_id_without_calling_service(bot_id, caplog):
    from agentclaw.community.adapters.http.cron.router import list_crons

    with caplog.at_level(logging.WARNING, logger="start"):
        response = await list_crons(
            bot_id=bot_id,
            owner_id="u001",
            runtime_stage=None,
            device_uuid=None,
            operator_id=None,
            user=_user(),
            service=_UnreachableService(),
            bot_service=_UnreachableBotService(),
        )

    assert response.success is False
    assert response.error_code == 400
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [r for r in caplog.records if bot_id and bot_id in r.getMessage()]


@pytest.mark.asyncio
async def test_get_cron_runs_rejects_malformed_bot_id_without_calling_service(caplog):
    from agentclaw.community.adapters.http.cron.router import get_cron_runs

    with caplog.at_level(logging.WARNING, logger="start"):
        response = await get_cron_runs(
            task_id="task_1",
            bot_id=_SEMICOLON_PROBE,
            limit=20,
            owner_id="u001",
            runtime_stage=None,
            device_uuid=None,
            operator_id=None,
            user=_user(),
            service=_UnreachableService(),
            bot_service=_UnreachableBotService(),
        )

    assert response.success is False
    assert response.error_code == 400
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [r for r in caplog.records if _SEMICOLON_PROBE in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_id", ["all", "20261117_abc12345", "default"])
async def test_is_valid_bot_id_accepts_sentinel_and_real_shapes(bot_id):
    from agentclaw.community.adapters.http.cron.router import _is_valid_bot_id

    assert _is_valid_bot_id(bot_id)


class _NotFoundService:
    async def list_all_crons(self, **kwargs):
        raise BotNotFoundError(f"Bot not found: {kwargs.get('bot_id')}")


class _NotFoundBotService:
    def get_bot(self, bot_id, user_id):
        raise BotNotFoundError(f"Bot not found: {bot_id}")


@pytest.mark.asyncio
async def test_list_crons_unknown_bot_logs_warning_and_returns_404(caplog):
    from agentclaw.community.adapters.http.cron.router import list_crons

    with caplog.at_level(logging.WARNING, logger="start"):
        response = await list_crons(
            bot_id=_UNKNOWN_BOT,
            owner_id="u001",
            runtime_stage=None,
            device_uuid=None,
            operator_id=None,
            user=_user(),
            service=_NotFoundService(),
            bot_service=_NotFoundBotService(),
        )

    assert response.success is False
    assert response.error_code == 404
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
