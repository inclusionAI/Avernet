"""Endpoint-framework coverage for the public harness operations (#1129).

Six routes under ``/openapi/v1/bots/{bot_id}/harness/…``, exercised through
the assembled public app: the real gateway-principal verification, the
owner/collaborator guard (``HarnessBotAccessDep``), the SQLite-backed harness
repositories, and the public error envelope.

A case mints its own gateway principal the way the skills and startup-script
groups already do; a **user** principal is enough because
``require_harness_bot_access`` explicitly checks owner-or-collaborator rather
than an application grant.

The PatchEngine is the one boundary no input can drive: preview/apply/
rollback would otherwise reach the bot's live config files. Those cases
substitute the three engine methods through ``bind_overrides`` — a subclass
of the wired engine on the per-test injector — while the repositories and the
router run for real. ``diagnose`` needs no stand-in: with no synced config
files the spawned scan fails its own empty-input guard, exactly as the
internal ``/api/harness/diagnose`` coverage shows.
"""

from __future__ import annotations

import json
import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.patch_engine_service import PatchEngineProtocol
from agentclaw.community.core.harness.models import (
    FindingsReport,
    Layer,
    PatchDefinition,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.harness import (
    HarnessPatchRepository,
    HarnessScanRecordRepository,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.di_seams import bind_overrides


_OWNER = "harness-owner"
_BOT_ID = "harness-bot"
_KEY = "harness-framework-signing-key-at-least-32-bytes"
_ENTITY_TYPE = "staff"
# The per-test SQLite database is fresh for every case, so the first row in
# ac_harness_patch is always id 1 — the same convention the skills cases use
# for their first seeded Skill.
_PATCH_ID = 1
_PATCH_CONTENT = json.dumps(
    [
        {
            "op": "update_md",
            "target": "BOT.md",
            "detail": {"dst_md_content": "new-content"},
        }
    ]
)


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    """A gateway-signed principal naming the bot's owner and no application."""
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": _OWNER, "username": "harness@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_BODY_ENTITY = {"entity_type": _ENTITY_TYPE, "entity_id": _OWNER}


def _seed_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.get(BotRepository).insert(
        {
            "bot_id": _BOT_ID,
            "bot_name": "Harness Bot",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "entity_id": _OWNER,
            "entity_type": _ENTITY_TYPE,
            "creator_id": _OWNER,
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
    )


def _seed_no_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _insert_patch(world) -> None:
    world.get(HarnessPatchRepository).create(
        PatchDefinition(
            template_id=0,
            name="harness-coverage-patch",
            layer=Layer.L1,
            content=_PATCH_CONTENT,
        )
    )


async def _preview_ok(_self, **_kwargs):
    """One rendered operation per input operation, content after apply last."""
    return "BOT.md", [("update_md", "BOT.md", "diff-text", "new-content")]


async def _apply_ok(_self, *, record, **_kwargs):
    """Applying is a no-op at the boundary; the record flows back unchanged."""
    return record


async def _rollback_ok(_self, **_kwargs):
    """A successful rollback reports (True, message) to the router."""
    return True, "rolled back"


def _seed_patch_with_engine(world) -> None:
    """A bot, one stored patch, and the engine boundary stood in for."""
    _seed_bot(world)
    _insert_patch(world)
    bind_overrides(
        world,
        PatchEngineProtocol,
        {
            "preview": _preview_ok,
            "apply": _apply_ok,
            "rollback_by_patch": _rollback_ok,
        },
    )


def _seed_scan_record(world) -> None:
    """One completed full/L1 scan record for the dim-report and dim-history reads."""
    _seed_bot(world)
    world.get(HarnessScanRecordRepository).create(
        FindingsReport(
            bot_id=_BOT_ID,
            entity_id=_OWNER,
            scan_type="full",
            layer=Layer.L1,
            health_score=90,
            score_grade="excellent",
            status="completed",
        )
    )


# ── POST /diagnose ──────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/diagnose",
    scenario="starts_a_scan_for_the_owner",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_BODY_ENTITY,
    ),
    seed=_seed_bot,
    drain_background=True,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"bot_id": _BOT_ID, "entity_id": _OWNER, "status": "scanning"},
        },
    ),
)
def diagnose_starts_a_scan():
    """The record insert and the 200 envelope precede the background scan."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/diagnose",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_BODY_ENTITY,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not Found", "data": None},
    ),
)
def diagnose_unknown_bot_is_indistinguishable():
    """The access guard answers 404 before any scan record is created."""


# ── POST /preview ────────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/preview",
    scenario="renders_the_stored_patch",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={**_BODY_ENTITY, "patch_id_list": [_PATCH_ID]},
    ),
    seed=_seed_patch_with_engine,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "risk_level": "low",
                "final_content": "new-content",
            },
        },
    ),
)
def preview_renders_the_stored_patch():
    """The repository row drives the operations; the engine only renders them."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/preview",
    scenario="missing_patch",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={**_BODY_ENTITY, "patch_id_list": [999]},
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not Found", "data": None},
    ),
)
def preview_missing_patch_is_404():
    """A patch id that names no row is a 404, not a silent skip."""


# ── POST /apply ──────────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/apply",
    scenario="applies_the_stored_patch",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={**_BODY_ENTITY, "patch_id_list": [_PATCH_ID]},
    ),
    seed=_seed_patch_with_engine,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"success": True}},
    ),
)
def apply_marks_the_patch_applied():
    """With no prior record the handler plans one, applies, and flags the patch."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/apply",
    scenario="nothing_to_apply",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_BODY_ENTITY,
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=400,
        json_contains={"code": 400000, "message": "Bad Request", "data": None},
    ),
)
def apply_without_record_or_patches_is_400():
    """The request must name a record_id or at least one patch id."""


# ── POST /rollback ───────────────────────────────────────────────────────────


# ``rollback`` in the case id is filtered out of the parametrized runner
# (test_endpoint_runner.py) — the internal rollback endpoints live on the
# coverage baseline for that reason. These scenarios avoid the substring so
# the public surface gets real coverage rather than a baseline line.
@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/rollback",
    scenario="restores_the_stored_patch",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={**_BODY_ENTITY, "patch_id": _PATCH_ID},
    ),
    seed=_seed_patch_with_engine,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"success": True}},
    ),
)
def rollback_clears_the_applied_flag():
    """A successful engine rollback un-marks the patch in the repository."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/harness/rollback",
    scenario="patch_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={**_BODY_ENTITY, "patch_id": 999},
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not Found", "data": None},
    ),
)
def rollback_missing_patch_is_404():
    """Rolling back a patch that does not exist is a 404."""


# ── GET /dim-report ──────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/harness/dim-report",
    scenario="returns_the_latest_scan_per_dimension",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={**_QUERY, "entity_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_scan_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "entity_id": _OWNER,
                "items": [
                    {"scan_dim": "full:L1", "grade": "excellent", "status": "completed"}
                ],
            },
        },
    ),
)
def dim_report_returns_the_seeded_scan():
    """The report reads back the scan record the repository holds."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/harness/dim-report",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"},
        query_params={**_QUERY, "entity_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not Found", "data": None},
    ),
)
def dim_report_unknown_bot_is_indistinguishable():
    """Reporting on a bot that is not the caller's is a 404."""


# ── GET /dim-history ─────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/harness/dim-history",
    scenario="returns_the_scan_history_page",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={**_QUERY, "entity_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_scan_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "bot_id": _BOT_ID,
                "total": 1,
                "items": [{"scan_dim": "full:L1", "status": "completed"}],
            },
        },
    ),
)
def dim_history_returns_the_seeded_scan():
    """The history page totals and lists the seeded scan record."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/harness/dim-history",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"},
        query_params={**_QUERY, "entity_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not Found", "data": None},
    ),
)
def dim_history_unknown_bot_is_indistinguishable():
    """History on a bot that is not the caller's is a 404."""
