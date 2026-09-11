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
#:
#: Always an enum member, never a bare string. ``.value`` is both the wire form
#: and the manifest key::
#:
#:     ManifestCategory.SKILLS         # .value == "skills"
#:     ManifestCategory.RESOURCES      # .value == "resources"
#:     ManifestCategory.IDENTITY       # .value == "identity"
#:     ManifestCategory.MCP            # .value == "mcp"
#:     ManifestCategory.CLI_TOOLS      # .value == "cli_tools"
#:     ManifestCategory.ENGINE_CONFIG  # .value == "engine_config"
#:     ManifestSection.SCRIPT          # .value == "script"
#:
#: Created by: ``apply/order.APPLY_ORDER`` (one row per construct) and each
#: materialiser's own ``construct`` class attribute.
#: Consumed by: ``apply/registry.build_materialisers`` (as the map key),
#: ``EntryResult.as_dict`` and ``CategoryResult.as_dict`` (as ``.value``).
ApplyConstruct = ManifestCategory | ManifestSection


class EntryOutcome(StrEnum):
    """What happened to one **declared** entry. A lowercase string on the wire.

    The five values, and what triggers each:

    ==============  ==========================================================
    Value           Trigger
    ==============  ==========================================================
    ``created``     The entry was not in the bot's area; it was written.
    ``updated``     It was there with different content; it was overwritten.
    ``unchanged``   It was there and already matched; no write was made.
    ``skipped``     Its category was aborted by *another* entry's failure.
    ``failed``      This entry itself could not be resolved or written.
    ==============  ==========================================================

    Example::

        EntryOutcome.CREATED.value == "created"

    Created by: each materialiser's ``plan`` and ``write`` for the first three;
    ``apply/orchestrator`` assigns ``FAILED`` and ``SKIPPED``.
    Consumed by: ``outcomes.derive_status``, and ``EntryResult.as_dict`` as the
    wire field ``action``.

    ``SKIPPED`` means *"not written because its category was aborted"* — the
    all-or-nothing rule refusing to write a partial set. It does not mean "the
    author allowed this one to be missing": under category overwrite, "skip
    this entry" would mean "delete it", the opposite of the name.

    The entry that *caused* an abort is ``FAILED``; its blameless neighbours in
    the same category are ``SKIPPED``.
    """

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


class ApplyStatus(StrEnum):
    """The report's own state. Upper case on the wire, unlike
    :class:`EntryOutcome`.

    ===============  ========================================================
    Value            Meaning
    ===============  ========================================================
    ``RUNNING``      Still working; no terminal value decided yet.
    ``SUCCEEDED``    Every declared entry was delivered, or nothing was
                     declared at all.
    ``PARTIAL``      Some entries were delivered and some were not.
    ``FAILED``       Nothing was delivered.
    ===============  ========================================================

    Example::

        ApplyStatus.PARTIAL.value == "PARTIAL"

    Created by: the apply service at start (``RUNNING``), then
    :func:`derive_status` for the three terminal values.
    Consumed by: ``ApplyReport.as_payload`` as the wire field ``result``, and
    by whoever polls the apply record.

    ``RUNNING`` exists because apply is **started, not awaited**: the route
    answers ``202`` with an id and the work continues on a background thread, so
    a poller needs to tell "still working" from "finished, and partially".

    The three terminal values are **derived** from the entry outcomes once every
    decision has been made. Nothing in this engine branches on one, and nothing
    writes one to a bot record. They are a summary for whoever reads the report.
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
    """One declared entry's outcome — one row of the report's ``entries`` list.

    ``identity`` is the value of whichever key that category names entries by:

    ===============  =================  ==================================
    Construct        Key read           Example ``identity``
    ===============  =================  ==================================
    ``skills``       ``name``           ``"code-review"``
    ``identity``     ``type``           ``"SOUL.md"``
    ``resources``    ``path``           ``"data/faq.csv"``
    ``mcp``          ``server_code``    ``"gh"``
    ``cli_tools``    ``name``           ``"qc"``
    ``script``       none; fixed        ``"script"``
    ===============  =================  ==================================

    ``script`` has no entity key of its own, because there is one script, so it
    reports under the section's own name (``materialisers/script._IDENTITY``).
    A malformed entry carrying none of those keys falls back to
    :func:`entry_identity`'s positional form, ``"[0]"``.

    Three states, all real. A failure, the entry that caused its category's
    abort::

        EntryResult(
            construct=ManifestCategory.RESOURCES,
            identity="data/faq.csv",
            outcome=EntryOutcome.FAILED,
            reason="the git tree has no file at subpath 'kb/faq.csv'",
            note=None,
        )

    A blameless neighbour in that same aborted category::

        EntryResult(
            construct=ManifestCategory.RESOURCES,
            identity="data/prices/",
            outcome=EntryOutcome.SKIPPED,
            reason=(
                "not written: another entry in this category could not be "
                "materialized, and a category is written in full or not at all"
            ),
            note=None,
        )

    A success carrying a note::

        EntryResult(
            construct=ManifestSection.SCRIPT,
            identity="script",
            outcome=EntryOutcome.CREATED,
            reason=None,
            note=(
                "delivered now; executes at this bot's next device "
                "provisioning (create, restart or republish). Apply does "
                "not run it."
            ),
        )

    Created by: each materialiser's ``write``; ``apply/orchestrator`` builds the
    ``FAILED`` and ``SKIPPED`` rows.
    Consumed by: ``CategoryResult.entries`` and ``ApplyReport.entries``, then
    :meth:`as_dict`.

    The report names entries the way their author wrote them, so a reader can
    find the line they need to fix.
    """

    construct: ApplyConstruct
    identity: str
    outcome: EntryOutcome
    #: Present when the outcome is ``FAILED`` or ``SKIPPED``. Never a credential
    #: value, and never raw exception text that might carry one — materialisers
    #: compose these deliberately.
    reason: str | None = None
    #: Something true of a *successful* entry that a caller would otherwise have
    #: to infer. Separate from ``reason`` rather than sharing it, because they
    #: answer opposite questions — "why did this not happen" and "what happens
    #: next" — and a client rendering failures would otherwise show a note as an
    #: error. Today's only use is ``script``'s delivery timing.
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The wire shape for one entry. The keys are renamed on the way out::

            {
                "category": "resources",     # construct.value
                "name": "data/faq.csv",      # identity
                "action": "failed",          # outcome.value
                "error": "the git tree has no file at subpath 'kb/faq.csv'",
                "note": None,
            }
        """
        return {
            "category": self.construct.value,
            "name": self.identity,
            "action": self.outcome.value,
            "error": self.reason,
            "note": self.note,
        }


@dataclass(frozen=True)
class CategoryResult:
    """One construct's entries, plus what overwriting its area removed.

    ``aborted`` and ``partially_written`` together say what the bot's area now
    holds:

    ===========  ==================  ======================================
    ``aborted``  ``partially_``      What it means for the bot's area
                 ``written``
    ===========  ==================  ======================================
    False        False               Converged. The area is what the
                                     document declared.
    True         False               Nothing was written. The area is
                                     exactly as it was before this apply.
    True         True                Some writes landed. Do not trust the
                                     area; re-apply to converge it.
    False        True                Never produced. ``partially_written``
                                     is only ever set alongside an abort.
    ===========  ==================  ======================================

    A converged category::

        CategoryResult(
            construct=ManifestCategory.MCP,
            entries=(EntryResult(ManifestCategory.MCP, "gh",
                                 EntryOutcome.CREATED),),
            removals=("old-server",),
            aborted=False,
            partially_written=False,
        )

    One aborted in ``resolve``, before any write::

        CategoryResult(
            construct=ManifestCategory.RESOURCES,
            entries=(<the FAILED entry>, <its SKIPPED neighbours>),
            removals=(),
            aborted=True,
            partially_written=False,
        )

    Created by: ``apply/orchestrator`` (one per construct it walks).
    Consumed by: ``ApplyReport.categories``, :func:`derive_status`, and
    :meth:`as_dict`.

    ``removals`` is its own field rather than a sixth :class:`EntryOutcome`: the
    five outcomes classify **declared** entries, and a removal has no declared
    entry to classify — ``skills: []`` deletes every skill while declaring none
    of them. Folding removals into the enum would either invent a value the
    acceptance criteria do not list, or leave the destructive half of overwrite
    unaudited.
    """

    construct: ApplyConstruct
    entries: tuple[EntryResult, ...] = ()
    #: Identities that existed in the area and are no longer declared, so
    #: overwrite removed them. Empty on an aborted category: the report never
    #: claims a removal it cannot confirm, which is why a partially written
    #: category says so through ``partially_written`` instead.
    removals: tuple[str, ...] = ()
    #: True when the category did not converge — the all-or-nothing rule (§3.2).
    #: On its own this does **not** promise the area is untouched; see
    #: ``partially_written``.
    aborted: bool = False
    #: True when the abort happened *during* the write, so some of the area may
    #: already have changed.
    #:
    #: Every refusal this engine can foresee is asked in ``resolve``, before the
    #: first write, and an abort from there leaves the area genuinely untouched.
    #: But a write can still fail for reasons no precondition can rule out — the
    #: service is down, a concurrent change lands, the row is gone — and the
    #: writes already made are real. This module cannot roll them back: the
    #: services it materialises through expose no transaction spanning several
    #: calls, and a compensating undo can fail exactly as the write did, so
    #: claiming atomicity would be a stronger promise than the platform can keep.
    #:
    #: The honest thing is therefore to *report* it rather than assert it away.
    #: ``aborted`` with this false means nothing was written; with this true it
    #: means "do not trust the area, re-apply to converge it". Silently reporting
    #: the second as the first is what a caller cannot recover from.
    partially_written: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The wire shape for one category's summary. Note that the entries are
        **not** nested here — they are flattened into the report's own
        ``entries`` list::

            {
                "category": "resources",
                "aborted": True,
                "partially_written": False,
                "removed": [],
            }
        """
        return {
            "category": self.construct.value,
            "aborted": self.aborted,
            "partially_written": self.partially_written,
            "removed": list(self.removals),
        }


@dataclass(frozen=True)
class SourceResolution:
    """A git source this apply resolved, and the commit it landed on.

    Only the git road produces these: an object-store source resolves no ref,
    so it contributes no row. A named source::

        SourceResolution(name="content",
                         url="https://code.example.com/team/content.git",
                         ref="v1.2.0", mode="strict",
                         resolved_sha="7c1d…", auth="gh-readonly")

    An inline git source has no ``from`` name, so it is named by the repository
    and the ref it declared, joined by ``@``::

        SourceResolution(name="https://code.example.com/team/content.git@main",
                         url="https://code.example.com/team/content.git",
                         ref="main", mode="non_strict",
                         resolved_sha="9e8d…", auth=None)

    ``name`` is the **display**: one row per declaration, so two names pointing
    at one repository are two rows and two inline declarations of one
    repository at two refs are two rows. ``(url, ref, mode)`` is what the
    strict-mode baselines are read back by — the display plays no part in that,
    because "has this repository's ref moved since we last resolved it" is not
    a question about what the document called the source.

    Created by: ``apply/source_session.SourceSession.adopt``, one per distinct
    ``display`` name, returned through ``resolution_records()``.
    Consumed by: ``ApplyReport.sources``, and read back by the apply service to
    build the next apply's ``SourceSession.baselines``.

    Records the credential's **name** and never its value: the report is what a
    support engineer reads, so this is a security property rather than tidiness.
    """

    #: The ``from`` name, or ``<url>@<ref>`` for an inline source.
    name: str
    #: The substituted repository URL — no credentials, which this record is
    #: structurally unable to carry anyway: it holds names, never values. Part
    #: of the key the next apply reads its baseline by, and ``None`` only on a
    #: row written before this field existed.
    url: str | None = None
    #: The ref as declared: a tag, a branch, or a full SHA. ``"HEAD"`` when the
    #: source declared none.
    ref: str | None = None
    #: The 40-character commit id the ref actually resolved to.
    resolved_sha: str | None = None
    #: The ``mode`` this resolution was made under, ``"strict"`` or
    #: ``"non_strict"``. The last third of the baseline key, and load-bearing
    #: rather than informational: a pin may only be advanced by an apply that
    #: stood behind it under the *same* mode. Were it left out, a document
    #: naming one ``(url, ref)`` twice — once ``strict``, once ``non_strict`` —
    #: would let the lax declaration record a moved sha that the pinned one had
    #: just refused, and the next apply would hand the pinned entry the very
    #: commit it rejected: "refuse each move once, then deliver it".
    mode: str | None = None
    #: The credential's name, never its value. ``None`` for an anonymous fetch.
    auth: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "ref": self.ref,
            "mode": self.mode,
            "resolved_sha": self.resolved_sha,
            "auth": self.auth,
        }


@dataclass(frozen=True)
class ApplyReport:
    """Everything one apply produced. Apply's only output.

    One filled-in example, an apply whose ``mcp`` converged and whose
    ``resources`` aborted::

        ApplyReport(
            apply_id="ap_01HZX8",
            bot_id="bot_42",
            trigger="explicit",
            status=ApplyStatus.PARTIAL,
            started_at=datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 3, 1, 9, 0, 4, tzinfo=timezone.utc),
            categories=(
                CategoryResult(ManifestCategory.MCP, entries=(...,)),
                CategoryResult(
                    ManifestCategory.RESOURCES,
                    entries=(...,),
                    aborted=True,
                ),
            ),
            sources=(
                SourceResolution(
                    name="content",
                    url="https://code.example.com/team/content.git",
                    ref="v1.2.0",
                    mode="strict",
                    resolved_sha="4f2a9c1b8e7d6a5c4b3a2918f7e6d5c4b3a29187",
                    auth="git-prod",
                ),
            ),
            notes=(),
        )

    Created by: ``apply/orchestrator`` at the end of an apply, and by
    ``apply/apply_task`` for the background path.
    Consumed by: :meth:`as_payload` for the HTTP response, and the apply record
    the poller reads.

    It is not a projection of some richer internal state — there is no richer
    state, because the per-entry records are the whole of what apply knows.
    """

    #: This apply's own id, as ``ApplyContext.apply_id`` carried it.
    apply_id: str
    bot_id: str
    #: What started the apply, as one of ``apply/triggers.ALL_TRIGGERS``:
    #: ``"explicit"``, ``"put"``, ``"create:pre_container"`` or
    #: ``"create:on_container"``. Restart and republish are not triggers.
    trigger: str
    status: ApplyStatus
    started_at: datetime
    finished_at: datetime | None = None
    #: One per construct the apply walked, in ``APPLY_ORDER`` position order.
    categories: tuple[CategoryResult, ...] = ()
    #: One per distinct git source this apply resolved. Empty when the document
    #: names no git source, which includes every object-store-only document.
    sources: tuple[SourceResolution, ...] = field(default=())
    #: Apply-level notes belonging to no category. Today's only producer is the
    #: delivery strategy's closing step: a teclaw redeliver that failed after
    #: every category was written is recorded here rather than raised.
    notes: tuple[str, ...] = field(default=())

    @property
    def entries(self) -> tuple[EntryResult, ...]:
        """Every entry result, flattened, in category order."""
        return tuple(
            entry for category in self.categories for entry in category.entries
        )

    def as_payload(self) -> dict[str, Any]:
        """The wire shape, defined here and nowhere else.

        ``status`` is emitted as ``result``, the timestamps as ISO strings, and
        the categories' entries are flattened into one top-level list::

            {
                "apply_id": "ap_01HZX8",
                "bot_id": "bot_42",
                "trigger": "explicit",
                "result": "PARTIAL",
                "started_at": "2026-03-01T09:00:00+00:00",
                "finished_at": "2026-03-01T09:00:04+00:00",
                "sources": [{"name": "content",
                             "url": "https://code.example.com/team/content.git",
                             "ref": "v1.2.0", "mode": "strict",
                             "resolved_sha": "4f2a9c1b...", "auth": "git-prod"}],
                "categories": [{"category": "mcp", "aborted": False,
                                "partially_written": False, "removed": []}],
                "entries": [{"category": "mcp", "name": "gh",
                             "action": "created", "error": None,
                             "note": None}],
                "notes": [],
            }

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
            "notes": list(self.notes),
        }


#: The entity-key fields an entry may name itself by, in the order they are
#: tried. One list for every category rather than a per-category lookup: each
#: category's entries carry exactly one of these, so the first hit is that
#: category's key — ``skills.name``, ``identity.type``, ``resources.path``,
#: ``mcp.server_code``, ``cli_tools.name``.
#:
#: Lives here rather than in the orchestrator on purpose: which field to *print*
#: is vocabulary, and the orchestrator is held to naming no category at all.
_ENTITY_KEY_FIELDS: tuple[str, ...] = ("name", "type", "path", "server_code")


def entry_identity(entry: Any, index: int) -> str:
    """How an entry names itself in a report.

    Takes the raw entry mapping and its position in the declared list, and
    answers the string :class:`EntryResult` will carry as ``identity``::

        entry_identity({"name": "code-review", "from": "pkgs"}, 0)
        # -> "code-review"
        entry_identity({"path": "data/faq.csv"}, 1)
        # -> "data/faq.csv"

        # No recognised key, or not a mapping at all: the position, in
        # brackets. This is what a malformed entry reports as.
        entry_identity({"nmae": "typo"}, 2)   # -> "[2]"
        entry_identity("not a mapping", 0)    # -> "[0]"

    Called by: ``apply/orchestrator``, when building the ``FAILED`` and
    ``SKIPPED`` rows for an aborted category.

    The fallback is load-bearing: a category can be aborted over a document
    whose entries are malformed, and those entries still have to appear in the
    report.
    """
    if isinstance(entry, dict):
        for key in _ENTITY_KEY_FIELDS:
            value = entry.get(key)
            if isinstance(value, str) and value:
                return value
    return f"[{index}]"


def derive_status(categories: tuple[CategoryResult, ...]) -> ApplyStatus:
    """The summary, computed once every decision has already been made.

    Takes every :class:`CategoryResult` the apply produced and answers one of
    the three terminal :class:`ApplyStatus` values (never ``RUNNING``).

    Called by: ``apply/orchestrator`` and ``apply/apply_task``, once, at the end.

    Deliberately a free function taking the finished results: it cannot be
    consulted mid-apply, which is the mechanical form of "the summary decides
    nothing".

    * Nothing declared at all ⇒ ``SUCCEEDED``. A bot with no manifest, or one
      whose manifest declares no category, applied everything it was asked to.
    * Every entry ``created`` / ``updated`` / ``unchanged`` ⇒ ``SUCCEEDED``.
    * Some delivered and some not ⇒ ``PARTIAL``.
    * Nothing delivered ⇒ ``FAILED``.

    An aborted category **with no entries of its own** has to be counted
    directly, not inferred from entries, because it produced none to inspect.
    A declared-empty category is the case: ``mcp: []`` whose removal raised, or
    ``script: null`` whose delete failed, aborts having asked for a state it
    never reached — and with an empty entry list it would otherwise fall through
    every entry-based test and be reported ``SUCCEEDED``. A caller polling that
    report would be told its bot converged on a state the apply had just failed
    to reach.
    """
    entries = tuple(entry for category in categories for entry in category.entries)
    # Counted as failures in their own right; see the docstring.
    silent_failures = sum(
        1 for category in categories if category.aborted and not category.entries
    )
    if not entries and not silent_failures:
        return ApplyStatus.SUCCEEDED
    delivered = {EntryOutcome.CREATED, EntryOutcome.UPDATED, EntryOutcome.UNCHANGED}
    ok = sum(1 for entry in entries if entry.outcome in delivered)
    total = len(entries) + silent_failures
    if ok == total:
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
