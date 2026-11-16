
"""Endpoint cases for the service-Bot unified evaluation API.

These are contract-level cases: they exercise the real router, the real
ServiceBotEvalService, and the local/community evaluation plugin. No upstream
SecurityAC call is attempted in the community test profile, which keeps the 6
Phase-1 endpoints inside hermetic per-test SQLite containers.
"""

from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER = "u_eval"
_BOT_ID = "service_eval_bot"
_VERSION = "1"
_HEADERS = {"x-user-id": _OWNER}


def _seed_service_bot(world) -> None:
    """Seed the owner, a service Bot, and its draft publication record."""
    make_staff_user(world, user_id=_OWNER)
    bot = world.get(BotRepository).insert(
        {
            "bot_id": _BOT_ID,
            "bot_name": "Evaluation Bot",
            "bot_desc": "Evaluation endpoint smoke bot",
            "owner_id": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "active_engine": "openclaw",
        }
    )
    world.get(BotPublishRepositoryProtocol).insert(
        {
            "source_bot_pk": bot["id"],
            "source_bot_id": _BOT_ID,
            "publish_bot_id": _BOT_ID,
            "name": "Evaluation Bot",
            "owner_id": _OWNER,
            "permission_owner": _OWNER,
            "status": PublishStatus.DRAFT,
            "version": int(_VERSION),
            "env": "dev",
        }
    )


def _seed_only_owner(world) -> None:
    """Seed a caller for the not-found cases while omitting the target Bot."""
    make_staff_user(world, user_id=_OWNER)


@endpoint_test(
    method="GET",
    path="/api/service-bot/eval/submissions/{submission_id}",
    scenario="happy",
    input=CaseInput(
        path_params={"submission_id": "submission-001"},
        query_params={"bot_id": _BOT_ID, "owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"submission_id": "submission-001"},
        },
    ),
)
def get_submission_happy() -> None:
    """Querying a submission for a valid service Bot returns the routed context."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/eval/submissions/{submission_id}",
    scenario="bot_not_found",
    input=CaseInput(
        path_params={"submission_id": "submission-001"},
        query_params={"bot_id": _BOT_ID, "owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_only_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def get_submission_bot_not_found() -> None:
    """Querying a submission for a missing Bot returns a 404 API envelope."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/detail",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "owner_permission": {"operable": True},
                "tabs": {
                    "simple_cert_submit": {"visible": False},
                    "restart_eval": {"visible": False},
                    "eval_history": {"visible": True},
                },
            },
        },
    ),
)
def detail_happy() -> None:
    """Querying evaluation detail returns the complete tab-visibility contract."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/detail",
    scenario="bot_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_only_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def detail_bot_not_found() -> None:
    """Querying evaluation detail for a missing Bot returns a 404 API envelope."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/simple-cert/precheck",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"gate_status": "SKIPPED", "allowed": True},
        },
    ),
)
def precheck_happy() -> None:
    """Prechecking a draft service Bot routes through the community no-op gate."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/simple-cert/precheck",
    scenario="bot_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_only_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def precheck_bot_not_found() -> None:
    """Prechecking a missing Bot returns the 404 API envelope."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/simple-cert/submissions",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
        form_data={"trigger_source": "manual_simple_cert"},
        files=[
            ("sample_files", ("samples.jsonl", b'{"question":"hello"}\n')),
            ("report_files", ("report.json", b'{"result":"PASS"}')),
        ],
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def submit_happy() -> None:
    """Submitting paired sample/report files routes through the evaluation plugin."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/simple-cert/submissions",
    scenario="bot_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
        form_data={"trigger_source": "manual_simple_cert"},
        files=[
            ("sample_files", ("samples.jsonl", b'{"question":"hello"}\n')),
            ("report_files", ("report.json", b'{"result":"PASS"}')),
        ],
    ),
    seed=_seed_only_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def submit_bot_not_found() -> None:
    """Submitting evaluation data for a missing Bot returns the 404 API envelope."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/history",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER, "page_no": 1, "page_size": 10},
        headers=_HEADERS,
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"records": [], "page": {"page_no": 1, "page_size": 10, "total": 0}},
        },
    ),
)
def history_happy() -> None:
    """Querying evaluation history returns the community plugin's empty history page."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/history",
    scenario="bot_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER, "page_no": 1, "page_size": 10},
        headers=_HEADERS,
    ),
    seed=_seed_only_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def history_bot_not_found() -> None:
    """Querying evaluation history for a missing Bot returns the 404 API envelope."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/simple-cert/sync",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"gate_status": "SKIPPED", "allowed": True},
        },
    ),
)
def sync_happy() -> None:
    """Synchronizing certification state returns the community no-op result."""


@endpoint_test(
    method="POST",
    path="/api/service-bot/eval/{bot_id}/versions/{bot_version}/simple-cert/sync",
    scenario="bot_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
        query_params={"owner_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_only_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def sync_bot_not_found() -> None:
    """Synchronizing certification state for a missing Bot returns the 404 API envelope."""
