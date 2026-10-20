"""``/openapi/v1/bots/{bot_id}/genome`` — routes, bars, admission, handlers, error envelopes, the body cap."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Response
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION
from agentclaw.community.adapters.http.openapi_v1.admission_modes import AdmissionMode
from agentclaw.community.adapters.http.openapi_v1.authorization_rules import AUTHORIZATION, EDIT_LOCK
from agentclaw.community.adapters.http.openapi_v1.bots import genome
from agentclaw.community.adapters.http.openapi_v1.bots.genome import router
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_genome import (
    MoveRefRequest,
    PolicyRequest,
    PromoteRequest,
    RecordPatchRequest,
    RunScopePayload,
)
from agentclaw.community.adapters.http.openapi_v1.responses import mapped_error_response
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_genome.errors import (
    CaptureRefused,
    ContentNotReachable,
    Drifted,
    LimitExceeded,
    MoverNotAllowed,
    NotAppliable,
    NotPromotable,
    PatchRejected,
    PolicyRejected,
    PromotionUnsupported,
    Reason,
    RefConflict,
    RevisionNotFound,
)
from agentclaw.community.core.bot_genome.models import (
    CompiledManifest,
    GenomeDiff,
    GenomeRef,
    GenomeRevision,
    Promotion,
    PromotionPage,
    Provenance,
    ProvenanceKind,
    RecordedRevision,
    RefMover,
    RevisionMetadata,
    RevisionPage,
    RevisionStatus,
    RunScope,
)

pytestmark = pytest.mark.asyncio

_GENOME = "/openapi/v1/bots/{bot_id}/genome"
R1, R2 = ("sha256:" + c * 64 for c in "ab")
NOW = datetime(2026, 10, 19, 12, 0, 0)
OWNER = Provenance(ProvenanceKind.USER, "u1")
BOT = {"entity_id": "ent_1", "active_engine": "openclaw", "bot_type": "personal", "entity_type": "staff"}


def _request(method: str = "GET", headers: dict[str, str] | None = None, body: bytes = b"") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    request = Request({"type": "http", "method": method, "path": "/", "headers": raw, "query_string": b""})
    request.state.trace_id = "trace-genome"
    chunks = [body[i : i + 7] for i in range(0, len(body), 7)]

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    request._receive = receive
    return request


def _bot_service() -> Mock:
    service = Mock()
    service.get_bot.return_value = dict(BOT)
    return service


def _meta(rev: str = R1, seq: int = 1, status: RevisionStatus = RevisionStatus.DRAFT, parent: str | None = None) -> RevisionMetadata:
    return RevisionMetadata(id=rev, seq=seq, parent=parent, created_by=OWNER, created_at=NOW, status=status, patch_digest=None)


def _revision(rev: str = R1) -> GenomeRevision:
    return GenomeRevision(genome_schema=1, revision=_meta(rev), spec={"persona": []}, policy={"mutable_genes": []})


def _promotion(doc: dict | None = None) -> Promotion:
    return Promotion(
        id="p" * 32, ref="active", revision=R2, from_revision=R1, document_digest=R1, going_back=False, actor=OWNER,
        reason="ship", status="applied", apply_kind="fake", apply_reference="apply_1", apply_detail="", created_at=NOW, document=doc,
    )


# -- the surface -----------------------------------------------------------------------------------


def test_the_thirteen_operations_are_mounted_once_in_the_right_order() -> None:
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert {(m.upper(), p) for p, ops in paths.items() for m in ops} == {
        ("GET", f"{_GENOME}/revisions"), ("POST", f"{_GENOME}/revisions"), ("POST", f"{_GENOME}/revisions:capture"),
        ("GET", f"{_GENOME}/revisions/{{rev}}/diff"), ("GET", f"{_GENOME}/revisions/{{rev}}"), ("PUT", f"{_GENOME}/policy"),
        ("GET", f"{_GENOME}/refs"), ("PUT", f"{_GENOME}/refs/draft"), ("GET", f"{_GENOME}/content/{{digest}}"),
        ("PUT", f"{_GENOME}/content"), ("POST", f"{_GENOME}/promotions"), ("GET", f"{_GENOME}/promotions"),
        ("GET", f"{_GENOME}/promotions/{{promotion_id}}"),
    }
    order = [r.path for r in router.routes]
    assert order.index(f"{_GENOME}/revisions:capture") < order.index(f"{_GENOME}/revisions/{{rev:path}}")
    assert order.index(f"{_GENOME}/revisions/{{rev:path}}/diff") < order.index(f"{_GENOME}/revisions/{{rev:path}}")
    assert paths[f"{_GENOME}/content"]["put"]["requestBody"]["content"] == {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}
    assert "application/octet-stream" in paths[f"{_GENOME}/content/{{digest}}"]["get"]["responses"]["200"]["content"]


def test_wildcard_paths_route_candidate_refs_without_swallowing_the_suffixes() -> None:
    app = FastAPI()
    app.include_router(router)
    matches = {}
    for path in ("/openapi/v1/bots/b/genome/revisions/candidate/run_7/1", "/openapi/v1/bots/b/genome/revisions/candidate/run_7/1/diff", "/openapi/v1/bots/b/genome/revisions:capture", "/openapi/v1/bots/b/genome/revisions/r42"):
        for route in router.routes:
            match, params = route.matches({"type": "http", "method": "GET" if "capture" not in path else "POST", "path": path})
            if match.name == "FULL":
                matches[path] = (route.path, params["path_params"])
                break
    assert matches["/openapi/v1/bots/b/genome/revisions/candidate/run_7/1"] == (f"{_GENOME}/revisions/{{rev:path}}", {"bot_id": "b", "rev": "candidate/run_7/1"})
    assert matches["/openapi/v1/bots/b/genome/revisions/candidate/run_7/1/diff"][0] == f"{_GENOME}/revisions/{{rev:path}}/diff"
    assert matches["/openapi/v1/bots/b/genome/revisions/candidate/run_7/1/diff"][1]["rev"] == "candidate/run_7/1"
    assert matches["/openapi/v1/bots/b/genome/revisions:capture"][0] == f"{_GENOME}/revisions:capture"
    assert matches["/openapi/v1/bots/b/genome/revisions/r42"][1]["rev"] == "r42"


@pytest.mark.parametrize(
    ("method", "suffix", "level", "lock"),
    [
        ("GET", "/revisions", PermissionLevel.MEMBER, None), ("POST", "/revisions", PermissionLevel.ADMIN, None),
        ("POST", "/revisions:capture", PermissionLevel.ADMIN, None), ("GET", "/revisions/{rev:path}/diff", PermissionLevel.MEMBER, None),
        ("GET", "/revisions/{rev:path}", PermissionLevel.MEMBER, None), ("PUT", "/policy", PermissionLevel.OWNER, None),
        ("GET", "/refs", PermissionLevel.MEMBER, None), ("PUT", "/refs/draft", PermissionLevel.ADMIN, None),
        ("GET", "/content/{digest}", PermissionLevel.MEMBER, None), ("PUT", "/content", PermissionLevel.ADMIN, None),
        ("POST", "/promotions", PermissionLevel.OWNER, EDIT_LOCK), ("GET", "/promotions", PermissionLevel.MEMBER, None),
        ("GET", "/promotions/{promotion_id}", PermissionLevel.MEMBER, None),
    ],
)
def test_bars_and_admission(method: str, suffix: str, level: PermissionLevel, lock) -> None:
    key = (method, f"{_GENOME}{suffix}")
    assert (AUTHORIZATION[key].level, AUTHORIZATION[key].edit_lock) == (level, lock)
    assert ADMISSION[key] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT


def test_promotion_takes_applys_bar() -> None:
    apply = AUTHORIZATION[("POST", "/openapi/v1/bots/{bot_id}/config-manifest/apply")]
    promote = AUTHORIZATION[("POST", f"{_GENOME}/promotions")]
    assert (promote.level, promote.edit_lock) == (apply.level, apply.edit_lock)


# -- handlers --------------------------------------------------------------------------------------


async def test_record_patch_answers_201_then_200_and_threads_the_run() -> None:
    registry = AsyncMock()
    registry.record_patch.return_value = RecordedRevision(_revision(R2), True, risk_tier="T2", flags=("persona/SOUL.md",), ref="candidate/run_7/1")
    body = RecordPatchRequest(status="candidate", run=RunScopePayload(run_id="run_7", binding_id="b1", allowed_genes=["persona"]), patch={"base": R1})
    response = Response()
    out = await genome.record_genome_patch("bot_1", body, _request("POST"), response, "u1", "u1", _bot_service(), registry)
    assert response.status_code == 201 and out.data.ref == "candidate/run_7/1" and out.data.flags == ["persona/SOUL.md"]
    bot, patch = registry.record_patch.await_args.args
    kwargs = registry.record_patch.await_args.kwargs
    assert (bot.entity_id, bot.engine_type, bot.bot_type, bot.owner_id) == ("ent_1", "openclaw", "personal", "u1")
    assert kwargs["status"] is RevisionStatus.CANDIDATE and kwargs["run"] == RunScope("run_7", "b1", ("persona",))
    assert kwargs["created_by"] == Provenance(ProvenanceKind.STRATEGY_RUN, "u1", run_id="run_7")
    registry.record_patch.return_value = RecordedRevision(_revision(R2), False)
    response = Response()
    out = await genome.record_genome_patch("bot_1", RecordPatchRequest(status="draft", patch={}), _request("POST"), response, "u1", "u1", _bot_service(), registry)
    assert response.status_code == 200 and out.data.created is False
    assert registry.record_patch.await_args.kwargs["created_by"] == OWNER


async def test_capture_policy_and_lookups() -> None:
    registry = AsyncMock()
    registry.capture.return_value = RecordedRevision(_revision(), True)
    response = Response()
    assert (await genome.capture_genome_revision("bot_1", _request("POST"), response, "u1", "u1", _bot_service(), registry)).data.id == R1
    assert response.status_code == 201
    registry.set_policy = Mock(return_value=RecordedRevision(_revision(), False))
    response = Response()
    out = await genome.set_genome_policy("bot_1", PolicyRequest(mutable_genes=["persona"], pins=[]), _request("PUT"), response, "u1", "u1", _bot_service(), registry)
    assert response.status_code == 200 and registry.set_policy.call_args.args[1] == {"mutable_genes": ["persona"], "pins": [], "risk_overrides": {}}
    registry.get_revision = Mock(return_value=_revision())
    out = await genome.get_genome_revision("bot_1", "r1", _request(), "u1", "u1", _bot_service(), registry)
    assert out.data.revision.id == R1 and out.data.spec == {"persona": []}
    registry.list_revisions = Mock(return_value=RevisionPage(3, (_meta(R2, 3),)))
    out = await genome.list_genome_revisions("bot_1", _request(), "u1", "u1", "candidate", None, 2, 1, _bot_service(), registry)
    assert (out.data.total, out.data.items[0].seq) == (3, 3)
    assert registry.list_revisions.call_args.kwargs == {"status": RevisionStatus.CANDIDATE, "parent": None, "page": 2, "page_size": 1}
    registry.diff = Mock(return_value=GenomeDiff(R2, R1, (), "T1", ()))
    out = await genome.diff_genome_revision("bot_1", "r2", _request(), "u1", "u1", "r1", _bot_service(), registry)
    assert (out.data.revision, out.data.against, out.data.risk_tier) == (R2, R1, "T1")


async def test_refs_content_and_promotions() -> None:
    registry = AsyncMock()
    ref = GenomeRef("draft", R1, NOW, OWNER, "wip")
    registry.list_refs = Mock(return_value=[ref])
    assert (await genome.list_genome_refs("bot_1", _request(), "u1", "u1", _bot_service(), registry)).data.refs[0].moved_by.actor == "u1"
    registry.get_revision = Mock(return_value=_revision(R2))
    registry.move_ref = Mock(return_value=ref)
    await genome.move_genome_draft_ref("bot_1", MoveRefRequest(revision="r2", expected_revision=R1, reason="wip"), _request("PUT"), "u1", "u1", _bot_service(), registry)
    update = registry.move_ref.call_args.args[1]
    assert (update.name, update.revision, update.expected_revision, update.reason) == ("draft", R2, R1, "wip")
    assert registry.move_ref.call_args.kwargs["mover"] is RefMover.OWNER
    registry.read_content = Mock(return_value=b"\x00bytes")
    raw = await genome.read_genome_content("bot_1", R1, _request(), "u1", "u1", _bot_service(), registry)
    assert (raw.body, raw.media_type) == (b"\x00bytes", "application/octet-stream")
    registry.put_content = Mock(return_value=SimpleNamespace(digest=R1, size=5, created=False))
    response = Response()
    out = await genome.put_genome_content("bot_1", _request("PUT", body=b"hello"), response, "u1", "u1", _bot_service(), registry)
    assert response.status_code == 200 and out.data.size == 5 and registry.put_content.call_args.args[1] == b"hello"

    promotion = AsyncMock()
    promotion.promote.return_value = _promotion({"schema_version": 1})
    out = await genome.promote_genome_revision("bot_1", PromoteRequest(revision="previous", expected_revision=R1, reason="back"), _request("POST"), "u1", "u1", _bot_service(), promotion)
    assert out.data.document == {"schema_version": 1} and out.data.status == "applied"
    assert promotion.promote.await_args.kwargs == {"revision": "previous", "ref": "active", "expected_revision": R1, "actor": OWNER, "reason": "back"}
    promotion.list_promotions = Mock(return_value=PromotionPage(1, (_promotion(),)))
    out = await genome.list_genome_promotions("bot_1", _request(), "u1", "u1", 1, 20, _bot_service(), promotion)
    assert out.data.items[0].document is None
    promotion.get_promotion = Mock(return_value=_promotion({"x": 1}))
    assert (await genome.get_genome_promotion("bot_1", "p" * 32, _request(), "u1", "u1", _bot_service(), promotion)).data.document == {"x": 1}


async def test_the_upload_cap_is_enforced_before_the_body_is_read_whole() -> None:
    with pytest.raises(LimitExceeded):
        await genome.read_bounded_body(_request("PUT", headers={"content-length": "11"}, body=b"x" * 11), 10)
    counted = _request("PUT", body=b"x" * 30)
    with pytest.raises(LimitExceeded):
        await genome.read_bounded_body(counted, 10)
    assert await genome.read_bounded_body(_request("PUT", body=b"x" * 10), 10) == b"x" * 10


# -- error envelopes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "data"),
    [
        (RevisionNotFound("r9"), 404, None),
        (ContentNotReachable("sha256:x"), 404, None),
        (RefConflict("draft", expected=None, current=R1), 409, {"ref": "draft", "expected_revision": None, "current_revision": R1}),
        (Drifted(active=R1, applied=None), 409, {"active": R1, "applied": None}),
        (PatchRejected([Reason("does_not_apply", "ops[0]", "no")]), 422, {"reasons": [{"code": "does_not_apply", "location": "ops[0]", "message": "no"}]}),
        (PolicyRejected([Reason("order", "policy.pins", "sorted")]), 422, {"reasons": [{"code": "order", "location": "policy.pins", "message": "sorted"}]}),
        (CaptureRefused([Reason("resource_path", "resources/how to.md", "x")]), 422, {"reasons": [{"code": "resource_path", "location": "resources/how to.md", "message": "x"}]}),
        (NotAppliable([Reason("not_appliable", "revision", "RSI-04")]), 422, {"reasons": [{"code": "not_appliable", "location": "revision", "message": "RSI-04"}]}),
        (NotPromotable("candidate"), 422, None),
        (PromotionUnsupported("service bot"), 422, None),
        (LimitExceeded("big"), 413, None),
        (MoverNotAllowed("promotion only"), 403, None),
    ],
)
def test_every_genome_error_maps_to_its_status_with_its_data(error, status, data) -> None:
    response = mapped_error_response(error, _request())
    assert response is not None and response.status_code == status
    body = json.loads(response.body)
    assert body.get("data") == data
    assert str(error) not in body["message"] or body["message"] == "Not found", "fixed messages, never exception text"


def test_policy_rejected_keeps_its_own_message() -> None:
    assert json.loads(mapped_error_response(PolicyRejected([]), _request()).body)["message"] == "Policy rejected"
    assert json.loads(mapped_error_response(PatchRejected([]), _request()).body)["message"] == "Patch rejected"


def test_bot_ref_reads_the_record_and_defaults() -> None:
    ref = genome.bot_ref({"entity_id": "e", "active_engine": "claude_code", "bot_type": "service"}, owner_id="o", bot_id="b")
    assert (ref.entity_id, ref.engine_type, ref.bot_type, ref.entity_type) == ("e", "claude_code", "service", "staff")
    assert isinstance(genome._promotion(_promotion()).document, type(None))
    assert genome.bot_ref({"entity_id": "e"}, owner_id="o", bot_id="b").bot_type == "personal"
    assert CompiledManifest("r", {}, "d").document == {}
