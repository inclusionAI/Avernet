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
    ExpectRetired,
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
    "set_engine_admission",
    "set_environment_rollout",
    "set_owner_rollout",
    "set_bot_allow",
    "set_bot_exclusion",
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
            json_body={
                "enabled": True,
                "expected_revision": None,
                "reason": "canary",
            },
        ),
    ),
    (
        "PUT",
        "/api/ops/skills-pool/rollout/engines/{engine}/admission",
        CaseInput(
            headers=_HEADERS,
            path_params={"engine": "openclaw"},
            json_body={
                "enabled": True,
                "expected_revision": "revision-1",
                "reason": "enable engine",
            },
        ),
    ),
    (
        "PUT",
        "/api/ops/skills-pool/rollout/environments/{engine}",
        CaseInput(
            headers=_HEADERS,
            path_params={"engine": "openclaw"},
            json_body={
                "expected_revision": "revision-1",
                "reason": "promote environment",
            },
        ),
    ),
    (
        "PUT",
        "/api/ops/skills-pool/rollout/owners/{owner_id}",
        CaseInput(
            headers=_HEADERS,
            path_params={"owner_id": "owner-1"},
            json_body={
                "engine": "openclaw",
                "expected_revision": "revision-1",
                "reason": "promote owner bots",
            },
        ),
    ),
    (
        "PUT",
        "/api/ops/skills-pool/rollout/bots/{bot_id}/allow",
        CaseInput(
            headers=_HEADERS,
            path_params={"bot_id": "bot-1"},
            json_body={
                "owner_id": "owner-1",
                "engine": "openclaw",
                "expected_revision": "revision-1",
                "reason": "canary",
            },
        ),
    ),
    (
        "PUT",
        "/api/ops/skills-pool/rollout/bots/{bot_id}/exclude",
        CaseInput(
            headers=_HEADERS,
            path_params={"bot_id": "bot-1"},
            json_body={
                "owner_id": "owner-1",
                "engine": "openclaw",
                "expected_revision": "revision-1",
                "reason": "exclude",
            },
        ),
    ),
    (
        "DELETE",
        "/api/ops/skills-pool/rollout/environments/{engine}",
        CaseInput(
            headers=_HEADERS,
            path_params={"engine": "openclaw"},
            json_body={
                "expected_revision": "revision-1",
                "reason": "stop environment rollout",
            },
        ),
    ),
    (
        "DELETE",
        "/api/ops/skills-pool/rollout/owners/{owner_id}",
        CaseInput(
            headers=_HEADERS,
            path_params={"owner_id": "owner-1"},
            json_body={
                "engine": "openclaw",
                "expected_revision": "revision-1",
                "reason": "stop owner rollout",
            },
        ),
    ),
    (
        "DELETE",
        "/api/ops/skills-pool/rollout/bots/{bot_id}/allow",
        CaseInput(
            headers=_HEADERS,
            path_params={"bot_id": "bot-1"},
            json_body={
                "owner_id": "owner-1",
                "engine": "openclaw",
                "expected_revision": "revision-1",
                "reason": "remove allow",
            },
        ),
    ),
    (
        "DELETE",
        "/api/ops/skills-pool/rollout/bots/{bot_id}/exclude",
        CaseInput(
            headers=_HEADERS,
            path_params={"bot_id": "bot-1"},
            json_body={
                "owner_id": "owner-1",
                "engine": "openclaw",
                "expected_revision": "revision-1",
                "reason": "remove exclusion",
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


for _index, _retired_path in enumerate(
    (
        "/api/ops/skills-pool/rollout/promote",
        "/api/ops/skills-pool/rollout/full",
        "/api/ops/skills-pool/rollout/whitelist",
        "/api/ops/skills-pool/rollout/whitelist/remove",
        "/api/ops/skills-pool/rollout/owners",
        "/api/ops/skills-pool/rollout/batches/accept",
        "/api/ops/skills-pool/rollout/controls",
    )
):
    endpoint_test(
        method="POST",
        path=_retired_path,
        scenario="retired",
        input=CaseInput(headers=_HEADERS, json_body={"reason": "legacy client"}),
        expect=ExpectRetired(),
    )(lambda: None)


_VALIDATION_ERRORS = tuple(
    (method, path, case_input)
    for method, path, case_input in _HAPPY_CASES
    if method in {"POST", "PUT", "DELETE"}
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
