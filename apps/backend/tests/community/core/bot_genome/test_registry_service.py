"""``GenomeRegistryService`` end to end over SQLite and a tmp content store: capture, patches, refs, content, compile."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.content import ContentScope
from agentclaw.community.core.bot_genome.canonical import canonicalize, sha256_digest
from agentclaw.community.core.bot_genome.errors import (
    CaptureRefused,
    ContentNotReachable,
    LimitExceeded,
    MoverNotAllowed,
    PatchRejected,
    PolicyRejected,
    RefConflict,
    RevisionNotFound,
)
from agentclaw.community.core.bot_genome.models import BotRef, Provenance, ProvenanceKind, RefMover, RefUpdate, RevisionStatus, RunScope
from agentclaw.community.core.bot_genome.patch_models import GenomePatch, parse_ops
from agentclaw.community.core.bot_genome.registry import DEFAULT_POLICY
from agentclaw.community.core.bot_genome.repository.models import GenomePromotionRow
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy
from tests.community.core.bot_genome.example_blobs import (
    MEMORY_BOT,
    SUPPORT_BOT,
    SUPPORT_BOT_CANDIDATE,
    BlobDir,
    load_patch,
    load_patch_json,
    load_revisions,
    spec_of,
)
from tests.community.core.bot_genome.service_fixtures import BOT, OTHER_BOT, OWNER, RUN, World
from tests.community.core.bot_genome.test_capture import FakeReader
from tests.community.core.bot_config_manifest.content.test_content_store import _fetched

REVISIONS = load_revisions()
SUPPORT_SPEC = spec_of(SUPPORT_BOT)


@pytest.fixture
def world(tmp_path) -> World:
    w = World(tmp_path)
    w.reader = FakeReader.from_spec(SUPPORT_SPEC)
    return w


async def _capture_support_bot(world: World):
    recorded = await world.registry.capture(BOT, created_by=OWNER)
    assert recorded.revision.revision.id == SUPPORT_BOT, "the captured support bot is the contract's example revision"
    return recorded


# -- capture ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_records_the_first_revision_with_the_default_policy_and_files_every_blob(world: World) -> None:
    recorded = await _capture_support_bot(world)
    meta = recorded.revision.revision
    assert (recorded.created, meta.seq, meta.parent, meta.status, meta.patch_digest) == (True, 1, None, RevisionStatus.DRAFT, None)
    assert recorded.revision.policy == DEFAULT_POLICY and recorded.risk_tier is None and recorded.ref is None
    for entry in SUPPORT_SPEC.persona + SUPPORT_SPEC.resources:
        assert world.registry.read_content(BOT, entry.digest) == BlobDir().read(entry.digest)
    assert world.registry.get_revision(BOT, "r1").spec == SUPPORT_SPEC
    again = await world.registry.capture(BOT, created_by=OWNER)
    assert (again.created, again.revision.revision.seq) == (False, 1)
    assert world.registry.list_refs(BOT) == [], "capture names no ref"


@pytest.mark.asyncio
async def test_capture_takes_the_parent_and_policy_from_active(world: World) -> None:
    first = await _capture_support_bot(world)
    policy = DEFAULT_POLICY.model_copy(update={"pins": ["persona/SAFETY.md", "skills/refund-policy"]})
    world.refs.move(env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, name="active", revision_id=first.revision.revision.id, expected_revision_id=None, moved_kind="user", moved_actor="u", run_id=None, reason=None, moved_at=first.revision.revision.created_at)
    repolicied = world.registry.set_policy(BOT, policy, created_by=OWNER)
    assert repolicied.created and repolicied.revision.revision.parent == first.revision.revision.id and repolicied.revision.spec == SUPPORT_SPEC
    world.registry.move_ref(BOT, RefUpdate("active", repolicied.revision.revision.id, first.revision.revision.id), actor=OWNER, mover=RefMover.PROMOTION)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT_CANDIDATE))
    captured = await world.registry.capture(BOT, created_by=OWNER)
    assert captured.revision.policy == policy and captured.revision.revision.parent == repolicied.revision.revision.id
    assert captured.revision.spec == spec_of(SUPPORT_BOT_CANDIDATE)


@pytest.mark.asyncio
async def test_capture_refuses_by_name(world: World) -> None:
    world.reader = FakeReader(refuse="platform-managed teclaw")
    with pytest.raises(CaptureRefused):
        await world.registry.capture(BOT, created_by=OWNER)
    world.reader = FakeReader.from_spec(SUPPORT_SPEC)
    world.auth.permitted = False
    with pytest.raises(CaptureRefused) as caught:
        await world.registry.capture(BOT, created_by=OWNER)
    assert {r.code for r in caught.value.reasons} == {"mcp_not_permitted"}
    assert world.registry.list_revisions(BOT).total == 0
    soul = next(e.digest for e in SUPPORT_SPEC.persona if e.type == "SOUL.md")
    with pytest.raises(ContentNotReachable):
        world.registry.read_content(BOT, soul)  # a refused capture files no bytes for the bot


# -- patches ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_patch_reproduces_the_contract_candidate_and_is_idempotent(world: World) -> None:
    await _capture_support_bot(world)
    patch = load_patch("00-mixed-r1-to-r2.json")
    upload = world.registry.put_content(BOT, BlobDir().read("sha256:a191ca8f49999530a2cf4f48562ffde7d62011b3bcfe040195da089a3f117c95"), provenance=OWNER)
    assert upload.created and upload.digest == "sha256:a191ca8f49999530a2cf4f48562ffde7d62011b3bcfe040195da089a3f117c95"
    run = RunScope(run_id="run_7f3", strategy="clawevolve/bot-evolution@2.0.0", allowed_genes=("persona", "resources"))
    recorded = await world.registry.record_patch(BOT, patch, created_by=RUN, status=RevisionStatus.CANDIDATE, run=run)
    meta = recorded.revision.revision
    assert meta.id == SUPPORT_BOT_CANDIDATE and meta.seq == 2 and meta.parent == SUPPORT_BOT
    assert meta.patch_digest == sha256_digest(canonicalize(patch.to_json())) == REVISIONS[SUPPORT_BOT_CANDIDATE]["revision"]["patch_digest"]
    assert (meta.status, meta.evidence, meta.created_by.kind, meta.created_by.run_id) == (RevisionStatus.CANDIDATE, ("episode:ep_91", "feedback:fb_311"), ProvenanceKind.STRATEGY_RUN, "run_7f3")
    assert (recorded.created, recorded.risk_tier, recorded.flags, recorded.ref) == (True, "T2", (), "candidate/run_7f3/1")
    assert world.registry.get_revision(BOT, "candidate/run_7f3/1").revision.id == SUPPORT_BOT_CANDIDATE
    assert world.patches.get(env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, digest=meta.patch_digest).base_id == SUPPORT_BOT
    again = await world.registry.record_patch(BOT, patch, created_by=RUN, status=RevisionStatus.CANDIDATE, run=run)
    assert (again.created, again.revision.revision.seq, again.ref) == (False, 2, "candidate/run_7f3/1")
    second = await world.registry.record_patch(BOT, load_patch("01-file-edit.json"), created_by=RUN, status=RevisionStatus.CANDIDATE, run=RunScope("run_7f3", "clawevolve/bot-evolution@2.0.0", ("persona", "skills", "resources")))
    assert second.ref == "candidate/run_7f3/2" and second.revision.revision.seq == 3
    page = world.registry.list_revisions(BOT, status=RevisionStatus.CANDIDATE, page=1, page_size=1)
    assert page.total == 2 and page.items[0].seq == 3


@pytest.mark.asyncio
async def test_record_patch_refusals_leave_nothing_behind(world: World) -> None:
    await _capture_support_bot(world)
    patch = load_patch("00-mixed-r1-to-r2.json")
    with pytest.raises(RevisionNotFound):
        await world.registry.record_patch(BOT, patch.model_copy(update={"base": "sha256:" + "0" * 64}), created_by=OWNER, status=RevisionStatus.DRAFT)
    with pytest.raises(ValidationError):  # the shape is the model's: a blank rationale never reaches the registry
        GenomePatch.from_json(dict(load_patch_json("00-mixed-r1-to-r2.json"), rationale=""))
    with pytest.raises(PatchRejected) as caught:
        await world.registry.record_patch(BOT, patch, created_by=RUN, status=RevisionStatus.CANDIDATE, run=RunScope("run_1", "clawevolve/bot-evolution@2.0.0", ("skills",)))
    assert {r.code for r in caught.value.reasons} == {"gene_not_allowed"}
    with pytest.raises(ContentNotReachable):  # the resource.add names bytes nobody uploaded for this bot
        await world.registry.record_patch(BOT, patch, created_by=OWNER, status=RevisionStatus.DRAFT)
    bad = load_patch("08-resource-add.json").model_copy(update={"ops": parse_ops([{"op": "resource.add", "path": "data/tool.sh", "content": "#!/bin/sh\n"}])})
    with pytest.raises(PatchRejected) as caught:
        await world.registry.record_patch(BOT, bad, created_by=OWNER, status=RevisionStatus.DRAFT)
    assert [r.code for r in caught.value.reasons] == ["admission"]
    with pytest.raises(ValueError):
        await world.registry.record_patch(BOT, patch, created_by=OWNER, status=RevisionStatus.PROMOTED)
    with pytest.raises(ValueError):  # a candidate is a run's proposal: no run, no candidate
        await world.registry.record_patch(BOT, patch, created_by=OWNER, status=RevisionStatus.CANDIDATE)
    with pytest.raises(ValueError):  # and a draft is the owner's: a run has no business on it
        await world.registry.record_patch(BOT, patch, created_by=RUN, status=RevisionStatus.DRAFT, run=RunScope("run_1", "clawevolve/bot-evolution@2.0.0", ("persona",)))
    with pytest.raises(ValueError):  # a run id is one ref segment, or candidate/<run>/<n> cannot be parsed back
        RunScope("a/b", "clawevolve/bot-evolution@2.0.0", ("persona",))
    with pytest.raises(ValueError):  # `$` would let a trailing newline through; the segment must match whole
        RunScope("run_1\n", "clawevolve/bot-evolution@2.0.0", ("persona",))
    for dots in (".", ".."):
        with pytest.raises(ValueError):  # a URL resolver folds these away, so candidate/<run>/<n> would not survive a request
            RunScope(dots, "clawevolve/bot-evolution@2.0.0", ("persona",))
    assert RunScope("..a", "clawevolve/bot-evolution@2.0.0", ("persona",)).run_id == "..a"  # a plain segment to a resolver
    with pytest.raises(ValueError):  # the actor the contract records is the strategy's id@version
        RunScope("run_1", "bot-evolution", ("persona",))
    assert Provenance(ProvenanceKind.STRATEGY_RUN, "s@1", run_id="run_1", principal="user_owner_5").checked_as == "user_owner_5"
    assert world.registry.list_revisions(BOT).total == 1
    assert world.registry.list_refs(BOT) == []


@pytest.mark.asyncio
async def test_a_patch_on_another_bots_revision_is_not_found(world: World) -> None:
    await _capture_support_bot(world)
    with pytest.raises(RevisionNotFound):
        await world.registry.record_patch(OTHER_BOT, load_patch("00-mixed-r1-to-r2.json"), created_by=OWNER, status=RevisionStatus.DRAFT)
    with pytest.raises(RevisionNotFound):
        world.registry.get_revision(OTHER_BOT, SUPPORT_BOT)


# -- policy, status, lookups -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_policy_needs_active_and_a_canonical_sorted_policy(world: World) -> None:
    first = await _capture_support_bot(world)
    with pytest.raises(RevisionNotFound):
        world.registry.set_policy(BOT, DEFAULT_POLICY, created_by=OWNER)
    world.registry.move_ref(BOT, RefUpdate("active", first.revision.revision.id, None), actor=OWNER, mover=RefMover.PROMOTION)
    same = world.registry.set_policy(BOT, DEFAULT_POLICY, created_by=OWNER)
    assert not same.created and same.revision.revision.id == first.revision.revision.id
    for bad in ({"mutable_genes": ["skills", "persona"], "pins": [], "risk_overrides": {}}, {"mutable_genes": ["skills/%61"], "pins": [], "risk_overrides": {}}):
        with pytest.raises(PolicyRejected):  # order and canonical spelling: the registry's rules
            world.registry.set_policy(BOT, GenomePolicy.from_json(bad), created_by=OWNER)
    with pytest.raises(ValidationError):  # a missing field is the model's refusal
        GenomePolicy.from_json({"mutable_genes": [], "pins": []})
    # The spec is re-checked in the bot's current context: what applied when `active` was recorded
    # may not apply now (here the engine no longer accepts the persona files).
    moved_engine = BotRef(entity_id=BOT.entity_id, bot_id=BOT.bot_id, engine_type="claude_code", bot_type=BOT.bot_type)
    with pytest.raises(PolicyRejected) as caught:
        world.registry.set_policy(moved_engine, DEFAULT_POLICY.model_copy(update={"pins": []}), created_by=OWNER)
    assert {r.code for r in caught.value.reasons} == {"persona_type"}
    assert world.registry.list_revisions(BOT).total == 1, "nothing recorded"


@pytest.mark.asyncio
async def test_set_status_and_lookups(world: World) -> None:
    first = await _capture_support_bot(world)
    rid = first.revision.revision.id
    world.registry.set_status(BOT, "r1", RevisionStatus.ARCHIVED, actor=OWNER, reason="old")
    assert world.registry.get_revision(BOT, rid).revision.status == RevisionStatus.ARCHIVED
    assert world.registry.get_revision(BOT, rid).spec == SUPPORT_SPEC
    for missing in ("r2", "sha256:" + "0" * 64, "draft", "nope", "candidate/x/0"):
        with pytest.raises(RevisionNotFound):
            world.registry.get_revision(BOT, missing)
    with pytest.raises(RevisionNotFound):
        world.registry.set_status(BOT, "r9", RevisionStatus.ARCHIVED, actor=OWNER, reason="x")


@pytest.mark.asyncio
async def test_audit_columns_name_the_application_acting_for_the_user(world: World) -> None:
    delegated = Provenance(ProvenanceKind.USER, OWNER.actor, recorded_as=f"app:7:on-behalf-of:{OWNER.actor}")
    recorded = await world.registry.capture(BOT, created_by=delegated)
    assert recorded.revision.revision.created_by.actor == delegated.audit, "the row says who really recorded it"
    moved = world.registry.move_ref(BOT, RefUpdate("draft", recorded.revision.revision.id, None), actor=delegated, mover=RefMover.OWNER)
    assert moved.moved_by.actor == delegated.audit


# -- refs ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_ref_enforces_the_mover_table_and_compare_and_swap(world: World) -> None:
    first = await _capture_support_bot(world)
    rid = first.revision.revision.id
    with pytest.raises(MoverNotAllowed):
        world.registry.move_ref(BOT, RefUpdate("active", rid, None), actor=OWNER, mover=RefMover.OWNER)
    with pytest.raises(MoverNotAllowed):
        world.registry.move_ref(BOT, RefUpdate("candidate/run_1/1", rid, None), actor=OWNER, mover=RefMover.OWNER)
    with pytest.raises(MoverNotAllowed):
        world.registry.move_ref(BOT, RefUpdate("inbox/1", rid, None), actor=OWNER, mover=RefMover.OWNER)
    with pytest.raises(RevisionNotFound):
        world.registry.move_ref(BOT, RefUpdate("draft", "sha256:" + "0" * 64, None), actor=OWNER, mover=RefMover.OWNER)
    draft = world.registry.move_ref(BOT, RefUpdate("draft", rid, None, reason="wip"), actor=OWNER, mover=RefMover.OWNER)
    assert (draft.name, draft.revision, draft.reason, draft.moved_by.actor) == ("draft", rid, "wip", "user_owner_5")
    with pytest.raises(RefConflict) as caught:
        world.registry.move_ref(BOT, RefUpdate("draft", rid, None), actor=OWNER, mover=RefMover.OWNER)
    assert (caught.value.ref, caught.value.expected, caught.value.current) == ("draft", None, rid)
    assert [r.name for r in world.registry.list_refs(BOT)] == ["draft"]
    assert world.registry.list_refs(OTHER_BOT) == []


# -- content ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_is_reachable_per_bot_and_uploads_are_bounded(world: World) -> None:
    await _capture_support_bot(world)
    soul = next(e.digest for e in SUPPORT_SPEC.persona if e.type == "SOUL.md")
    assert world.registry.read_content(BOT, soul).startswith(b"# Soul")
    with pytest.raises(ContentNotReachable):
        world.registry.read_content(OTHER_BOT, soul)
    with pytest.raises(ContentNotReachable):
        world.registry.read_content(BOT, "sha256:" + "1" * 64)
    fetched = b"fetched by a Manifest apply, never captured or uploaded"
    world.content.store(_fetched(fetched), scope=ContentScope(env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id), source_url="https://code.example.com/x.git")
    with pytest.raises(ContentNotReachable):  # the bot holds the bytes, but no revision or upload of its own names them
        world.registry.read_content(BOT, sha256_digest(fetched))
    with pytest.raises(ContentNotReachable):
        world.registry.read_content(BOT, "not-a-digest")
    receipt = world.registry.put_content(OTHER_BOT, b"hello", provenance=OWNER)
    assert (receipt.digest, receipt.size, receipt.created) == (sha256_digest(b"hello"), 5, True)
    assert world.registry.put_content(OTHER_BOT, b"hello", provenance=OWNER).created is False
    assert world.registry.read_content(OTHER_BOT, receipt.digest) == b"hello"
    with pytest.raises(ContentNotReachable):
        world.registry.read_content(BOT, receipt.digest)
    with pytest.raises(LimitExceeded):
        world.registry.put_content(BOT, b"x" * (200 * 1024 * 1024 + 1), provenance=OWNER)


# -- diff, compile, applied revision ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_compile_and_applied_revision(world: World) -> None:
    first = await _capture_support_bot(world)
    world.registry.put_content(BOT, BlobDir().read("sha256:a191ca8f49999530a2cf4f48562ffde7d62011b3bcfe040195da089a3f117c95"), provenance=OWNER)
    second = await world.registry.record_patch(BOT, load_patch("00-mixed-r1-to-r2.json"), created_by=OWNER, status=RevisionStatus.DRAFT)
    diff = world.registry.diff(BOT, "r2", "r1")
    assert (diff.revision, diff.against, diff.risk_tier) == (SUPPORT_BOT_CANDIDATE, SUPPORT_BOT, "T2")
    assert [(c.gene, c.kind) for c in diff.changes] == [("persona/SOUL.md", "modified"), ("resources/data/returns.md", "added")]
    compiled = world.registry.compile(BOT, "r2")
    assert compiled.revision_id == SUPPORT_BOT_CANDIDATE and compiled.document["manifest"]["resources"][-1]["path"] == "data/returns.md"
    assert compiled.document["script"]["body"].startswith("#!/")
    assert world.registry.applied_revision(BOT) is None
    world.report(BOT, "apply_9")
    assert world.registry.applied_revision(BOT) is None, "an apply nobody promoted"
    world.promotions.insert(GenomePromotionRow(
        env="dev", entity_id=BOT.entity_id, bot_id=BOT.bot_id, promotion_id="p" * 32, ref="active", revision_id=second.revision.revision.id,
        from_revision_id=first.revision.revision.id, document_digest=compiled.document_digest, document_json=canonicalize(compiled.document).decode(),
        actor_kind="user", actor_id="u", reason="ship", status="applied", apply_kind="pending", apply_reference="apply_9", created_at=first.revision.revision.created_at,
    ))
    assert world.registry.applied_revision(BOT) == SUPPORT_BOT_CANDIDATE
    for not_done in (ApplyStatus.RUNNING, ApplyStatus.PARTIAL, ApplyStatus.FAILED):
        world.report(BOT, "apply_9", status=not_done)
        assert world.registry.applied_revision(BOT) is None, f"a {not_done.value} apply is not what the bot runs"


@pytest.mark.asyncio
async def test_memory_bot_patches_work_end_to_end(world: World) -> None:
    world.reader = FakeReader.from_spec(spec_of(MEMORY_BOT))
    world.reader._mcp = ()
    captured = await world.registry.capture(BOT, created_by=OWNER)
    assert captured.revision.spec.memory is None, "capture has no curated memory until RSI-05"
    items = json.loads(BlobDir().read(spec_of(MEMORY_BOT).memory.items_digest))
    assert items[0]["key"] == "customer-tier-rules"
