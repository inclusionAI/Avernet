"""Values the Genome Registry and promotion pass around.

``spec`` and ``policy`` are the contract's typed records (``spec_models.py``);
the dataclasses here carry what the registry adds around them. Everything
is frozen: a revision never changes, and a ref move returns a new
``GenomeRef``.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import datetime
from abc import abstractmethod
from enum import StrEnum
from typing import Any, NamedTuple, Protocol

from agentclaw.community.core.bot_genome.errors import LimitExceeded
from agentclaw.community.core.bot_genome.limits import MAX_AUDIT_ACTOR_CHARS
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy, GenomeSpec


class ContentReader(Protocol):
    """How patch application, checks, diff and compile read the bytes a digest names.

    The registry binds it to the manifest content store scoped to the bot;
    tests bind it to a directory of blobs. ``read`` raises
    :class:`~agentclaw.community.core.bot_genome.errors.ContentNotReachable`
    for a digest the bot cannot reach.
    """

    @abstractmethod
    def read(self, digest: str) -> bytes: ...


class RevisionStatus(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    ARCHIVED = "archived"


class ProvenanceKind(StrEnum):
    USER = "user"
    STRATEGY_RUN = "strategy_run"


class RefMover(StrEnum):
    """Who is moving a ref; each ref kind has exactly one allowed mover (01-genome.md §5.1).

    ``OWNER`` moves ``draft``; ``PROMOTION`` moves ``active``, ``previous`` and
    ``canary``; ``RUN`` is the registry itself creating ``candidate/<run_id>/<n>``
    when an evolution run's candidate is recorded (a candidate ref is created
    once and never moved).
    """

    OWNER = "owner"
    PROMOTION = "promotion"
    RUN = "run"


#: The four fixed ref names every bot can have; ``candidate/<run_id>/<n>`` is the only other shape.
FIXED_REFS = ("active", "previous", "canary", "draft")
PROMOTION_REFS = ("active", "previous", "canary")


@dataclass(frozen=True)
class BotRef:
    """The bot a registry call is about, with the facts every check needs.

    ``entity_id`` + ``bot_id`` identifies the bot (within an env and tenant)
    and is the key every genome table is scoped by. ``owner_id`` is not part
    of that identity: it is the bot's owner as the bot record says
    (``ac_bots.owner_id``), carried here because the platform services
    capture and the spec checks read through (identity files, skills,
    resources, MCP overrides) scope by the owner's user id, and because the
    contract's ``revision.bot`` names the owner. The caller resolves both
    from the bot record; the registry never derives one from the other.
    """

    entity_id: str  #: with ``bot_id``, the bot's identity and the genome tables' key
    bot_id: str  #: the bot's id
    owner_id: str  #: the owner's user id from the bot record; what the platform services scope by
    engine_type: str  #: the engine the bot runs on (``openclaw``, …): a capability axis of the checks
    bot_type: str  #: ``personal`` / ``arca`` / ``desktop`` / ``service`` …: the other capability axis
    entity_type: str = "staff"  #: ``staff`` or ``user``: the identity service's third coordinate for the bot's files


@dataclass(frozen=True)
class Provenance:
    """Who recorded a revision or moved a ref (contract ``created_by`` / ``moved_by``).

    ``actor`` is what the contract names: a user id, or for a ``strategy_run``
    the strategy's ``id@version``. The apply-time permission checks are made
    against a user, so a strategy's record also carries the ``principal``
    the run acts for; a user's record checks as the user.
    """

    kind: ProvenanceKind
    actor: str
    run_id: str | None = None
    step: str | None = None
    #: What the audit columns (``created_actor``, ``moved_actor``, a promotion's ``actor_id``) record
    #: when that is not ``actor`` itself. Set only for an admitted application acting for a user:
    #: ``actor`` is the user (what the contract's ``created_by`` shows), ``recorded_as`` is
    #: ``app:<application id>:on-behalf-of:<user>``, so the audit trail keeps the application.
    #: ``None`` for a user acting directly and for a strategy run: the audit then records ``actor``.
    recorded_as: str | None = None
    #: ``strategy_run`` only: the user the run acts for, which the MCP permission check is made against.
    principal: str | None = None

    def __post_init__(self) -> None:
        """Runs on construction: an identity the audit columns cannot hold is refused before anything is written.

        User ids are unbounded at the API boundary, and the content store's
        ``modifier`` column (a frozen migration shared with the Manifest) is
        ``MAX_AUDIT_ACTOR_CHARS`` wide, so a longer identity is refused here
        as ``LimitExceeded`` rather than failing inside a write.
        """
        if len(self.audit) > MAX_AUDIT_ACTOR_CHARS:
            raise LimitExceeded(
                f"the audit identity is {len(self.audit)} characters; the audit columns hold {MAX_AUDIT_ACTOR_CHARS}"
            )
        # The contract's provenance rule: a strategy_run names its run; a user has no run or step.
        if self.kind is ProvenanceKind.STRATEGY_RUN and not self.run_id:
            raise ValueError("a strategy_run provenance names its run_id")
        if self.kind is ProvenanceKind.USER and (self.run_id is not None or self.step is not None):
            raise ValueError("a user provenance carries no run_id or step")

    @property
    def audit(self) -> str:
        """What the audit columns record: ``recorded_as`` when set, else ``actor``."""
        return self.recorded_as or self.actor

    @property
    def checked_as(self) -> str:
        """The user the apply-time permission checks are made against."""
        return self.principal or self.actor

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind.value, "actor": self.actor}
        if self.run_id is not None:
            out["run_id"] = self.run_id
        if self.step is not None:
            out["step"] = self.step
        return out


#: A run id is one ref segment: ``candidate/<run_id>/<n>`` must parse back into three parts and
#: survive URL path resolution, so a segment of dots only (``.`` and ``..`` a resolver folds away) is not a run id.
RUN_ID_PATTERN = re.compile(r"^\.*[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
RUN_ID_MAX_LENGTH = 64
#: A strategy is named ``<strategy id>@<version>`` (06-evolution-run.md); it is the candidate's ``actor``.
STRATEGY_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+$")
STRATEGY_MAX_LENGTH = 256


@dataclass(frozen=True)
class RunScope:
    """The evolution run a candidate is recorded for, and the genes it may touch.

    ``run_id`` names the candidate ref (``candidate/<run_id>/<n>``);
    ``strategy`` (``id@version``) is recorded as the candidate's actor;
    ``allowed_genes`` is what the run's binding lets it change, and every op
    of the patch must fall under one of them; ``step`` is the strategy's own
    label for the step that produced the candidate.
    """

    run_id: str
    strategy: str
    allowed_genes: tuple[str, ...]
    step: str | None = None

    def __post_init__(self) -> None:
        """Runs on construction: refuses a run id or strategy the contract cannot record, for in-process callers too."""
        if len(self.run_id) > RUN_ID_MAX_LENGTH or not RUN_ID_PATTERN.fullmatch(self.run_id):
            raise ValueError(f"run_id {self.run_id!r} is not one ref segment ([A-Za-z0-9_.-], 1-64, not dots only)")
        if len(self.strategy) > STRATEGY_MAX_LENGTH or not STRATEGY_PATTERN.fullmatch(self.strategy):
            raise ValueError(f"strategy {self.strategy!r} is not <id>@<version>")


@dataclass(frozen=True)
class RevisionMetadata:
    """The platform-written part of a revision (contract §4.4); none of it is hashed."""

    id: str  #: ``sha256:<hex64>`` of the canonical ``{spec, policy}``: the identity
    seq: int  #: the bot's N-th recorded revision, from 1 (``r42``); a handle for people
    parent: str | None  #: the patch's base, or ``active`` for a capture / policy change; ``None`` for the first
    created_by: Provenance
    created_at: datetime
    status: RevisionStatus
    patch_digest: str | None  #: the stored patch's digest (the candidate id); ``None`` for a capture / policy change
    evidence: tuple[str, ...] = ()  #: experience refs the patch cited, e.g. ``episode:ep_91``
    evaluations: tuple[str, ...] = ()  #: verification evaluation ids attached later, e.g. ``ev_301``
    annotations: dict[str, str] = field(default_factory=dict)  #: free-form platform notes


@dataclass(frozen=True)
class GenomeRevision:
    """One revision: the contract's record, parsed (``genome-revision.schema.json``).

    ``spec`` and ``policy`` are the hashed half; their ``to_json()`` is the
    exact object the revision id is computed over.
    """

    genome_schema: int
    revision: RevisionMetadata
    spec: GenomeSpec
    policy: GenomePolicy

    def as_record(self, bot: BotRef) -> dict[str, Any]:
        """The contract's wire shape (``genome-revision.schema.json``)."""
        meta = self.revision
        return {
            "genome_schema": self.genome_schema,
            "revision": {
                "id": meta.id,
                "seq": meta.seq,
                "bot": {"owner_id": bot.owner_id, "bot_id": bot.bot_id},
                "parent": meta.parent,
                "created_by": meta.created_by.as_dict(),
                "created_at": meta.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": meta.status.value,
                "patch_digest": meta.patch_digest,
                "evidence": list(meta.evidence),
                "evaluations": list(meta.evaluations),
                "annotations": dict(meta.annotations),
            },
            "spec": self.spec.to_json(),
            "policy": self.policy.to_json(),
        }


class ProducedBlob(NamedTuple):
    """Bytes an operation produced that are not in the content store yet, named by their digest.

    ``digest`` is ``sha256:<64 hex>`` of ``data``, the address the spec names
    them by; the registry files every blob under it before the revision is
    recorded. Example: capturing a bot whose ``SOUL.md`` is ``"# Soul\\n"``
    yields ``ProducedBlob("sha256:3f9a…", b"# Soul\\n")``.
    """

    digest: str
    data: bytes


@dataclass(frozen=True)
class RecordedRevision:
    """What recording returns: the revision, whether it was new, and the assessment of a patch."""

    revision: GenomeRevision  #: the recorded revision, as ``get_revision`` would return it
    created: bool  #: False when a revision with this id already existed, which is then returned unchanged
    #: a patch's tier (``T0``–``T3``, README §6.3); ``None`` for a capture / policy change
    risk_tier: str | None = None
    flags: tuple[
        str, ...
    ] = ()  #: gene paths of files the patch rewrote (over the rewrite threshold), e.g. ``persona/SOUL.md``
    ref: str | None = None  #: the ref recording created, ``candidate/<run>/<n>`` for a candidate; ``None`` otherwise


@dataclass(frozen=True)
class RevisionPage:
    total: int
    items: tuple[RevisionMetadata, ...]


@dataclass(frozen=True)
class GenomeRef:
    name: str
    revision: str
    moved_at: datetime
    moved_by: Provenance
    reason: str | None = None


@dataclass(frozen=True)
class RefUpdate:
    """A request to move one ref by compare-and-swap (README §8).

    The move succeeds only if ``name`` points at ``expected_revision`` at
    the moment it is applied; otherwise ``RefConflict`` reports where it
    points now and nothing changes. Example: ``RefUpdate("draft",
    "sha256:7c1e…", "sha256:a90b…")`` moves ``draft`` from ``a90b`` to
    ``7c1e`` and fails if someone moved it first.
    """

    name: str  #: the ref to move: ``active``, ``previous``, ``draft`` or ``candidate/<run>/<n>``
    revision: str  #: the revision id the ref points at after the move
    expected_revision: str | None  #: the revision id it must point at now; ``None`` means the ref must not exist yet
    reason: str | None = None  #: the caller's reason, kept in the ref event log


@dataclass(frozen=True)
class GeneChange:
    """One gene that differs between two revisions (README §9.3)."""

    gene: str  #: the gene path, e.g. ``persona/SOUL.md`` or ``skills/refund-policy/SKILL.md``
    kind: str  #: ``added`` | ``removed`` | ``modified``
    #: A unified diff of the text, present only for a ``modified`` text file (or JSON value such as an MCP
    #: entry) whose diff fits ``MAX_DIFF_BYTES``; ``None`` for added / removed genes, binary files and trees.
    diff: str | None = None


@dataclass(frozen=True)
class GenomeDiff:
    """What ``revision`` changes against ``against``, gene by gene, with the risk of applying that change."""

    revision: str  #: the newer revision's id
    against: str  #: the older revision's id
    changes: tuple[GeneChange, ...]  #: in gene-path order
    #: The tier (``T0``–``T3``) a patch making exactly these changes would get under ``revision``'s policy,
    #: rewrite raise included.
    risk_tier: str
    #: Gene paths of text files whose change is a rewrite (more than the threshold of their lines changed,
    #: README §6.3) and of entries replaced by another kind; each raises the tier one step.
    rewrite_flags: tuple[str, ...]


@dataclass(frozen=True)
class ContentReceipt:
    """What an upload answers."""

    digest: str  #: ``sha256:<64 hex>`` of the uploaded bytes, the name a patch then uses
    size: int  #: the byte count
    created: bool  #: False when this bot could already reach these bytes


@dataclass(frozen=True)
class CompiledManifest:
    """A revision rendered as the complete desired-state document delivery consumes."""

    revision_id: str
    document: dict[str, Any]
    document_digest: str


@dataclass(frozen=True)
class ApplyResult:
    """What the delivery seam answered when it took a compiled revision."""

    kind: str  # the applier: pending | direct | …
    reference: str | None  # the applier's handle (an apply id), joined by applied_revision
    detail: str = ""


@dataclass(frozen=True)
class Promotion:
    """One promotion as recorded: the move of ``ref`` from ``from_revision`` to ``revision`` and its delivery.

    Promoting moves ``active`` to the target (and ``previous`` to the old
    ``active``), records this row with the compiled document, then hands
    the document to the delivery seam. ``document`` is carried only when the
    caller asked for it (a single read, not a listing).
    """

    id: str  #: the promotion's id
    ref: str  #: the ref it moved; ``active`` for every promotion today (canary refs are RSI-16)
    revision: str  #: the revision id the ref points at after the promotion
    from_revision: str | None  #: where the ref pointed before; ``None`` for the bot's first promotion
    document_digest: str  #: ``sha256:<64 hex>`` of the compiled document's canonical JSON
    going_back: bool  #: True when the target had been promoted before and was not the current ``active``
    actor: Provenance  #: who promoted
    reason: str  #: why, as the actor said
    status: str  #: ``applying`` until the seam answers, then ``applied`` or ``apply_failed``
    apply_kind: str  #: which delivery seam took it: ``pending`` (none yet, RSI-03) or ``direct`` (RSI-04)
    apply_reference: str | None  #: the seam's handle for the delivery (an apply id); ``None`` when none started
    apply_detail: str  #: the seam's one-line answer, e.g. ``started``; a failure names the error type only
    created_at: datetime  #: when the refs moved
    document: dict[str, Any] | None = None  #: the compiled document (Manifest v1 layout), when asked for


@dataclass(frozen=True)
class PromotionPage:
    total: int
    items: tuple[Promotion, ...]


def parse_ref_name(name: str) -> tuple[str, str | None, int | None]:
    """``("active", None, None)`` for a fixed ref, ``("candidate", run_id, n)`` for a candidate ref.

    Raises ``ValueError`` for anything else: no free-form ref names exist.
    """
    if name in FIXED_REFS:
        return name, None, None
    parts = name.split("/")
    if len(parts) == 3 and parts[0] == "candidate" and parts[1] and parts[2].isdigit() and int(parts[2]) >= 1:
        return "candidate", parts[1], int(parts[2])
    raise ValueError(f"not a ref name: {name!r}")


def candidate_ref(run_id: str, n: int) -> str:
    return f"candidate/{run_id}/{n}"
