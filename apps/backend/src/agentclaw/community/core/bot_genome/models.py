"""Values the Genome Registry and promotion pass around.

``spec`` and ``policy`` stay the contract's JSON (validated by the schema in
``contract.py``); the dataclasses here carry what the registry adds around
them. Everything is frozen: a revision never changes, and a ref move returns
a new ``GenomeRef``.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import datetime
from abc import abstractmethod
from enum import StrEnum
from typing import Any, Protocol

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

    ``entity_id`` + ``bot_id`` is the bot's identity within an env and tenant
    (a bot id alone is not unique across entities) and the storage key the
    genome and Manifest tables share. ``owner_id`` is a different value: the
    owning user's id (``bot_service.create_bot`` sets ``entity_id`` to
    ``staff_<user>`` unless told otherwise), which the identity, skill,
    resource and MCP services scope their reads by. Capture needs both.
    ``engine_type`` and ``bot_type`` are the capability axes;
    ``entity_type`` (``staff`` or ``user``) is the identity service's third
    coordinate for the bot's files.
    """

    entity_id: str
    bot_id: str
    owner_id: str
    engine_type: str
    bot_type: str
    entity_type: str = "staff"


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
    #: How the audit columns name a user actor when that differs from the principal: an admitted
    #: application acting for a user is ``app:<id>:on-behalf-of:<user>``, never just the user.
    recorded_as: str | None = None
    #: ``strategy_run`` only: the user the run acts for, which the MCP permission check is made against.
    principal: str | None = None

    @property
    def audit(self) -> str:
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


@dataclass(frozen=True)
class RecordedRevision:
    """What recording returns: the revision, whether it was new, and the assessment of a patch."""

    revision: GenomeRevision
    created: bool
    risk_tier: str | None = None
    flags: tuple[str, ...] = ()
    ref: str | None = None


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
    """A compare-and-swap move: ``expected_revision`` is where the caller believes the ref points now.

    ``None`` means the ref must not exist yet.
    """

    name: str
    revision: str
    expected_revision: str | None
    reason: str | None = None


@dataclass(frozen=True)
class GeneChange:
    gene: str
    kind: str  # added | removed | modified
    diff: str | None = None


@dataclass(frozen=True)
class GenomeDiff:
    revision: str
    against: str
    changes: tuple[GeneChange, ...]
    risk_tier: str
    rewrite_flags: tuple[str, ...]


@dataclass(frozen=True)
class ContentReceipt:
    digest: str
    size: int
    created: bool


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
    """One promotion as recorded; ``document`` is carried only when the caller asked for it."""

    id: str
    ref: str
    revision: str
    from_revision: str | None
    document_digest: str
    going_back: bool
    actor: Provenance
    reason: str
    status: str  # applying | applied | apply_failed
    apply_kind: str
    apply_reference: str | None
    apply_detail: str
    created_at: datetime
    document: dict[str, Any] | None = None


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
