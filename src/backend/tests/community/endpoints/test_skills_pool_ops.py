"""Endpoint-injection coverage for the Skills Pool operator API.

These routes are thin: each one calls a rollout / query / command service and
envelopes the result. What the cases here pin is that layer — routing, request
validation, the ``RolloutOperationError`` → 409 mapping — so the services
behind them are stood in for.

The stand-ins are bound through the injector as subclasses of whatever the
graph wired (``bind_overrides``), never patched onto the production classes.
That matters beyond style: a class-level patch outlived the case that set it
whenever an assertion failed before the teardown hook ran, silently poisoning
later tests. A binding cannot, because the injector it lives on is discarded
with the test.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.api.skills_pool_operational_query_service import (
    SkillsPoolOperationalQueryServiceProtocol,
)
from agentclaw.community.api.skills_pool_operator_commands_service import (
    SkillsPoolOperatorCommandsServiceProtocol,
)
from agentclaw.community.api.skills_pool_recovery_service import (
    SkillsPoolRecoveryServiceProtocol,
)
from agentclaw.community.api.skills_pool_rollback_service import (
    SkillsPoolRollbackServiceProtocol,
)
from agentclaw.community.api.skills_pool_rollout_service import (
    SkillsPoolRolloutServiceProtocol,
)
from agentclaw.community.core.skills_pool.operations import RolloutOperationError
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)


_HEADERS = {"x-user-id": "skills-pool-operator"}
_SCOPE = BotSkillLayoutScope("dev", "entity-1", "bot-1")

# Every rollout mutation the router exposes. Listed rather than derived so a
# new route shows up here as a deliberate edit.
_ROLLOUT_MUTATIONS = (
    "set_feature_enabled",
    "set_full_rollout",
    "set_owner_full_rollout",
    "promote_engine",
    "add_bot",
    "remove_bot",
    "accept_batch",
    "set_control_bot",
)


@dataclass(frozen=True)
class _Result:
    outcome: str = "ok"
    scope: BotSkillLayoutScope | None = None
    promotion_ready: bool = True
    rollout_config_version: str = "config-1"


def _seed_happy_services(world) -> None:
    """Bind every skills-pool service to a stand-in that reports success."""
    result = _Result(scope=_SCOPE)

    def answer(_self, *_args, **_kwargs):
        return result

    async def answer_async(_self, *_args, **_kwargs):
        return result

    bind_overrides(
        world,
        SkillsPoolRolloutServiceProtocol,
        {"get_snapshot": answer, **{m: answer for m in _ROLLOUT_MUTATIONS}},
    )
    bind_overrides(
        world,
        SkillsPoolOperationalQueryServiceProtocol,
        {"get_bot": answer, "summarize_batch": answer},
    )
    bind_overrides(world, SkillsPoolOperatorCommandsServiceProtocol, {"wake": answer})
    bind_overrides(
        world, SkillsPoolRecoveryServiceProtocol, {"resolve_repair_state": answer},
    )
    bind_overrides(
        world, SkillsPoolRollbackServiceProtocol, {"rollback": answer_async},
    )


def _seed_rollout_error(world) -> None:
    """A rollout config the operations layer rejects — the 409 mapping."""

    def fail(_self, *_args, **_kwargs):
        raise RolloutOperationError("invalid rollout config")

    bind_overrides(world, SkillsPoolRolloutServiceProtocol, {"get_snapshot": fail})


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
        "/api/ops/skills-pool/rollout/full",
        CaseInput(
            headers=_HEADERS,
            json_body={"enabled": True, "reason": "promote environment"},
        ),
    ),
    (
        "POST",
        "/api/ops/skills-pool/rollout/owners",
        CaseInput(
            headers=_HEADERS,
            json_body={
                "owner_id": "owner-1",
                "engine": "openclaw",
                "enabled": True,
                "acceptance_batch_id": "batch-1",
                "reason": "promote owner bots",
            },
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
