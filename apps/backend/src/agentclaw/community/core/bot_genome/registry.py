"""``GenomeRegistryService``: the registry Service API over the repositories and the content store.

What it ties together: capture (``capture/``), patch application, policy
and risk (``patch/``), the apply-time predicates (``spec_checks``), the
diff and the compiled document, the five ``ac_genome_*`` repositories, and
the manifest content store for every byte a revision names.

Recording order (plan): the shape and policy checks, apply, the spec
checks on the result, risk; only then are produced bytes filed and rows
written, so a refused patch leaves nothing behind.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.content import ContentScope, ManifestContentServiceProtocol
from agentclaw.community.core.bot_config_manifest.content.errors import ContentStoreError
from agentclaw.community.core.bot_config_manifest.fetch.limits import DIGEST_RE
from agentclaw.community.core.bot_genome import contract
from agentclaw.community.core.bot_genome.canonical import canonicalize, loads_strict, revision_id, sha256_digest
from agentclaw.community.core.bot_genome.capture import BotStateReader, capture_spec
from agentclaw.community.core.bot_genome.compile import compile_spec
from agentclaw.community.core.bot_genome.diff import diff_specs
from agentclaw.community.core.bot_genome.errors import (
    CaptureRefused,
    ContentNotReachable,
    LimitExceeded,
    MoverNotAllowed,
    PatchRejected,
    PolicyRejected,
    Reason,
    RefConflict,
    RevisionNotFound,
)
from agentclaw.community.core.bot_genome.genome_registry_protocol import GenomeRegistryProtocol
from agentclaw.community.core.bot_genome.limits import MAX_ENTRIES_PER_CATEGORY, MAX_UPLOAD_BYTES
from agentclaw.community.core.bot_genome.models import (
    FIXED_REFS,
    BotRef,
    CompiledManifest,
    ContentReader,
    ContentReceipt,
    GenomeDiff,
    GenomeRef,
    GenomeRevision,
    Provenance,
    ProvenanceKind,
    RecordedRevision,
    RefMover,
    RefUpdate,
    RevisionMetadata,
    RevisionPage,
    RevisionStatus,
    RunScope,
    parse_ref_name,
)
from agentclaw.community.core.bot_genome.patch import (
    apply_patch,
    evidence_reasons,
    patch_assessment,
    policy_reasons,
    shape_reasons,
)
from agentclaw.community.core.bot_genome.patch_models import GenomePatch
from agentclaw.community.core.bot_genome.repository.models import CandidateRefRequest, GenomePatchRow, GenomeRevisionRow
from agentclaw.community.core.bot_genome.spec_checks import SpecCheckContext, SpecChecks, policy_path_reasons
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy, GenomeSpec
from agentclaw.community.core.repository.protocols.bot.genome import (
    GenomePatchRepositoryProtocol,
    GenomePromotionRepositoryProtocol,
    GenomeRefRepositoryProtocol,
    GenomeRevisionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

__all__ = ["DEFAULT_POLICY", "GenomeRegistryService", "MOVERS"]

logger = get_logger()

#: README §5: the policy of a bot's first revision.
DEFAULT_POLICY = GenomePolicy(
    mutable_genes=["memory", "persona", "resources", "skills"],
    pins=["persona/SAFETY.md"],
    risk_overrides={},
)
#: 01-genome.md §5.1: the one mover of each ref kind.
MOVERS: dict[str, RefMover] = {
    "active": RefMover.PROMOTION,
    "previous": RefMover.PROMOTION,
    "canary": RefMover.PROMOTION,
    "draft": RefMover.OWNER,
    "candidate": RefMover.RUN,
}
_SEQ_REF = re.compile(r"^r([1-9][0-9]*)$")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


#: The producer whose provenance rows make a digest the registry's: captured, patch-written or uploaded bytes.
_PRODUCER = "genome"


class _BotContent(ContentReader):
    """The content store scoped to one bot: a digest is readable only when the registry filed a row for it for this bot."""

    def __init__(self, content: ManifestContentServiceProtocol, scope: ContentScope) -> None:
        self._content, self._scope = content, scope

    def read(self, digest: str) -> bytes:
        if not DIGEST_RE.match(digest) or not self._content.is_reachable(self._scope, digest, produced_by=_PRODUCER):
            raise ContentNotReachable(digest)
        try:
            return self._content.read(digest)
        except ContentStoreError as exc:
            raise ContentNotReachable(digest) from exc


class _PendingContent(ContentReader):
    """Bytes produced but not yet filed, in front of the bot's stored content."""

    def __init__(self, pending: dict[str, bytes], stored: ContentReader) -> None:
        self._pending, self._stored = pending, stored

    def read(self, digest: str) -> bytes:
        if digest in self._pending:
            return self._pending[digest]
        return self._stored.read(digest)


class GenomeRegistryService(GenomeRegistryProtocol):
    def __init__(
        self,
        *,
        revisions: GenomeRevisionRepositoryProtocol,
        refs: GenomeRefRepositoryProtocol,
        patches: GenomePatchRepositoryProtocol,
        promotions: GenomePromotionRepositoryProtocol,
        content: ManifestContentServiceProtocol,
        state_reader: Callable[[], BotStateReader],
        checks: SpecChecks,
        validator: SkillPackageValidator,
        applies: BotConfigManifestApplyServiceProtocol,
        env: Callable[[], str] = get_current_env,
    ) -> None:
        self._revisions = revisions
        self._refs = refs
        self._patches = patches
        self._promotions = promotions
        self._content = content
        self._state_reader = state_reader
        self._checks = checks
        self._validator = validator
        self._applies = applies
        self._env = env

    # -- plumbing -----------------------------------------------------------------------------

    def _axes(self, bot: BotRef) -> dict[str, str]:
        return {"env": self._env(), "entity_id": bot.entity_id, "bot_id": bot.bot_id}

    def _scope(self, bot: BotRef) -> ContentScope:
        return ContentScope(env=self._env(), entity_id=bot.entity_id, bot_id=bot.bot_id)

    def _reader(self, bot: BotRef) -> ContentReader:
        return _BotContent(self._content, self._scope(bot))

    def _ctx(self, bot: BotRef, actor: str) -> SpecCheckContext:
        return SpecCheckContext(engine_type=bot.engine_type, bot_type=bot.bot_type, owner_id=bot.owner_id, actor_id=actor)

    def _resolve(self, bot: BotRef, rev: str) -> GenomeRevisionRow:
        axes = self._axes(bot)
        row = None
        if (match := _SEQ_REF.match(rev)) is not None:
            row = self._revisions.get_by_seq(**axes, seq=int(match.group(1)))
        elif DIGEST_RE.match(rev):
            row = self._revisions.get(**axes, revision_id=rev)
        else:
            try:
                parse_ref_name(rev)
            except ValueError:
                raise RevisionNotFound(rev) from None
            ref = self._refs.get(**axes, name=rev)
            if ref is not None:
                row = self._revisions.get(**axes, revision_id=ref.revision_id)
        if row is None:
            raise RevisionNotFound(rev)
        return row

    @staticmethod
    def _revision(row: GenomeRevisionRow) -> GenomeRevision:
        hashed = loads_strict(row.content)
        if revision_id(hashed["spec"], hashed["policy"]) != row.revision_id:
            raise RuntimeError(f"stored revision {row.revision_id} fails its own id")
        return GenomeRevision(
            genome_schema=row.genome_schema,
            revision=_metadata(row),
            spec=GenomeSpec.from_json(hashed["spec"]),
            policy=GenomePolicy.from_json(hashed["policy"]),
        )

    def _active(self, bot: BotRef) -> GenomeRevisionRow | None:
        ref = self._refs.get(**self._axes(bot), name="active")
        if ref is None:
            return None
        row = self._revisions.get(**self._axes(bot), revision_id=ref.revision_id)
        if row is None:
            raise RuntimeError(f"ref 'active' of bot {bot.bot_id} points at {ref.revision_id}, which is not one of its revisions")
        return row

    def _record(
        self,
        bot: BotRef,
        spec: GenomeSpec,
        policy: GenomePolicy,
        *,
        parent: str | None,
        created_by: Provenance,
        status: RevisionStatus,
        writes: dict[str, bytes],
        origin: str,
        patch: GenomePatchRow | None = None,
        evidence: list[str] | None = None,
        risk_tier: str | None = None,
        flags: tuple[str, ...] = (),
        run: RunScope | None = None,
        parent_from_latest: bool = False,
    ) -> RecordedRevision:
        """File the produced bytes, then in one transaction the revision row (idempotent by id), its patch row and a run's candidate ref.

        Content goes first because it is content-addressed and idempotent:
        bytes filed for a revision that is then not recorded are the same
        as an upload nobody used. The three rows land together or not at all.
        """
        scope = self._scope(bot)
        for digest, data in writes.items():
            stored = self._content.store_produced(data, scope=scope, origin=origin, modifier=created_by.audit, category="genome")
            if stored.digest != digest:
                raise RuntimeError(f"produced bytes filed as {stored.digest}, expected {digest}")
        content = canonicalize({"policy": policy.to_json(), "spec": spec.to_json()}).decode("utf-8")
        candidate = None
        if run is not None:
            candidate = CandidateRefRequest(run_id=run.run_id, moved_kind=created_by.kind.value, moved_actor=created_by.audit, moved_at=_now())
        row, created, ref = self._revisions.record(
            GenomeRevisionRow(
                **self._axes(bot),
                revision_id=revision_id(spec, policy),
                parent_id=parent,
                genome_schema=1,
                status=status.value,
                created_kind=created_by.kind.value,
                created_actor=created_by.audit,
                run_id=created_by.run_id,
                run_step=created_by.step,
                patch_digest=patch.digest if patch is not None else None,
                evidence=json.dumps(evidence or [], ensure_ascii=False),
                content=content,
                created_at=_now(),
            ),
            patch=patch,
            candidate=candidate,
            parent_from_latest=parent_from_latest,
        )
        return RecordedRevision(revision=self._revision(row), created=created, risk_tier=risk_tier, flags=flags, ref=ref.name if ref is not None else None)

    # -- revisions ----------------------------------------------------------------------------

    def get_revision(self, bot: BotRef, rev: str) -> GenomeRevision:
        return self._revision(self._resolve(bot, rev))

    def list_revisions(
        self,
        bot: BotRef,
        *,
        status: RevisionStatus | None = None,
        parent: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> RevisionPage:
        total, rows = self._revisions.list(
            **self._axes(bot), status=None if status is None else status.value, parent_id=parent, page=page, page_size=page_size
        )
        return RevisionPage(total=total, items=tuple(_metadata(row) for row in rows))

    async def capture(self, bot: BotRef, *, created_by: Provenance) -> RecordedRevision:
        captured = await capture_spec(self._state_reader(), bot, validator=self._validator)
        active = self._active(bot)
        policy = GenomePolicy.from_json(loads_strict(active.content)["policy"]) if active is not None else DEFAULT_POLICY
        writes = dict(captured.writes)
        reasons = self._checks.reasons(captured.spec, policy, _PendingContent(writes, self._reader(bot)), self._ctx(bot, created_by.checked_as))
        if reasons:
            raise CaptureRefused(reasons)
        # The parent is `active`; while the bot has none yet, its latest revision, read by the
        # repository inside the recording transaction so that two overlapping first captures
        # cannot both come out parentless.
        return self._record(
            bot, captured.spec, policy, parent=active.revision_id if active is not None else None, created_by=created_by,
            status=RevisionStatus.DRAFT, writes=writes, origin="genome/capture", parent_from_latest=active is None,
        )

    async def record_patch(
        self,
        bot: BotRef,
        patch: GenomePatch,
        *,
        created_by: Provenance,
        status: RevisionStatus,
        run: RunScope | None = None,
    ) -> RecordedRevision:
        if status not in (RevisionStatus.DRAFT, RevisionStatus.CANDIDATE):
            raise ValueError(f"a recorded patch is draft or candidate, not {status.value}")
        if (status is RevisionStatus.CANDIDATE) != (run is not None):
            # A candidate is a run's proposal: the run bounds its genes and names its ref. A draft is the owner's.
            raise ValueError("a candidate is recorded with its run, a draft without one")
        patch_json = patch.to_json()
        reasons = shape_reasons(patch_json)
        if reasons:
            raise PatchRejected(reasons)
        base_row = self._revisions.get(**self._axes(bot), revision_id=patch.base)
        if base_row is None:
            raise RevisionNotFound(patch.base)
        base = self._revision(base_row)
        reasons = evidence_reasons(patch) + policy_reasons(
            patch.ops, base.policy, allowed_genes=run.allowed_genes if run is not None else None
        )
        if reasons:
            raise PatchRejected(reasons)
        stored = self._reader(bot)
        outcome = apply_patch(base.spec, patch.ops, stored)
        for category in ("persona", "skills", "resources"):
            if len(getattr(outcome.spec, category)) > MAX_ENTRIES_PER_CATEGORY:
                raise LimitExceeded(f"spec.{category} holds more than {MAX_ENTRIES_PER_CATEGORY} entries")
        writes = dict(outcome.writes)
        reasons = self._checks.reasons(outcome.spec, base.policy, _PendingContent(writes, stored), self._ctx(bot, created_by.checked_as))
        if reasons:
            raise PatchRejected(reasons)
        tier, flags = patch_assessment(outcome, base.policy)
        patch_bytes = canonicalize(patch_json)
        patch_row = GenomePatchRow(**self._axes(bot), digest=sha256_digest(patch_bytes), base_id=base.revision.id, content=patch_bytes.decode("utf-8"), created_at=_now())
        return self._record(
            bot, outcome.spec, base.policy, parent=base.revision.id, created_by=created_by, status=status,
            writes=writes, origin="genome/patch", patch=patch_row, evidence=list(patch.evidence),
            risk_tier=tier, flags=flags, run=run,
        )

    def set_policy(self, bot: BotRef, policy: GenomePolicy, *, created_by: Provenance) -> RecordedRevision:
        validator = contract.definition_validator(contract.REVISION_SCHEMA_FILE, "policy")
        reasons = [Reason("schema", "policy", error.message) for error in validator.iter_errors(policy.to_json())]
        if not reasons:
            reasons = policy_path_reasons(policy)
            for name in ("mutable_genes", "pins"):
                paths = getattr(policy, name)
                if paths != sorted(paths) or len(set(paths)) != len(paths):
                    reasons.append(Reason("order", f"policy.{name}", "sorted, unique gene paths"))
        if reasons:
            raise PolicyRejected(reasons)
        active = self._active(bot)
        if active is None:
            raise RevisionNotFound("active")
        spec = GenomeSpec.from_json(loads_strict(active.content)["spec"])
        # The spec is unchanged, but what apply would accept may have moved since `active` was
        # recorded (a revoked MCP permission, a retired server): a recorded revision is one that
        # applies now, so the content is re-checked in the recording bot's current context.
        reasons = self._checks.reasons(spec, policy, self._reader(bot), self._ctx(bot, created_by.checked_as))
        if reasons:
            raise PolicyRejected(reasons)
        return self._record(bot, spec, policy, parent=active.revision_id, created_by=created_by, status=RevisionStatus.DRAFT, writes={}, origin="genome/policy")

    def set_status(self, bot: BotRef, revision_id: str, status: RevisionStatus, *, actor: Provenance, reason: str) -> None:
        row = self._resolve(bot, revision_id)
        if self._revisions.update_metadata(**self._axes(bot), revision_id=row.revision_id, status=status.value) is None:
            raise RevisionNotFound(revision_id)
        logger.info("[genome.status] bot=%s revision=%s status=%s actor=%s reason=%s", bot.bot_id, row.revision_id, status.value, actor.actor, reason)

    def diff(self, bot: BotRef, rev: str, against: str) -> GenomeDiff:
        newer, older = self._revision(self._resolve(bot, rev)), self._revision(self._resolve(bot, against))
        return diff_specs(older.spec, newer.spec, self._reader(bot), revision=newer.revision.id, against=older.revision.id, policy=newer.policy)

    # -- refs ---------------------------------------------------------------------------------

    def list_refs(self, bot: BotRef) -> list[GenomeRef]:
        return [_ref(row) for row in self._refs.list(**self._axes(bot))]

    def move_ref(self, bot: BotRef, update: RefUpdate, *, actor: Provenance, mover: RefMover) -> GenomeRef:
        try:
            kind, _, _ = parse_ref_name(update.name)
        except ValueError as exc:
            raise MoverNotAllowed(str(exc)) from exc
        if MOVERS[kind] is not mover:
            raise MoverNotAllowed(f"{update.name!r} is moved by {MOVERS[kind].value}, not {mover.value}")
        if self._revisions.get(**self._axes(bot), revision_id=update.revision) is None:
            raise RevisionNotFound(update.revision)
        moved = self._refs.move(
            **self._axes(bot), name=update.name, revision_id=update.revision, expected_revision_id=update.expected_revision,
            moved_kind=actor.kind.value, moved_actor=actor.audit, run_id=actor.run_id, reason=update.reason, moved_at=_now(),
        )
        if moved is None:
            current = self._refs.get(**self._axes(bot), name=update.name)
            raise RefConflict(update.name, expected=update.expected_revision, current=current.revision_id if current else None)
        return _ref(moved)

    # -- content ------------------------------------------------------------------------------

    def read_content(self, bot: BotRef, digest: str) -> bytes:
        return self._reader(bot).read(digest)

    def put_content(self, bot: BotRef, data: bytes, *, provenance: Provenance) -> ContentReceipt:
        if len(data) > MAX_UPLOAD_BYTES:
            raise LimitExceeded(f"an upload is at most {MAX_UPLOAD_BYTES} bytes")
        scope = self._scope(bot)
        existed = self._content.is_reachable(scope, sha256_digest(data), produced_by=_PRODUCER)
        stored = self._content.store_produced(data, scope=scope, origin="genome/upload", modifier=provenance.audit, category="genome")
        return ContentReceipt(digest=stored.digest, size=len(data), created=not existed)

    # -- delivery -----------------------------------------------------------------------------

    def compile(self, bot: BotRef, rev: str) -> CompiledManifest:
        revision = self._revision(self._resolve(bot, rev))
        return compile_spec(revision.spec, self._reader(bot), revision_id=revision.revision.id)

    def applied_revision(self, bot: BotRef) -> str | None:
        report = self._applies.last_apply(entity_id=bot.entity_id, bot_id=bot.bot_id)
        if report is None or report.status is not ApplyStatus.SUCCEEDED:
            return None  # running, partial or failed: the bot does not run that revision (yet)
        promotion = self._promotions.by_apply_reference(**self._axes(bot), apply_reference=report.apply_id)
        return None if promotion is None else promotion.revision_id


# ---------------------------------------------------------------------------------------------


def _metadata(row: GenomeRevisionRow) -> RevisionMetadata:
    return RevisionMetadata(
        id=row.revision_id,
        seq=row.seq,
        parent=row.parent_id,
        created_by=Provenance(ProvenanceKind(row.created_kind), row.created_actor, run_id=row.run_id, step=row.run_step),
        created_at=row.created_at,
        status=RevisionStatus(row.status),
        patch_digest=row.patch_digest,
        evidence=tuple(json.loads(row.evidence)),
        evaluations=tuple(json.loads(row.evaluations)),
        annotations=dict(json.loads(row.annotations)),
    )


def _ref(row) -> GenomeRef:
    return GenomeRef(
        name=row.name,
        revision=row.revision_id,
        moved_at=row.moved_at,
        moved_by=Provenance(ProvenanceKind(row.moved_kind), row.moved_actor, run_id=row.run_id),
        reason=row.reason,
    )


assert set(MOVERS) == set(FIXED_REFS) | {"candidate"}
