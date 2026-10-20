"""Endpoint-framework coverage for the genome registry operations (RSI-03).

The thirteen ``/openapi/v1/bots/{bot_id}/genome`` routes through the assembled
public app: the gateway-principal verification, the ownership guard, the
declared bars, the real registry over the test profile's SQLite and local
device filesystem. The registry's own rules are proven in
``tests/community/core/bot_genome/``; what these cover is the surface.

Seeds build state through the same services the routes use: a personal
openclaw bot on a local binding, a ``SOUL.md`` written through the identity
service, and the registry's ``capture`` / ``record_patch`` run in process.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.core.bot_genome.canonical import revision_id, sha256_digest
from agentclaw.community.core.bot_genome.models import ApplyResult, BotRef, Provenance, ProvenanceKind, RevisionStatus
from agentclaw.community.core.bot_genome.promotion.applier import RevisionApplier
from agentclaw.community.core.bot_genome.registry import DEFAULT_POLICY
from agentclaw.community.core.bot_genome.repository.models import GenomePromotionRow
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    GenomePromotionRepositoryProtocol,
    GenomeRefRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, bind_overrides, endpoint_test

_OWNER = "genome-endpoint-owner"
_KEY = "genome-endpoint-framework-signing-key-at-least-32-bytes"
_SOUL = "# Soul\n\n## Tone\n\nWarm and brief.\n"
_SOUL_DIGEST = sha256_digest(_SOUL.encode("utf-8"))
#: The captured bot has exactly this spec, so its first revision's id is known before the seed runs.
_R1_ID = revision_id(
    {"persona": [{"type": "SOUL.md", "digest": _SOUL_DIGEST}], "skills": [], "memory": None, "resources": [],
     "tools": {"mcp": []}, "engine_config": {}, "script": None},
    DEFAULT_POLICY,
)
_PROMOTION_ID = "p" * 32
_PATCH = {
    "patch_schema": 1, "base": _R1_ID,
    "ops": [{"op": "file.edit", "target": "persona/SOUL.md",
             "edits": [{"kind": "insert_after", "anchor": "## Tone", "content": "Confirm the order id first."}]}],
    "rationale": "Owner edit after a customer complaint.", "evidence": ["feedback:fb_1"],
}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    claims = {"iss": "gateway", "aud": "backend", "iat": now, "exp": now + 3600,
              "principals": [{"type": "user", "subject": {"id": _OWNER, "username": f"{_OWNER}@example.test"}}]}
    return jwt.encode(claims, _KEY, algorithm="HS256")


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_GENOME = "/openapi/v1/bots/{bot_id}/genome"


#: Fixed: the only host-filesystem state a seed leaves is this bot's SOUL.md, rewritten identically each run.
_BOT_ID = "genome-endpoint-bot"


def _bot_ref(bot_id: str) -> BotRef:
    return BotRef(owner_id=_OWNER, bot_id=bot_id, entity_id=_OWNER, engine_type="openclaw", bot_type="personal")


def _params(bot_id: str = _BOT_ID, *, json_body=None, raw_body=None, query=None, **extra) -> CaseInput:
    return CaseInput(path_params={"bot_id": bot_id, **extra}, query_params={**_QUERY, **(query or {})}, headers=_HEADERS,
                     json_body=json_body, raw_body=raw_body)


def _seed_bot(world) -> str:
    """A personal openclaw bot on a local binding without ``adapter_port``, so identity files use the local filesystem."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    bot_id = _BOT_ID
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=f"local_dev_{bot_id}", device_provider="local", env="dev",
        device_props={}, status="ACTIVE", apply_reason="genome endpoint seed", applied_by=_OWNER,
    )
    world.get(BotRepository).insert({
        "bot_id": bot_id, "bot_name": f"Bot {bot_id}", "owner_id": _OWNER, "owner_name": _OWNER, "bot_type": "personal",
        "status": "ACTIVE", "entity_id": _OWNER, "entity_type": "staff", "creator_id": _OWNER, "active_engine": "openclaw",
        "device_id": f"local_dev_{bot_id}", "binding_id": binding_id,
    })
    asyncio.run(world.get(IdentityService).write_identity_file("staff", _OWNER, bot_id, "SOUL.md", _SOUL, _OWNER, engine_type="openclaw"))
    return bot_id


def _seed_captured(world) -> str:
    bot_id = _seed_bot(world)
    recorded = asyncio.run(world.get(GenomeRegistryProtocol).capture(_bot_ref(bot_id), created_by=Provenance(ProvenanceKind.USER, _OWNER)))
    assert recorded.revision.revision.id == _R1_ID, "the seed's captured spec must be the one the static patch names as base"
    return bot_id


def _seed_two_revisions(world) -> str:
    bot_id = _seed_captured(world)
    asyncio.run(world.get(GenomeRegistryProtocol).record_patch(
        _bot_ref(bot_id), _PATCH, created_by=Provenance(ProvenanceKind.USER, _OWNER), status=RevisionStatus.DRAFT
    ))
    return bot_id


def _seed_active(world) -> str:
    bot_id = _seed_captured(world)
    moved = world.get(GenomeRefRepositoryProtocol).move(
        env=get_current_env(), entity_id=_OWNER, bot_id=bot_id, name="active", revision_id=_R1_ID, expected_revision_id=None,
        moved_kind="user", moved_actor=_OWNER, run_id=None, reason="seed", moved_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    assert moved is not None
    return bot_id


def _seed_promotion_row(world) -> str:
    bot_id = _seed_two_revisions(world)
    world.get(GenomePromotionRepositoryProtocol).insert(GenomePromotionRow(
        env=get_current_env(), entity_id=_OWNER, bot_id=bot_id, promotion_id=_PROMOTION_ID, ref="active", revision_id=_R1_ID,
        from_revision_id=None, document_digest=_SOUL_DIGEST, document_json="{}", actor_kind="user", actor_id=_OWNER, reason="seed",
        status="applied", apply_kind="fake", apply_reference="apply_1", apply_detail="started",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    return bot_id


def _seed_deliverable(world) -> str:
    """A captured bot whose delivery seam accepts, so a promotion can succeed before RSI-04."""
    bot_id = _seed_captured(world)

    async def _start(self, bot, compiled, *, actor, reason):
        return ApplyResult(kind="fake", reference="apply_1", detail="started")

    bind_overrides(world, RevisionApplier, {"preflight": lambda self, bot, compiled: None, "start": _start})
    return bot_id


_NOBODY = "no-such-bot"

# -- revisions -----------------------------------------------------------------------------------


@endpoint_test(method="GET", path=f"{_GENOME}/revisions", scenario="lists_newest_first", input=_params(), seed=_seed_two_revisions,
               expect=ExpectSuccess(status=200, json_contains={"data": {"total": 2, "items": [{"seq": 2, "parent": _R1_ID}, {"seq": 1, "parent": None}]}}))
def list_revisions_ok():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/revisions", scenario="unknown_bot", input=_params(_NOBODY), seed=_seed_captured, expect=ExpectError(status=404))
def list_revisions_unknown_bot():
    pass


@endpoint_test(method="POST", path=f"{_GENOME}/revisions", scenario="records_a_draft_from_a_patch", input=_params(json_body={"status": "draft", "patch": _PATCH}),
               seed=_seed_captured, expect=ExpectSuccess(status=201, json_contains={"data": {"seq": 2, "parent": _R1_ID, "status": "draft", "risk_tier": "T2", "created": True}}))
def record_patch_ok():
    pass


@endpoint_test(method="POST", path=f"{_GENOME}/revisions", scenario="base_is_not_a_revision_of_this_bot",
               input=_params(json_body={"status": "draft", "patch": dict(_PATCH, base="sha256:" + "0" * 64)}), seed=_seed_captured, expect=ExpectError(status=404))
def record_patch_unknown_base():
    pass


@endpoint_test(method="POST", path=f"{_GENOME}/revisions:capture", scenario="records_the_first_revision", input=_params(), seed=_seed_bot,
               expect=ExpectSuccess(status=201, json_contains={"data": {"id": _R1_ID, "seq": 1, "parent": None, "status": "draft", "created": True}}))
def capture_ok():
    pass


@endpoint_test(method="POST", path=f"{_GENOME}/revisions:capture", scenario="unknown_bot", input=_params(_NOBODY), seed=_seed_bot, expect=ExpectError(status=404))
def capture_unknown_bot():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/revisions/{{rev:path}}", scenario="by_sequence_number", input=_params(rev="r1"), seed=_seed_captured,
               expect=ExpectSuccess(status=200, json_contains={"data": {"genome_schema": 1, "revision": {"id": _R1_ID, "seq": 1}, "policy": DEFAULT_POLICY}}))
def get_revision_ok():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/revisions/{{rev:path}}", scenario="no_such_revision", input=_params(rev="r9"), seed=_seed_captured, expect=ExpectError(status=404))
def get_revision_not_found():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/revisions/{{rev:path}}/diff", scenario="two_revisions_of_the_bot", input=_params(rev="r2", query={"against": "r1"}),
               seed=_seed_two_revisions, expect=ExpectSuccess(status=200, json_contains={"data": {"against": _R1_ID, "risk_tier": "T2", "changes": [{"gene": "persona/SOUL.md", "kind": "modified"}]}}))
def diff_ok():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/revisions/{{rev:path}}/diff", scenario="against_a_missing_revision", input=_params(rev="r1", query={"against": "r9"}),
               seed=_seed_captured, expect=ExpectError(status=404))
def diff_not_found():
    pass


# -- policy --------------------------------------------------------------------------------------


@endpoint_test(method="PUT", path=f"{_GENOME}/policy", scenario="records_a_revision_with_the_new_policy",
               input=_params(json_body={"mutable_genes": ["persona"], "pins": [], "risk_overrides": {}}), seed=_seed_active,
               expect=ExpectSuccess(status=201, json_contains={"data": {"seq": 2, "parent": _R1_ID, "status": "draft", "created": True}}))
def set_policy_ok():
    pass


@endpoint_test(method="PUT", path=f"{_GENOME}/policy", scenario="unsorted_gene_paths",
               input=_params(json_body={"mutable_genes": ["skills", "persona"], "pins": [], "risk_overrides": {}}), seed=_seed_active, expect=ExpectError(status=422))
def set_policy_rejected():
    pass


# -- refs ----------------------------------------------------------------------------------------


@endpoint_test(method="GET", path=f"{_GENOME}/refs", scenario="active_after_a_seeded_move", input=_params(), seed=_seed_active,
               expect=ExpectSuccess(status=200, json_contains={"data": {"refs": [{"name": "active", "revision": _R1_ID}]}}))
def list_refs_ok():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/refs", scenario="unknown_bot", input=_params(_NOBODY), seed=_seed_captured, expect=ExpectError(status=404))
def list_refs_unknown_bot():
    pass


@endpoint_test(method="PUT", path=f"{_GENOME}/refs/draft", scenario="moves_by_compare_and_swap", input=_params(json_body={"revision": "r1", "expected_revision": None, "reason": "wip"}),
               seed=_seed_captured, expect=ExpectSuccess(status=200, json_contains={"data": {"name": "draft", "revision": _R1_ID, "reason": "wip"}}))
def move_draft_ok():
    pass


@endpoint_test(method="PUT", path=f"{_GENOME}/refs/draft", scenario="stale_expectation", input=_params(json_body={"revision": "r1", "expected_revision": _R1_ID}),
               seed=_seed_captured, expect=ExpectError(status=409))
def move_draft_conflict():
    pass


# -- content -------------------------------------------------------------------------------------


@endpoint_test(method="GET", path=f"{_GENOME}/content/{{digest}}", scenario="bytes_the_bot_captured", input=_params(digest=_SOUL_DIGEST), seed=_seed_captured,
               expect=ExpectSuccess(status=200), extra_assertions=(lambda response, world: _assert_body(response, _SOUL.encode("utf-8")),))
def read_content_ok():
    pass


def _assert_body(response, expected: bytes) -> None:
    assert response.content == expected and response.headers["content-type"].startswith("application/octet-stream")


@endpoint_test(method="GET", path=f"{_GENOME}/content/{{digest}}", scenario="digest_never_stored_for_this_bot", input=_params(digest="sha256:" + "0" * 64),
               seed=_seed_captured, expect=ExpectError(status=404))
def read_content_not_reachable():
    pass


@endpoint_test(method="PUT", path=f"{_GENOME}/content", scenario="stores_uploaded_bytes", input=_params(raw_body=b"import sys\n"), seed=_seed_bot,
               expect=ExpectSuccess(status=201, json_contains={"data": {"digest": sha256_digest(b"import sys\n"), "size": 11, "created": True}}))
def put_content_ok():
    pass


@endpoint_test(method="PUT", path=f"{_GENOME}/content", scenario="unknown_bot", input=_params(_NOBODY, raw_body=b"x"), seed=_seed_bot, expect=ExpectError(status=404))
def put_content_unknown_bot():
    pass


# -- promotions ----------------------------------------------------------------------------------


@endpoint_test(method="POST", path=f"{_GENOME}/promotions", scenario="moves_active_and_starts_delivery",
               input=_params(json_body={"revision": "r1", "expected_revision": None, "reason": "ship it"}), seed=_seed_deliverable,
               expect=ExpectSuccess(status=201, json_contains={"data": {"ref": "active", "revision": _R1_ID, "from_revision": None, "status": "applied", "apply_reference": "apply_1"}}))
def promote_ok():
    pass


@endpoint_test(method="POST", path=f"{_GENOME}/promotions", scenario="refused_at_the_delivery_seam_before_any_ref_moves",
               input=_params(json_body={"revision": "r1", "expected_revision": None, "reason": "ship it"}), seed=_seed_captured,
               expect=ExpectError(status=422, json_contains={"data": {"reasons": [{"code": "not_appliable"}]}}))
def promote_not_appliable():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/promotions", scenario="lists_without_documents", input=_params(), seed=_seed_promotion_row,
               expect=ExpectSuccess(status=200, json_contains={"data": {"total": 1, "items": [{"id": _PROMOTION_ID, "revision": _R1_ID, "status": "applied", "document": None}]}}))
def list_promotions_ok():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/promotions", scenario="unknown_bot", input=_params(_NOBODY), seed=_seed_promotion_row, expect=ExpectError(status=404))
def list_promotions_unknown_bot():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/promotions/{{promotion_id}}", scenario="with_its_document", input=_params(promotion_id=_PROMOTION_ID), seed=_seed_promotion_row,
               expect=ExpectSuccess(status=200, json_contains={"data": {"id": _PROMOTION_ID, "document": {}}}))
def get_promotion_ok():
    pass


@endpoint_test(method="GET", path=f"{_GENOME}/promotions/{{promotion_id}}", scenario="no_such_promotion", input=_params(promotion_id="x" * 32), seed=_seed_promotion_row,
               expect=ExpectError(status=404))
def get_promotion_not_found():
    pass
