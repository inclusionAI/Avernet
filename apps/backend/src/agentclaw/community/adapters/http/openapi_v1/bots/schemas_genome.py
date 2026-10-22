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
RevisionStatusLiteral = Literal["draft", "candidate", "accepted", "rejected", "promoted", "archived"]
_STATUS_DESCRIPTION = (
    "`draft` (an owner's edit), `candidate` (a run's proposal, undecided), `accepted` (the gate accepted it), "
    "`rejected`, `promoted` (made `active` at least once), `archived` (retired; never deleted)."
)


class ProvenancePayload(BaseModel):
    """Who recorded a revision or moved a ref."""

    kind: Literal["user", "strategy_run"] = Field(description="`user` for a person, `strategy_run` for an evolution run.")
    actor: str = Field(description="The user id, or the strategy that proposed the change.")
    run_id: str | None = Field(default=None, description="The evolution run, for `strategy_run`.")
    step: str | None = Field(default=None, description="For `strategy_run`: the strategy's step inside that run which produced the record, e.g. `propose` (a run can submit several candidates from several steps).")


class RevisionSummary(BaseModel):
    """A revision's metadata, as listed; the content is read with `GET …/revisions/{rev}`."""

    id: str = Field(description="The content-hash id, `sha256:<hex64>`: the same content has the same id on any bot.")
    seq: int = Field(description="The per-bot sequence number: this is the bot's N-th recorded revision, counting from 1, so `r42` is its 42nd. A handle for people, never an identity (two bots both have an `r1`).")
    parent: str | None = Field(description="The revision this one was derived from; null for the bot's first.")
    status: RevisionStatusLiteral = Field(description=_STATUS_DESCRIPTION)
    created_by: ProvenancePayload = Field(description="Who recorded it.")
    created_at: datetime = Field(description="When it was recorded (UTC).")
    patch_digest: str | None = Field(description="The patch that produced it (its candidate id, `sha256:<hex64>`). Null when the revision did not come from a patch: a capture of the bot (`POST …/revisions:capture`) or a policy change (`PUT …/policy`).")
    evidence: list[str] = Field(default_factory=list, description="Experience references the patch cited, e.g. `[\"episode:ep_91\", \"feedback:fb_311\"]` (`episode:<id>`, `feedback:<id>`, `eval:<id>`); empty for a capture or a policy change.")
    evaluations: list[str] = Field(default_factory=list, description="Ids of the verification evaluations run on this revision, e.g. `[\"ev_301\", \"ev_302\"]`; empty until verification attaches one.")
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
    status: RevisionStatusLiteral = Field(description=_STATUS_DESCRIPTION)
    parent: str | None = Field(description="The revision it was derived from; null for the bot's first.")
    patch_digest: str | None = Field(description="The patch's candidate id when it came from a patch; null for a capture or a policy change.")
    risk_tier: str | None = Field(default=None, description="The patch's tier `T0`–`T3` (contract §6.3); null for a capture or a policy change.")
    flags: list[str] = Field(default_factory=list, description="Gene paths of files the patch rewrote rather than edited: a text file with more than 2/5 of its lines changed, or an entry replaced by one of another kind. E.g. `[\"persona/SOUL.md\"]` when a `file.edit` rewrote most of SOUL.md, which lifts that op to at least T2 (contract §6.2).")
    ref: str | None = Field(default=None, description="The `candidate/<run>/<n>` ref a run's candidate was named; null otherwise.")
    created: bool = Field(description="False when the content already existed: same id, same seq, same parent.")


class RunScopePayload(BaseModel):
    """The evolution run a candidate is recorded for, and what it may change."""

    run_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="The run's id (one execution of a strategy for this bot, e.g. `run_7f3`); it names the candidate ref `candidate/<run_id>/<n>`, so it is one ref segment: `[A-Za-z0-9_.-]`, 1 to 64 characters.")
    allowed_genes: list[str] = Field(description="Gene paths the run's binding lets it change, e.g. `[\"persona\", \"skills/refund-policy\"]`; every op of the patch must fall under one of them, or the patch is refused with `gene_not_allowed`.")


class RecordPatchRequest(BaseModel):
    """Record a revision by applying a Genome Patch to its own `base`."""

    status: Literal["draft", "candidate"] = Field(description="`draft` for an owner's edit, `candidate` for a run's proposal.")
    run: RunScopePayload | None = Field(default=None, description="Required with `candidate` and refused with `draft`: the evolution run that proposed the patch. It bounds the patch (every op must fall under the run's `allowed_genes`) and names the revision's ref `candidate/<run_id>/<n>`.")
    patch: dict[str, Any] = Field(description="A Genome Patch (contract §6): `{patch_schema, base, ops, rationale, evidence}`, where `base` is the id of this bot's revision the ops apply to.")

    @model_validator(mode="after")
    def _run_matches_status(self) -> RecordPatchRequest:
        if (self.status == "candidate") != (self.run is not None):
            raise ValueError("`run` is required with status `candidate` and not allowed with `draft`")
        return self


class PolicyRequest(BaseModel):
    """The owner's policy: what automation may change on this bot (contract §5).

    Exactly the contract's three fields, all required and nothing else: a
    misspelled or missing `risk_overrides` must not be recorded as "no
    overrides", which would silently drop a tier the owner meant to raise.
    """

    model_config = ConfigDict(extra="forbid")

    mutable_genes: list[str] = Field(description="Sorted, unique gene paths patches may change; everything else is off limits.")
    pins: list[str] = Field(description="Sorted, unique gene paths no patch may touch, even inside a mutable gene.")
    risk_overrides: dict[str, str] = Field(description="Gene path → tier (`T0`–`T3`); an override can only raise an op's tier. Required, `{}` for none.")


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
    rewrite_flags: list[str] = Field(description="Gene paths of files a patch making these changes would be flagged `rewrite` on: a text file with more than 2/5 of its lines changed, or an entry replaced by one of another kind, e.g. `[\"persona/SOUL.md\"]`.")


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

    revision: str = Field(description="The revision to point `draft` at (the ref is named by the path): " + _REV_DESCRIPTION)
    expected_revision: str | None = Field(default=None, description="The revision id `draft` points at now, as the caller last read it, or null if `draft` does not exist yet; the move happens only if that is still true (`409` otherwise).")
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
    document_digest: str = Field(description="`sha256:<hex64>` of the compiled document's canonical JSON (the revision rendered in the Manifest v1 field layout, contract §7); the apply report repeats it, which is how the registry later tells which revision a bot runs.")
    going_back: bool = Field(description="True when the target had been promoted before and is not the current `active`.")
    actor: ProvenancePayload = Field(description="Who promoted.")
    reason: str = Field(description="Why, as the actor said.")
    status: Literal["applying", "applied", "apply_failed"] = Field(description="`applying` while delivery starts, then `applied` or `apply_failed`.")
    apply_kind: str = Field(description="Which delivery seam took the promotion: `pending` (RSI-03: no delivery exists yet, every promotion is refused before the refs move) or `direct` (RSI-04: direct delivery into the apply engine). Named by the seam, so a future seam adds a value.")
    apply_reference: str | None = Field(description="The seam's handle for the delivery it started, an apply id such as `0123456789abcdef0123456789abcdef`; it is what `applied_revision` joins the bot's last apply on. Null when delivery did not start.")
    apply_detail: str = Field(description="One sentence from the seam on how the start went, e.g. `started`; a failure names the error type only, never its text.")
    created_at: datetime = Field(description="When the refs moved (UTC).")
    document: dict[str, Any] | None = Field(default=None, description="The compiled document the promotion recorded (the revision in the Manifest v1 field layout, contract §7); present on a single promotion read, null in listings.")
