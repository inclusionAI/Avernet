"""Endpoint-injection coverage for the Skills Pool operator API."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from agentclaw.community.core.skills_pool.operational_query import (
    SkillsPoolOperationalQuery,
)
from agentclaw.community.core.skills_pool.operations import (
    RolloutOperationError,
    SkillsPoolRolloutOperations,
)
from agentclaw.community.core.skills_pool.operator_commands import (
    SkillsPoolOperatorCommands,
)
from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRecoveryService,
    SkillsPoolRollbackService,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_HEADERS = {"x-user-id": "skills-pool-operator"}
_SCOPE = BotSkillLayoutScope("dev", "entity-1", "bot-1")


@dataclass(frozen=True)
class _Result:
    outcome: str = "ok"
    scope: BotSkillLayoutScope | None = None
    promotion_ready: bool = True
    rollout_config_version: str = "config-1"


def _seed_happy_services(world) -> None:
    result = _Result(scope=_SCOPE)

    def get_snapshot(*_args, **_kwargs):
        return result

    def mutation(*_args, **_kwargs):
        return result

    def get_bot(*_args, **_kwargs):
        return result

    def summarize_batch(*_args, **_kwargs):
        return result

    def wake(*_args, **_kwargs):
        return result

    def resolve(*_args, **_kwargs):
        return result

    async def rollback(*_args, **_kwargs):
        return result

    patchers = [
        patch.object(
            SkillsPoolRolloutOperations,
            "get_snapshot",
            get_snapshot,
        ),
        *[
            patch.object(SkillsPoolRolloutOperations, method, mutation)
            for method in (
                "set_feature_enabled",
                "promote_engine",
                "add_bot",
                "remove_bot",
                "accept_batch",
                "set_control_bot",
            )
        ],
        patch.object(SkillsPoolOperationalQuery, "get_bot", get_bot),
        patch.object(
            SkillsPoolOperationalQuery,
            "summarize_batch",
            summarize_batch,
        ),
        patch.object(SkillsPoolOperatorCommands, "wake", wake),
        patch.object(
            SkillsPoolRecoveryService,
            "resolve_repair_state",
            resolve,
        ),
        patch.object(SkillsPoolRollbackService, "rollback", rollback),
    ]
    for patcher in patchers:
        patcher.start()
    world.skills_pool_patchers = patchers


def _seed_rollout_error(world) -> None:
    def fail(*_args, **_kwargs):
        raise RolloutOperationError("invalid rollout config")

    patcher = patch.object(
        SkillsPoolRolloutOperations,
        "get_snapshot",
        fail,
    )
    patcher.start()
    world.skills_pool_patchers = [patcher]


def _stop_patches(_response, world) -> None:
    for patcher in reversed(getattr(world, "skills_pool_patchers", [])):
        patcher.stop()


_HAPPY_CASES = (
    ("GET", "/api/ops/skills-pool/rollout", CaseInput(headers=_HEADERS)),
    (
        "POST",
        "/api/ops/skills-pool/rollout/feature",
        CaseInput(
            headers=_HEADERS,
            json_body={"enabled": True, "reason": "canary"},
        ),
    ),
    (
        "POST",
        "/api/ops/skills-pool/rollout/promote",
        CaseInput(
            headers=_HEADERS,
            json_body={"engine": "openclaw", "reason": "first engine"},
        ),
    ),
    (
        "POST",
        "/api/ops/skills-pool/rollout/whitelist",
        CaseInput(
            headers=_HEADERS,
            json_body={
                "owner_id": "owner-1",
                "bot_id": "bot-1",
                "batch_id": "batch-1",
                "reason": "canary",
            },
        ),
    ),
    (
        "POST",
        "/api/ops/skills-pool/rollout/whitelist/remove",
        CaseInput(
            headers=_HEADERS,
            json_body={
                "owner_id": "owner-1",
                "bot_id": "bot-1",
                "reason": "remove",
            },
        ),
    ),
    (
        "POST",
        "/api/ops/skills-pool/rollout/batches/accept",
        CaseInput(
            headers=_HEADERS,
            json_body={
                "engine": "openclaw",
                "batch_id": "batch-1",
                "reason": "accepted",
            },
        ),
    ),
    (
        "POST",
        "/api/ops/skills-pool/rollout/controls",
        CaseInput(
            headers=_HEADERS,
            json_body={
                "owner_id": "owner-1",
                "bot_id": "bot-1",
                "batch_id": "batch-1",
                "group": "negative",
                "reason": "control",
            },
        ),
    ),
    (
        "GET",
        "/api/ops/skills-pool/bots/{bot_id}",
        CaseInput(
            headers=_HEADERS,
            path_params={"bot_id": "bot-1"},
            query_params={"owner_id": "owner-1"},
        ),
    ),
    (
        "GET",
        "/api/ops/skills-pool/batches/{batch_id}",
        CaseInput(
            headers=_HEADERS,
            path_params={"batch_id": "batch-1"},
            query_params={"engine": "openclaw"},
        ),
    ),
    *(
        (
            "POST",
            f"/api/ops/skills-pool/bots/{{bot_id}}/{action}",
            CaseInput(
                headers=_HEADERS,
                path_params={"bot_id": "bot-1"},
                json_body={"owner_id": "owner-1"},
            ),
        )
        for action in ("wake", "retry")
    ),
    (
        "POST",
        "/api/ops/skills-pool/bots/{bot_id}/repair",
        CaseInput(
            headers=_HEADERS,
            path_params={"bot_id": "bot-1"},
            json_body={
                "owner_id": "owner-1",
                "migration_generation": "generation-1",
                "note": "verified",
                "resolution": "pool_committed",
            },
        ),
    ),
    (
        "POST",
        "/api/ops/skills-pool/bots/{bot_id}/rollback",
        CaseInput(
            headers=_HEADERS,
            path_params={"bot_id": "bot-1"},
            json_body={
                "owner_id": "owner-1",
                "rollback_generation": "rollback-1",
                "note": "verified",
            },
        ),
    ),
)


for _index, (_method, _path, _input) in enumerate(_HAPPY_CASES):
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(
            status=200,
            json_contains={"success": True},
        ),
        extra_assertions=(_stop_patches,),
    )(lambda: None)


_VALIDATION_ERRORS = tuple(
    (method, path, case_input)
    for method, path, case_input in _HAPPY_CASES
    if method == "POST"
)

for _index, (_method, _path, _input) in enumerate(_VALIDATION_ERRORS):
    endpoint_test(
        method=_method,
        path=_path,
        scenario="validation_error",
        input=CaseInput(
            headers=_HEADERS,
            path_params=_input.path_params,
        ),
        expect=ExpectError(status=422),
    )(lambda: None)


endpoint_test(
    method="GET",
    path="/api/ops/skills-pool/rollout",
    scenario="invalid_config",
    input=CaseInput(headers=_HEADERS),
    seed=_seed_rollout_error,
    expect=ExpectError(status=409),
    extra_assertions=(_stop_patches,),
)(lambda: None)

for _path, _path_params in (
    ("/api/ops/skills-pool/bots/{bot_id}", {"bot_id": "bot-1"}),
    ("/api/ops/skills-pool/batches/{batch_id}", {"batch_id": "batch-1"}),
):
    endpoint_test(
        method="GET",
        path=_path,
        scenario="missing_query_identity",
        input=CaseInput(headers=_HEADERS, path_params=_path_params),
        expect=ExpectError(status=422),
    )(lambda: None)
