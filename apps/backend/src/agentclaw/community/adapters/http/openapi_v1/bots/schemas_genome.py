"""Wire shapes of the ``/openapi/v1/bots/{bot_id}/genome`` group (RSI-03).

``spec``, ``policy`` and ``patch`` ride as plain JSON objects: their shape is
the Genome contract's (``api-contracts/genome/v1/*.schema.json``), which the
registry validates with the contract's own schemas; re-declaring it here
would be a second, drifting copy.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_REV_DESCRIPTION = "A revision id (`sha256:<hex64>`), `r<seq>`, or a ref name such as `active`, `previous`, `draft` or `candidate/<run>/<n>`."
_STATUS_DESCRIPTION = (
    "`draft` (an owner's edit), `candidate` (a run's proposal, undecided), `accepted` (the gate accepted it), "
    "`rejected`, `promoted` (made `active` at least once), `archived` (retired; never deleted)."
)


class ProvenancePayload(BaseModel):
    """Who recorded a revision or moved a ref."""

    kind: Literal["user", "strategy_run"] = Field(description="`user` for a person, `strategy_run` for an evolution run.")
    actor: str = Field(description="The user id, or the strategy that proposed the change.")
    run_id: str | None = Field(default=None, description="The evolution run, for `strategy_run`.")
    step: str | None = Field(default=None, description="The run step, for `strategy_run`.")


class RevisionSummary(BaseModel):
    """A revision's metadata, as listed; the content is read with `GET …/revisions/{rev}`."""

    id: str = Field(description="The content-hash id, `sha256:<hex64>`: the same content has the same id on any bot.")
    seq: int = Field(description="The per-bot sequence number, for people: `r42` is seq 42. Never an identity.")
    parent: str | None = Field(description="The revision this one was derived from; null for the bot's first.")
    status: str = Field(description=_STATUS_DESCRIPTION)
    created_by: ProvenancePayload = Field(description="Who recorded it.")
    created_at: datetime = Field(description="When it was recorded (UTC).")
    patch_digest: str | None = Field(description="The patch that produced it (its candidate id), if it came from a patch.")
    evidence: list[str] = Field(default_factory=list, description="Experience references the patch cited (`episode:<id>`, `feedback:<id>`, `eval:<id>`).")
    evaluations: list[str] = Field(default_factory=list, description="Evaluation ids attached by verification.")
    annotations: dict[str, str] = Field(default_factory=dict, description="Free-form platform notes, outside the hash.")


class GenomeRevisionPayload(BaseModel):
    """A whole revision: the contract's record shape (metadata, `spec`, `policy`)."""

    genome_schema: int = Field(description="The contract version the record follows (1).")
    revision: RevisionSummary = Field(description="The revision's metadata.")
    spec: dict[str, Any] = Field(description="The bot's definition, every category present (contract §4).")
    policy: dict[str, Any] = Field(description="What automation may change: `mutable_genes`, `pins`, `risk_overrides` (contract §5).")


class RevisionRecorded(BaseModel):
    """What recording answers: the revision's identity and, for a patch, its assessment."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "sha256:7c1e" + "0" * 60, "seq": 42, "status": "candidate", "parent": "sha256:a90b" + "0" * 60,
                "patch_digest": "sha256:c41e" + "0" * 60, "risk_tier": "T2", "flags": [], "ref": "candidate/run_7f3/1", "created": True,
            }
        }
    )

    id: str = Field(description="The recorded revision's content-hash id.")
    seq: int = Field(description="Its per-bot sequence number.")
    status: str = Field(description=_STATUS_DESCRIPTION)
    parent: str | None = Field(description="The revision it was derived from; null for the bot's first.")
    patch_digest: str | None = Field(description="The patch's candidate id when it came from a patch.")
    risk_tier: str | None = Field(default=None, description="The patch's tier `T0`–`T3` (contract §6.3); null for a capture or a policy change.")
    flags: list[str] = Field(default_factory=list, description="Gene paths of files the patch rewrote (more than 2/5 of their lines).")
    ref: str | None = Field(default=None, description="The `candidate/<run>/<n>` ref a run's candidate was named; null otherwise.")
    created: bool = Field(description="False when the content already existed: same id, same seq, same parent.")


class RunScopePayload(BaseModel):
    """The evolution run a candidate is recorded for, and what it may change."""

    run_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="The run's id; names the candidate ref `candidate/<run_id>/<n>`, so it is one ref segment: `[A-Za-z0-9_.-]`, 1 to 64 characters.")
    binding_id: str = Field(min_length=1, max_length=64, description="The strategy binding the run executes.")
    allowed_genes: list[str] = Field(description="Gene paths the run may change; every op must be covered by one.")


class RecordPatchRequest(BaseModel):
    """Record a revision by applying a Genome Patch to its own `base`."""

    status: Literal["draft", "candidate"] = Field(description="`draft` for an owner's edit, `candidate` for a run's proposal.")
    run: RunScopePayload | None = Field(default=None, description="Required with `candidate` and refused with `draft`: the run, which bounds the genes and names the candidate ref.")
    patch: dict[str, Any] = Field(description="A Genome Patch (contract §6); `patch.base` is the revision it applies to.")

    @model_validator(mode="after")
    def _run_matches_status(self) -> "RecordPatchRequest":
        if (self.status == "candidate") != (self.run is not None):
            raise ValueError("`run` is required with status `candidate` and not allowed with `draft`")
        return self


class PolicyRequest(BaseModel):
    """The owner's policy: what automation may change on this bot (contract §5)."""

    mutable_genes: list[str] = Field(description="Sorted, unique gene paths patches may change; everything else is off limits.")
    pins: list[str] = Field(description="Sorted, unique gene paths no patch may touch, even inside a mutable gene.")
    risk_overrides: dict[str, str] = Field(default_factory=dict, description="Gene path → tier (`T0`–`T3`); an override can only raise an op's tier.")


class GeneChangePayload(BaseModel):
    """One gene that differs between two revisions."""

    gene: str = Field(description="The gene path, e.g. `persona/SOUL.md` or `skills/refund-policy`.")
    kind: Literal["added", "removed", "modified"] = Field(description="`added`, `removed`, or `modified` (the key stayed, the value changed).")
    diff: str | None = Field(default=None, description="A unified diff (hunks only) for a modified UTF-8 text file; null otherwise.")


class GenomeDiffPayload(BaseModel):
    """What one revision changes against another, per gene."""

    revision: str = Field(description="The newer side's revision id.")
    against: str = Field(description="The revision it is compared with.")
    changes: list[GeneChangePayload] = Field(description="Every gene that differs, in gene-path order.")
    risk_tier: str = Field(description="The highest tier a patch making these changes would get (`T0`–`T3`).")
    rewrite_flags: list[str] = Field(description="Gene paths of files rewritten beyond the 2/5 threshold.")


class GenomeRefPayload(BaseModel):
    """A named pointer at a revision."""

    name: str = Field(description="`active`, `previous`, `canary`, `draft`, or `candidate/<run>/<n>`.")
    revision: str = Field(description="The revision id it points at.")
    moved_at: datetime = Field(description="When it last moved (UTC).")
    moved_by: ProvenancePayload = Field(description="Who moved it.")
    reason: str | None = Field(default=None, description="Why, as the mover said.")


class GenomeRefsPayload(BaseModel):
    """Every ref of the bot."""

    refs: list[GenomeRefPayload] = Field(description="The bot's refs, by name.")


class MoveRefRequest(BaseModel):
    """Move the `draft` ref by compare-and-swap."""

    revision: str = Field(description=_REV_DESCRIPTION)
    expected_revision: str | None = Field(default=None, description="Where the ref points now, or null if it does not exist yet.")
    reason: str | None = Field(default=None, max_length=1024, description="Why, for the ref log.")


class ContentStored(BaseModel):
    """The receipt of an upload."""

    digest: str = Field(description="`sha256:<hex64>` of the bytes: what a patch names them by.")
    size: int = Field(description="The byte count stored.")
    created: bool = Field(description="False when this bot already held the bytes.")


class PromoteRequest(BaseModel):
    """Move `active` to a revision and hand it to delivery."""

    revision: str = Field(description=_REV_DESCRIPTION + " `previous` goes one step back.")
    expected_revision: str | None = Field(default=None, description="Where `active` points now; null when the bot has no `active` yet.")
    reason: str = Field(min_length=1, max_length=1024, description="Why, for the promotion record and the ref log.")


class PromotionPayload(BaseModel):
    """One promotion as recorded."""

    id: str = Field(description="The promotion's id.")
    ref: str = Field(description="The ref it moved (`active`).")
    revision: str = Field(description="The revision it moved the ref to.")
    from_revision: str | None = Field(description="Where the ref pointed before; null for a bot's first promotion.")
    document_digest: str = Field(description="The digest of the compiled document's canonical JSON; what the apply report must repeat.")
    going_back: bool = Field(description="True when the target had been promoted before and is not the current `active`.")
    actor: ProvenancePayload = Field(description="Who promoted.")
    reason: str = Field(description="Why, as the actor said.")
    status: Literal["applying", "applied", "apply_failed"] = Field(description="`applying` while delivery starts, then `applied` or `apply_failed`.")
    apply_kind: str = Field(description="The delivery seam that took it (`pending` until direct delivery lands).")
    apply_reference: str | None = Field(description="The seam's handle (an apply id), or null.")
    apply_detail: str = Field(description="The seam's last word; never raw error text.")
    created_at: datetime = Field(description="When the refs moved (UTC).")
    document: dict[str, Any] | None = Field(default=None, description="The compiled document; only on a single promotion read.")
