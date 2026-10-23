"""``GenomeRegistryService``: the registry Service API over the repositories and the content store.

What it ties together: capture (``capture/``), patch application, policy
and risk (``patch/``), the apply-time predicates (``spec_checks``), the
diff and the compiled document, the five ``ac_genome_*`` repositories, and
the content store for every byte a revision names.

Recording order (plan): the shape and policy checks, apply, the spec
checks on the result, risk; only then are produced bytes filed and rows
written, so a refused patch leaves nothing behind.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.apply_engine.run.service_protocol import (
    ApplyRunServiceProtocol,
)
from agentclaw.community.core.content_store import DIGEST_RE, ContentScope, ContentStoreError, ContentStoreProtocol
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
from agentclaw.community.core.bot_genome.repository.models import (
    CandidateCollision,
    CandidateRefRequest,
    GenomePatchRow,
    GenomeRevisionRow,
)
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
    """The content store scoped to one bot.

    A digest is readable only when the registry filed a row for it for this bot.
    """

    def __init__(self, content: ContentStoreProtocol, scope: ContentScope) -> None:
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
        content: ContentStoreProtocol,
        state_reader: Callable[[], BotStateReader],
        checks: SpecChecks,
        validator: SkillPackageValidator,
        applies: ApplyRunServiceProtocol,
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
        return SpecCheckContext(
            engine_type=bot.engine_type, bot_type=bot.bot_type, owner_id=bot.owner_id, actor_id=actor
        )

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
            raise RuntimeError(
                f"ref 'active' of bot {bot.bot_id} points at {ref.revision_id}, which is not one of its revisions"
            )
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
        """File the produced bytes, then in one transaction the revision row, its patch row and a run's candidate ref.

        Content goes first because it is content-addressed and idempotent:
        bytes filed for a revision that is then not recorded are the same
        as an upload nobody used. The three rows land together or not at all.

        ``spec`` and ``policy`` are hashed into the revision id. ``parent`` is
        the id the new revision descends from (``patch.base`` for a patch,
        ``active`` for a capture or policy change; ``None`` for a bot's first
        revision, see ``parent_from_latest``). ``writes`` maps digest →
        bytes for every blob the spec names that is not in the content store
        yet, e.g. ``{"sha256:3f9a…": b"# Soul\\n…"}`` for a captured
        ``SOUL.md`` or an edited file's new text; it is empty for a policy
        change. ``origin`` tags the content rows with how the bytes came to
        be: ``genome/capture``, ``genome/patch`` or ``genome/policy``.
        ``patch`` is the stored patch row when the revision is the result of
        applying a patch (``commit_patch``), so that ``patch_digest`` names
        it; a capture or a policy change has none. ``evidence``,
        ``risk_tier`` and ``flags`` are likewise a patch's. ``run`` makes the
        revision a run's candidate and creates ``candidate/<run>/<n>``.
        """
        scope = self._scope(bot)
        # The spec names its bytes by digest; here they land in the content store (idempotent, by digest),
        # filed to this bot so that `read_content` can reach them, before the revision row is written.
        for digest, data in writes.items():
            stored = self._content.store_produced(
                data, scope=scope, origin=origin, modifier=created_by.audit, category="genome"
            )
            if stored.digest != digest:
                raise RuntimeError(f"produced bytes filed as {stored.digest}, expected {digest}")
        content = canonicalize({"policy": policy.to_json(), "spec": spec.to_json()}).decode("utf-8")
        candidate = None
        if run is not None:
            candidate = CandidateRefRequest(
                run_id=run.run_id, moved_kind=created_by.kind.value, moved_actor=created_by.audit, moved_at=_now()
            )
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
        return RecordedRevision(
            revision=self._revision(row),
            created=created,
            risk_tier=risk_tier,
            flags=flags,
            ref=ref.name if ref is not None else None,
        )

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
            **self._axes(bot),
            status=None if status is None else status.value,
            parent_id=parent,
            page=page,
            page_size=page_size,
        )
        return RevisionPage(total=total, items=tuple(_metadata(row) for row in rows))

    async def capture(self, bot: BotRef, *, created_by: Provenance) -> RecordedRevision:
        """Record what the bot runs right now as a draft revision (README §7).

        The bot's state is read through the platform services, normalised
        as the contract says and checked as a patch result would be; a bot
        the contract cannot represent is refused by name
        (``CaptureRefused``). The policy is ``active``'s, or the default
        while the bot has no ``active``. The parent is ``active``, or the
        bot's latest revision while it has none, so only a bot's very first
        revision is parentless. Re-capturing an unchanged bot returns the
        existing revision with ``created=False``.
        """
        captured = await capture_spec(self._state_reader(), bot, validator=self._validator)
        active = self._active(bot)
        policy = (
            GenomePolicy.from_json(loads_strict(active.content)["policy"]) if active is not None else DEFAULT_POLICY
        )
        writes = dict(captured.writes)
        reasons = self._checks.reasons(
            captured.spec, policy, _PendingContent(writes, self._reader(bot)), self._ctx(bot, created_by.checked_as)
        )
        if reasons:
            raise CaptureRefused(reasons)
        # The parent is `active`; while the bot has none yet, its latest revision, read by the
        # repository inside the recording transaction so that two overlapping first captures
        # cannot both come out parentless.
        return self._record(
            bot,
            captured.spec,
            policy,
            parent=active.revision_id if active is not None else None,
            created_by=created_by,
            status=RevisionStatus.DRAFT,
            writes=writes,
            origin="genome/capture",
            parent_from_latest=active is None,
        )

    async def commit_patch(
        self,
        bot: BotRef,
        patch: GenomePatch,
        *,
        created_by: Provenance,
        status: RevisionStatus,
        run: RunScope | None = None,
    ) -> RecordedRevision:
        """Apply ``patch`` to its base revision and record the result as a new revision (README §6.4).

        In order: the patch's shape (``shape_reasons``); the base,
        ``patch.base``, which must be one of this bot's revisions and becomes
        the new revision's parent; rationale and evidence; the base's policy
        and, for a run, its ``allowed_genes``; the ops themselves, one by
        one, each of which must apply exactly; the result against the limits
        and the apply-time predicates (what the engine would accept now);
        then the risk tier. Every refusal is a ``PatchRejected`` listing its
        reasons. The patch is stored by its digest (the candidate id) with the
        revision, in one transaction. ``status`` is ``DRAFT`` for an owner's
        patch and ``CANDIDATE`` for a run's, which also creates
        ``candidate/<run>/<n>``; a candidate whose result already exists as
        a revision is refused as ``already_recorded``, unless this very run
        recorded it (a retry returns it with ``created=False``).
        """
        if status not in (RevisionStatus.DRAFT, RevisionStatus.CANDIDATE):
            raise ValueError(f"a recorded patch is draft or candidate, not {status.value}")
        if (status is RevisionStatus.CANDIDATE) != (run is not None):
            # A candidate is a run's proposal: the run bounds its genes and names its ref. A draft is the owner's.
            raise ValueError("a candidate is recorded with its run, a draft without one")
        if run is not None:
            # The revision's provenance and the candidate ref must name the same run, else the audit trail
            # attributes `candidate/<run>/<n>` to another run or to a user. (`step` is the strategy's own
            # label for the step, not an identity.)
            expected = (ProvenanceKind.STRATEGY_RUN, run.strategy, run.run_id)
            if (created_by.kind, created_by.actor, created_by.run_id) != expected:
                raise ValueError("a candidate's provenance is its run's: strategy_run, the run's strategy and run_id")
        elif created_by.kind is not ProvenanceKind.USER:
            raise ValueError("a draft is recorded by a user")
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
        reasons = self._checks.reasons(
            outcome.spec, base.policy, _PendingContent(writes, stored), self._ctx(bot, created_by.checked_as)
        )
        if reasons:
            raise PatchRejected(reasons)
        tier, flags = patch_assessment(outcome, base.policy)
        patch_bytes = canonicalize(patch_json)
        patch_row = GenomePatchRow(
            **self._axes(bot),
            digest=sha256_digest(patch_bytes),
            base_id=base.revision.id,
            content=patch_bytes.decode("utf-8"),
            created_at=_now(),
        )
        if run is not None:
            # A candidate is new content: a revision that already exists was recorded by another run,
            # as an owner's draft, from another patch or from a capture, and its status, provenance or
            # patch_digest are not this candidate's. The one exception is a retry: the run that first
            # recorded it (its own `candidate/<run>/<n>` points at the revision) resubmitting the very
            # same patch. This read gives the clear answer; the recording transaction repeats the check,
            # so a record of the same content committing in between is refused the same way.
            existing = self._revisions.get(**self._axes(bot), revision_id=revision_id(outcome.spec, base.policy))
            if existing is not None and not (
                existing.patch_digest == patch_row.digest
                and any(
                    ref.revision_id == existing.revision_id
                    for ref in self._refs.list(**self._axes(bot), name_prefix=f"candidate/{run.run_id}/")
                )
            ):
                raise PatchRejected(
                    [_already_recorded(existing.revision_id, existing.seq, existing.patch_digest, patch_row.digest)]
                )
        try:
            return self._record(
                bot,
                outcome.spec,
                base.policy,
                parent=base.revision.id,
                created_by=created_by,
                status=status,
                writes=writes,
                origin="genome/patch",
                patch=patch_row,
                evidence=list(patch.evidence),
                risk_tier=tier,
                flags=flags,
                run=run,
            )
        except CandidateCollision as exc:
            raise PatchRejected(
                [_already_recorded(exc.revision_id, exc.seq, exc.patch_digest, patch_row.digest)]
            ) from exc

    def set_policy(self, bot: BotRef, policy: GenomePolicy, *, created_by: Provenance) -> RecordedRevision:
        """Record a new draft revision with ``active``'s spec and this policy (README §5).

        Revisions never change, ``active``'s included: the policy is part of
        what the revision id hashes, so a new policy is a new revision whose
        parent is ``active``. The bot keeps running ``active`` until the new
        revision is promoted. The policy is checked for shape and gene-path
        grammar, and the unchanged spec is re-checked against what apply
        accepts now (``PolicyRejected`` either way). An owner's operation:
        a ``strategy_run`` provenance is a programming error (``ValueError``).
        """
        if created_by.kind is not ProvenanceKind.USER:
            # The policy is what bounds automation; only an owner (or an admitted application acting for
            # one) changes it. A run that could loosen `mutable_genes` or drop a pin would gate itself.
            raise ValueError("the policy is changed by a user, never by a strategy run")
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
        return self._record(
            bot,
            spec,
            policy,
            parent=active.revision_id,
            created_by=created_by,
            status=RevisionStatus.DRAFT,
            writes={},
            origin="genome/policy",
        )

    def set_status(
        self, bot: BotRef, revision_id: str, status: RevisionStatus, *, actor: Provenance, reason: str
    ) -> None:
        row = self._resolve(bot, revision_id)
        if self._revisions.update_metadata(**self._axes(bot), revision_id=row.revision_id, status=status.value) is None:
            raise RevisionNotFound(revision_id)
        logger.info(
            "[genome.status] bot=%s revision=%s status=%s actor=%s reason=%s",
            bot.bot_id,
            row.revision_id,
            status.value,
            actor.actor,
            reason,
        )

    def diff(self, bot: BotRef, rev: str, against: str) -> GenomeDiff:
        newer, older = self._revision(self._resolve(bot, rev)), self._revision(self._resolve(bot, against))
        return diff_specs(
            older.spec,
            newer.spec,
            self._reader(bot),
            revision=newer.revision.id,
            against=older.revision.id,
            policy=newer.policy,
        )

    # -- refs ---------------------------------------------------------------------------------

    def list_refs(self, bot: BotRef) -> list[GenomeRef]:
        return [_ref(row) for row in self._refs.list(**self._axes(bot))]

    def move_ref(self, bot: BotRef, update: RefUpdate, *, actor: Provenance, mover: RefMover) -> GenomeRef:
        """Point a ref at a revision by compare-and-swap, logging the move (README §8).

        ``update.revision`` is the revision id the ref points at afterwards
        (it must be one of this bot's); ``update.expected_revision`` is where
        the caller believes the ref points now, ``None`` if it should not
        exist yet. The store moves the ref only if that is still true, so two
        callers racing for one ref cannot both win: the loser gets
        ``RefConflict`` with the ref's current revision. ``mover`` must be the
        ref's designated mover (``active`` / ``previous`` by promotion, ``draft``
        by the owner, candidates by the registry), else ``MoverNotAllowed``.
        """
        try:
            kind, _, _ = parse_ref_name(update.name)
        except ValueError as exc:
            raise MoverNotAllowed(str(exc)) from exc
        if MOVERS[kind] is not mover:
            raise MoverNotAllowed(f"{update.name!r} is moved by {MOVERS[kind].value}, not {mover.value}")
        if self._revisions.get(**self._axes(bot), revision_id=update.revision) is None:
            raise RevisionNotFound(update.revision)
        moved = self._refs.move(
            **self._axes(bot),
            name=update.name,
            revision_id=update.revision,
            expected_revision_id=update.expected_revision,
            moved_kind=actor.kind.value,
            moved_actor=actor.audit,
            run_id=actor.run_id,
            reason=update.reason,
            moved_at=_now(),
        )
        if moved is None:
            current = self._refs.get(**self._axes(bot), name=update.name)
            raise RefConflict(
                update.name, expected=update.expected_revision, current=current.revision_id if current else None
            )
        return _ref(moved)

    # -- content ------------------------------------------------------------------------------

    def read_content(self, bot: BotRef, digest: str) -> bytes:
        return self._reader(bot).read(digest)

    def put_content(self, bot: BotRef, data: bytes, *, provenance: Provenance) -> ContentReceipt:
        if len(data) > MAX_UPLOAD_BYTES:
            raise LimitExceeded(f"an upload is at most {MAX_UPLOAD_BYTES} bytes")
        scope = self._scope(bot)
        existed = self._content.is_reachable(scope, sha256_digest(data), produced_by=_PRODUCER)
        stored = self._content.store_produced(
            data, scope=scope, origin="genome/upload", modifier=provenance.audit, category="genome"
        )
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


def _already_recorded(revision_id: str, seq: int, patch_digest: str | None, this_patch: str) -> Reason:
    if patch_digest is None:
        origin = "the bot's own state"
    elif patch_digest == this_patch:
        origin = "this same patch, by another run or as a draft"
    else:
        origin = "another patch"
    return Reason(
        "already_recorded",
        "patch",
        f"the result is revision {revision_id} (r{seq}), recorded earlier from {origin}; "
        "a candidate is new content, promote that revision instead",
    )


def _metadata(row: GenomeRevisionRow) -> RevisionMetadata:
    return RevisionMetadata(
        id=row.revision_id,
        seq=row.seq,
        parent=row.parent_id,
        created_by=Provenance(
            ProvenanceKind(row.created_kind), row.created_actor, run_id=row.run_id, step=row.run_step
        ),
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
