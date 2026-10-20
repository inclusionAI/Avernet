"""Values the Genome Registry and promotion pass around.

``spec`` and ``policy`` stay the contract's JSON (validated by the schema in
``contract.py``); the dataclasses here carry what the registry adds around
them. Everything is frozen: a revision never changes, and a ref move returns
a new ``GenomeRef``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


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
    """Who is moving a ref; each ref kind has exactly one allowed mover (01-genome.md §5.1)."""

    OWNER = "owner"
    PROMOTION = "promotion"
    RUN = "run"


#: The four fixed ref names every bot can have; ``candidate/<run_id>/<n>`` is the only other shape.
FIXED_REFS = ("active", "previous", "canary", "draft")
PROMOTION_REFS = ("active", "previous", "canary")


@dataclass(frozen=True)
class BotRef:
    """The bot a registry call is about, with the facts every check needs.

    ``owner_id`` + ``bot_id`` is the bot's identity (a bot id alone is not
    unique across owners); ``entity_id`` is the storage key shared with the
    Manifest tables; ``engine_type`` and ``bot_type`` are the capability axes.
    """

    owner_id: str
    bot_id: str
    entity_id: str
    engine_type: str
    bot_type: str
    entity_type: str = "staff"


@dataclass(frozen=True)
class Provenance:
    kind: ProvenanceKind
    actor: str
    run_id: str | None = None
    step: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind.value, "actor": self.actor}
        if self.run_id is not None:
            out["run_id"] = self.run_id
        if self.step is not None:
            out["step"] = self.step
        return out


@dataclass(frozen=True)
class RunScope:
    """The evolution run a candidate is recorded for, and the genes it may touch."""

    run_id: str
    binding_id: str
    allowed_genes: tuple[str, ...]


@dataclass(frozen=True)
class RevisionMetadata:
    id: str
    seq: int
    parent: str | None
    created_by: Provenance
    created_at: datetime
    status: RevisionStatus
    patch_digest: str | None
    evidence: tuple[str, ...] = ()
    evaluations: tuple[str, ...] = ()
    annotations: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GenomeRevision:
    genome_schema: int
    revision: RevisionMetadata
    spec: dict[str, Any]
    policy: dict[str, Any]

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
            "spec": self.spec,
            "policy": self.policy,
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
