"""Assembled signed RSI callbacks over real Registry/promotion persistence.

Only runtime I/O and the Apply delivery edge are substituted through DI. Public
baseline/drift and the independent control relay share their existing fixtures.
"""

import asyncio

import httpx
from agentclaw.community.api.bot_genome_promotion_service import (
    GenomePromotionServiceProtocol,
    ManualPromotionAuthorization,
)
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_genome.baseline import ensure_active_baseline
from agentclaw.community.core.bot_genome.models import (
    Provenance,
    ProvenanceKind,
    RevisionStatus,
    RunScope,
)
from agentclaw.community.core.bot_genome.patch_models import GenomePatch
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.di.evolution_control_config import EvolutionControlConfig
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from avernet_evolution.adapters.backend_access import fingerprint
from avernet_evolution.adapters.backend_data import HEADER, PREFIX, issue_data_proof
from avernet_evolution.core.evolution_run.data_scope import DataScope

from tests.community.endpoints.test_openapi_genome import (
    _BOT_ID,
    _GENOME,
    _KEY,
    _NOBODY,
    _OWNER,
    _PATCH,
    _R1_ID,
    _SOUL,
    _SOUL_DIGEST,
    _bot_ref,
    _params,
    _seed_active,
    _seed_bot,
    _seed_deliverable,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_AUTH_ID = "a" * 32
_RUN = "run-endpoint"
_STRATEGY = "fixture/manual@1"
_BASE = PREFIX + "/{bot_id}"


def _declare():
    """The framework owns invocation, including the concrete route and inputs."""


def _seed_runtime(world):
    async def resolve(self, bot_id, owner_id, caller_id):
        assert (bot_id, owner_id, caller_id) == (_BOT_ID, _OWNER, _OWNER)
        return BotFacts(_BOT_ID, "personal", "openclaw", _OWNER)

    async def call(self, *, bot_id, owner_id, stage, method, path, **kwargs):
        assert (bot_id, owner_id, stage) == (_BOT_ID, _OWNER, "draft")
        if path == "/api/engine/models:complete":
            assert method == "POST" and kwargs["body"]["messages"][0]["content"] == "probe"
            return EngineResult({"text": "fixture response", "model": "fixture/model", "tokens": 3})
        assert method == "GET"
        if path == "/api/sessions":
            assert kwargs["params"]["source"] == "all_but_others"
            return EngineResult([{"id": "fixture-session", "title": "Fixture"}])
        assert path == "/api/sessions/fixture-session/messages"
        return EngineResult([{"id": "message-1", "role": "user", "content": "probe"}])

    bind_overrides(world, EngineRuntimeRelayProtocol, {"resolve_bot_off_loop": resolve, "call": call})


def _manual_candidate(world):
    """Real candidate provenance, frozen parent and preflight, without a live Bot."""
    registry = world.get(GenomeRegistryProtocol)
    recorded = asyncio.run(registry.commit_patch(
        _bot_ref(_BOT_ID), GenomePatch.from_json(_PATCH),
        created_by=Provenance(ProvenanceKind.STRATEGY_RUN, _STRATEGY, run_id=_RUN, principal=_OWNER),
        status=RevisionStatus.CANDIDATE,
        run=RunScope(_RUN, _STRATEGY, ("persona",)),
    ))
    meta = recorded.revision.revision
    return ManualPromotionAuthorization(
        id=_AUTH_ID, candidate_id=meta.patch_digest, run_id=_RUN,
        revision_id=meta.id, parent_revision_id=_R1_ID, strategy=_STRATEGY,
        user_id=_OWNER, reason="Human inspected fixture diff; not an evaluation pass",
        authorized_at="2026-11-26T00:00:00Z",
    )


def _callback(method, suffix, *, body=None, manual=False, allowed=True):
    """Sign just before invocation so a long suite cannot expire import-time proofs."""
    payload = dict(body) if body is not None else None
    headers = {}
    params = {"bot_id": _BOT_ID, "rev": "active", "digest": _SOUL_DIGEST,
              "session_id": "fixture-session", "authorization_id": _AUTH_ID}
    path = suffix.format(**params)

    def seed(world):
        if manual:
            _seed_deliverable(world)
            # Delivery fixture captures, but deliberately does not name active.
            registry = world.get(GenomeRegistryProtocol)
            asyncio.run(ensure_active_baseline(
                registry, _bot_ref(_BOT_ID), actor=Provenance(ProvenanceKind.USER, _OWNER)
            ))
        else:
            _seed_active(world)
        world.injector.binder.bind(
            EvolutionControlConfig,
            to=EvolutionControlConfig(builtin_data_tenant=DEFAULT_AVERNET_TENANT),
        )
        _seed_runtime(world)
        if manual:
            authorization = _manual_candidate(world)
            if method == "POST":
                payload.update(authorization.model_dump(mode="json"))
            else:
                asyncio.run(world.get(GenomePromotionServiceProtocol).promote_manual(
                    _bot_ref(_BOT_ID), authorization
                ))
        if allowed:
            scope = DataScope(
                _bot_ref(_BOT_ID), _OWNER, _RUN, _R1_ID, _STRATEGY, ("persona",),
                promotion_authorization_id=_AUTH_ID if manual else None,
            )
            request = httpx.Request(method, "http://fixture.test" + path, json=payload)
            headers[HEADER] = issue_data_proof(
                _KEY, DEFAULT_AVERNET_TENANT, scope,
                fingerprint(method, path, "", request.content, request.headers),
            )

    return CaseInput(path_params=params, headers=headers, json_body=payload), seed


# Public additions use the same real Bot/Registry as the original Genome cases.
for _method, _suffix, _seed, _status, _expected in (
    ("GET", "/drift", _seed_active, 200, {"data": {"drift": None}}),
    ("POST", "/baseline:initialize", _seed_bot, 201, {"data": {"revision": _R1_ID, "created": True}}),
):
    for _allowed in (True, False):
        endpoint_test(
            method=_method, path=_GENOME + _suffix,
            scenario="rsi_baseline_observation" if _allowed else "rsi_unknown_bot",
            input=_params() if _allowed else _params(_NOBODY), seed=_seed,
            expect=ExpectSuccess(status=_status, json_contains=_expected) if _allowed else ExpectError(status=404),
        )(_declare)


_CALLBACKS = (
    ("GET", "/genome/drift", None, False, 200, {"data": {"drift": None}}),
    ("GET", "/genome/revisions/{rev:path}", None, False, 200, {"data": {"revision": {"id": _R1_ID}}}),
    ("GET", "/genome/content/{digest}", None, False, 200, None),
    ("POST", "/genome/revisions", {
        "patch": _PATCH, "status": "candidate",
        "run": {"run_id": _RUN, "strategy": _STRATEGY, "allowed_genes": ["persona"]},
    }, False, 201, {"data": {"status": "candidate"}}),
    ("GET", "/sessions", None, False, 200, {"data": {"items": [{"session_id": "fixture-session"}]}}),
    ("GET", "/sessions/{session_id}/messages", None, False, 200, {"data": {"items": [{"content": "probe"}]}}),
    ("POST", "/models:complete", {"messages": [{"role": "user", "content": "probe"}]}, False, 200,
     {"data": {"text": "fixture response", "model": "fixture/model", "tokens": 3}}),
    ("POST", "/genome/manual-promotions", {}, True, 200, {"data": {"id": _AUTH_ID, "status": "applying"}}),
    ("GET", "/genome/manual-promotions/{authorization_id}", None, True, 200,
     {"data": {"id": _AUTH_ID, "status": "applying"}}),
)


def _assert_content(response, world):
    assert response.content == _SOUL.encode()


for _method, _suffix, _body, _manual, _status, _expected in _CALLBACKS:
    for _allowed in (True, False):
        # Normalize the path converter only for signing the concrete request.
        _input, _seed = _callback(
            _method, (_BASE + _suffix).replace("{rev:path}", "{rev}"),
            body=_body, manual=_manual, allowed=_allowed,
        )
        endpoint_test(
            method=_method, path=_BASE + _suffix,
            scenario="rsi_signed_callback" if _allowed else "rsi_missing_data_proof",
            input=_input, seed=_seed,
            expect=ExpectSuccess(status=_status, json_contains=_expected or {}) if _allowed else ExpectError(status=403),
            extra_assertions=(_assert_content,) if _allowed and "/content/" in _suffix else (),
        )(_declare)
