"""What one apply did: the per-entry outcomes, and the report they make up.

A leaf. It imports the construct vocabulary and nothing else from this feature,
which is what lets the materialisers, the orchestrator and the HTTP adapter all
depend on it without closing a cycle.

**The per-entry records *are* the report** (work-items §2.7). Apply has no other
output: it writes nothing to the bot record, and the summary below is derived
for a reader rather than consumed by anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)

#: What a construct is, for the purposes of applying it: one of the six
#: categories under ``manifest``, or the top-level ``script`` section.
ApplyConstruct = ManifestCategory | ManifestSection


class EntryOutcome(StrEnum):
    """What happened to one **declared** entry.

    ``SKIPPED`` means *"not written because its category was aborted"* — the
    all-or-nothing rule in work-items §3.2 refusing to write a partial set. It
    no longer means "the author allowed this one to be missing": that reading
    belonged to ``on_fetch_failure: skip``, which was removed when §3.2 became
    category overwrite, because under overwrite "skip this entry" would mean
    "delete it" — the opposite of the name.

    The entry that *caused* an abort is ``FAILED``; its blameless neighbours in
    the same category are ``SKIPPED``.
    """

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


class ApplyStatus(StrEnum):
    """The report's own state.

    ``RUNNING`` exists because apply is **started, not awaited**: the route
    answers ``202`` with an id and the work continues on a background thread, so
    a poller needs to tell "still working" from "finished, and partially" — the
    distinction W13's ``APPLYING`` state is built on.

    The three terminal values are **derived** from the entry outcomes once every
    decision has been made. Nothing in this engine branches on one, and nothing
    writes one to a bot record (§2.7). They are a summary for whoever reads the
    report.
    """

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


#: Why a category was not written, when the orchestrator aborted it before any
#: materialiser could be asked. Kept as a constant because the HTTP layer and
#: the tests both assert on it, and a reason string drifting between them would
#: make the "no materialiser yet" state hard to recognise.
NO_MATERIALISER_REASON = (
    "no materializer is registered for this construct in this build, so nothing "
    "can apply it; the category was not written"
)


@dataclass(frozen=True)
class EntryResult:
    """One declared entry's outcome.

    ``identity`` is whatever its category keys entries by — a skill ``name``, an
    identity ``type``, a resource ``path``, an mcp ``server_code``. The report
    names entries the way their author wrote them, so a reader can find the line
    they need to fix.
    """

    construct: ApplyConstruct
    identity: str
    outcome: EntryOutcome
    #: Present when the outcome is ``FAILED`` or ``SKIPPED``. Never a credential
    #: value, and never raw exception text that might carry one — materialisers
    #: compose these deliberately.
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The wire shape for one entry."""
        return {
            "category": self.construct.value,
            "name": self.identity,
            "action": self.outcome.value,
            "error": self.reason,
        }


@dataclass(frozen=True)
class CategoryResult:
    """One construct's entries, plus what overwriting its area removed.

    ``removals`` is its own field rather than a sixth :class:`EntryOutcome`, and
    the distinction is precision rather than taste: the five outcomes classify
    **declared** entries, and a removal has no declared entry to classify —
    ``skills: []`` deletes every skill while declaring none of them. Folding
    removals into the enum would either invent a value the acceptance criteria
    do not list, or leave the destructive half of overwrite unaudited.
    """

    construct: ApplyConstruct
    entries: tuple[EntryResult, ...] = ()
    #: Identities that existed in the area and are no longer declared, so
    #: overwrite removed them. Empty on an aborted category by construction:
    #: an aborted category is not written at all.
    removals: tuple[str, ...] = ()
    #: True when the category was not written at all — the all-or-nothing rule
    #: (§3.2). Everything about its area is unchanged.
    aborted: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The wire shape for one category's summary."""
        return {
            "category": self.construct.value,
            "aborted": self.aborted,
            "removed": list(self.removals),
        }


@dataclass(frozen=True)
class SourceResolution:
    """A named source, and what its ref resolved to.

    Empty in this wave — nothing is fetched — and filled by W5. Records the
    credential's **name** and never its value: the report is what a support
    engineer reads, so this is a security property rather than tidiness.
    """

    name: str
    ref: str | None = None
    resolved_sha: str | None = None
    auth: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ref": self.ref,
            "resolved_sha": self.resolved_sha,
            "auth": self.auth,
        }


@dataclass(frozen=True)
class ApplyReport:
    """Everything one apply produced. Shape follows design §7.

    Apply's only output. It is not a projection of some richer internal state —
    there is no richer state, because §2.7 makes the per-entry records the whole
    of what apply knows.
    """

    apply_id: str
    bot_id: str
    #: ``explicit`` here. W8 and W13 add ``republish`` / ``restart`` / ``create``
    #: without this type changing.
    trigger: str
    status: ApplyStatus
    started_at: datetime
    finished_at: datetime | None = None
    categories: tuple[CategoryResult, ...] = ()
    #: Resolved named sources. Empty in this wave; W5 fills it.
    sources: tuple[SourceResolution, ...] = field(default=())

    @property
    def entries(self) -> tuple[EntryResult, ...]:
        """Every entry result, flattened, in category order."""
        return tuple(
            entry for category in self.categories for entry in category.entries
        )

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, defined here and nowhere else.

        Every field is named explicitly. There is no passthrough of a declared
        entry or of a materialiser's internals, which is what makes it
        structurally unable to emit a credential value: a secret cannot ride
        along inside a dict nobody inspected, because no dict is copied through.
        """
        return {
            "apply_id": self.apply_id,
            "bot_id": self.bot_id,
            "trigger": self.trigger,
            "result": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            "sources": [source.as_dict() for source in self.sources],
            "categories": [category.as_dict() for category in self.categories],
            "entries": [entry.as_dict() for entry in self.entries],
        }


#: The entity-key field each category names its entries by, in the order they
#: are tried. Schema §3 gives each category its own key — ``skills.name``,
#: ``identity.type``, ``resources.path``, ``mcp.server_code`` — and a report
#: that could not name an entry would be one a caller cannot act on.
#:
#: Lives here rather than in the orchestrator on purpose: which field to *print*
#: is vocabulary, and the orchestrator is held to naming no category at all.
_ENTITY_KEY_FIELDS: tuple[str, ...] = ("name", "type", "path", "server_code")


def entry_identity(entry: Any, index: int) -> str:
    """How an entry names itself in a report.

    Falls back to the entry's position, and the fallback is load-bearing: a
    category can be aborted over a document whose entries are malformed, and
    those entries still have to appear in the report.
    """
    if isinstance(entry, dict):
        for key in _ENTITY_KEY_FIELDS:
            value = entry.get(key)
            if isinstance(value, str) and value:
                return value
    return f"[{index}]"


def derive_status(categories: tuple[CategoryResult, ...]) -> ApplyStatus:
    """The summary, computed once every decision has already been made.

    Deliberately a free function taking the finished results: it cannot be
    consulted mid-apply, which is the mechanical form of "the summary decides
    nothing" (§2.7).

    * Nothing declared at all ⇒ ``SUCCEEDED``. A bot with no manifest, or one
      whose manifest declares no category, applied everything it was asked to.
    * Every entry ``created`` / ``updated`` / ``unchanged`` ⇒ ``SUCCEEDED``.
    * Some delivered and some not ⇒ ``PARTIAL``.
    * Nothing delivered ⇒ ``FAILED``.
    """
    entries = tuple(entry for category in categories for entry in category.entries)
    if not entries:
        return ApplyStatus.SUCCEEDED
    delivered = {EntryOutcome.CREATED, EntryOutcome.UPDATED, EntryOutcome.UNCHANGED}
    ok = sum(1 for entry in entries if entry.outcome in delivered)
    if ok == len(entries):
        return ApplyStatus.SUCCEEDED
    return ApplyStatus.PARTIAL if ok else ApplyStatus.FAILED


__all__ = [
    "ApplyConstruct",
    "ApplyReport",
    "ApplyStatus",
    "CategoryResult",
    "EntryOutcome",
    "EntryResult",
    "NO_MATERIALISER_REASON",
    "SourceResolution",
    "derive_status",
    "entry_identity",
]
