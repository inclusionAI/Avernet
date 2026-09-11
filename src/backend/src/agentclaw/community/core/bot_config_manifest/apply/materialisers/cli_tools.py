"""``cli_tools`` → ``CliToolService``. The manifest as a second caller.

**The entry shape.** ``identity`` for this category is the entry's ``name``,
the command as it will be invoked. Both source spellings are accepted, and a
``digest`` is mandatory on everything but git::

    manifest:
      cli_tools:
        # a named object-store source. The source's key is a prefix and the
        # entry's is the rest, so this reads oss://team-artifacts/tools/qc/v2.tgz
        - name: qc
          from: artifacts
          key: qc/v2.tgz
          digest: sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b...
          version: "2.1.0"           # a label only; never decides convergence

        # an inline source declaration
        - name: mycli
          source:
            protocol: oss
            bucket: team-artifacts
            key: tools/mycli
            auth: oss-prod
          digest: sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b...

        # over git the resolved commit SHA is the pin, so 'digest' is
        # refused rather than required
        - name: tool
          from: content
          subpath: bin/tool

An entry reaches ``resolve`` as the raw mapping, e.g. ``{"name": "mycli",
"source": {"protocol": "oss", "bucket": "team-artifacts", "key": "tools/mycli",
"auth": "oss-prod"}, "digest": "sha256:…"}``.

**The API is the other caller, and it does not declare a source at all**: it
uploads the binary. Both doors converge inside :class:`CliToolService` once
the bytes are in hand, which is the property that keeps them refusing the same
executable for the same reason.

This materialiser fetches nothing, verifies nothing, stores nothing and
delivers nothing. It translates: manifest entries into ``CliToolDecl``s on the
way in, and the service's per-tool outcomes into report rows on the way out.
Every step between belongs to :class:`CliToolService`, which the management API
also calls — and doing any of it twice is exactly how two callers of one
feature drift apart on the checks that matter (the pin, the architecture,
whether a failed placement still writes a row).

**The area this overwrites** is the one work-items §3.2 names for this
category: the tools installed on *this bot*, ``ac_bot_cli_tool`` keyed
``(env, entity_id, bot_id, name)``. Declared and absent ⇒ installed. Installed
and no longer declared ⇒ removed. Already there at the same ``(digest,
subpath)`` ⇒ ``unchanged``, and nothing is fetched.

**Convergence is ``(digest, subpath)`` and never ``version``.** One archive can
carry two commands, so the digest alone cannot decide; and ``version`` is a
label, so letting it converge would redeliver a binary of up to 200 MiB because
a caller edited a string.

**Removing a tool a user installed through the API is intended**, and is the
cost §3.2 accepted when it made a declared category overwrite its area. The
row's ``installed_by`` is what lets the report say a manifest apply replaced an
API-installed tool rather than silently overwriting it.
"""
from __future__ import annotations

from typing import Any, Sequence

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestCategory
from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.declarations import (
    CliToolDecl,
    CliToolStatus,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.models import (
    INSTALLED_BY_MANIFEST,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    CliToolService,
)
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    declared_protocol,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind

#: ``CliToolStatus`` → the :class:`~...outcomes.EntryOutcome` its report row
#: carries::
#:
#:     CliToolStatus.INSTALLED -> EntryOutcome.UPDATED
#:     CliToolStatus.UNCHANGED -> EntryOutcome.UNCHANGED
#:     CliToolStatus.FAILED    -> EntryOutcome.FAILED
#:     CliToolStatus.CONFLICT  -> EntryOutcome.FAILED
#:
#: Note that ``INSTALLED`` maps to ``UPDATED``, not ``CREATED``: the service
#: reports that it wrote the tool, not whether one was there before, and the
#: created/updated split is decided in ``plan`` from the current area instead.
#:
#: ``CliToolStatus.REMOVED`` is deliberately absent: a removal has no declared
#: entry to attach to, so the orchestrator reports it from the plan's
#: ``removals`` and it never reaches this map.
_OUTCOMES = {
    CliToolStatus.INSTALLED: EntryOutcome.UPDATED,
    CliToolStatus.UNCHANGED: EntryOutcome.UNCHANGED,
    CliToolStatus.FAILED: EntryOutcome.FAILED,
    # Unreachable from here — a full override never asks for insert-only, so
    # the service never answers CONFLICT on this path. Mapped anyway so a
    # future caller that does ask cannot produce a KeyError mid-apply.
    CliToolStatus.CONFLICT: EntryOutcome.FAILED,
}


def context_for(ctx: ApplyContext) -> CliToolContext:
    """The apply's identity, in the service's vocabulary::

        context_for(ctx)
        # -> CliToolContext(
        #        bot_id="bot_42", owner_id="usr_owner",
        #        actor_id="usr_collaborator", entity_id="ent_7",
        #        env="prod", engine_type=<the bot's engine>, tenant="acme",
        #        apply_id="ap_01HZX8",        # None off the apply path
        #        budget=ApplyFetchBudget(...),      # None likewise
        #        source_session=SourceSession(...), # None likewise
        #    )

    Ten fields, all copied straight off :class:`ApplyContext`. The service
    itself satisfies ``FetchContext`` through this object, which is how its
    fetch is charged to this apply's budget. ``engine_type`` is carried but
    never compared here: this module must not name an engine, and a structural
    test pins that against its whole source, docstrings included.

    ``actor_id`` stays the person applying — the audit field must not lose that
    a collaborator, not the owner, ran this — while ``installed_by`` is the
    constant ``"manifest"``, which is a different question: *what put the tool
    there*, and therefore whether a later full override is replacing a
    hand-installed tool.

    The three apply-only fields ride along so the service's fetch is charged to
    this apply's budget and its receipts carry this apply's id. An install
    through the HTTP route passes ``None`` for all three, which is what that
    column's nullability means.
    """
    return CliToolContext(
        bot_id=ctx.bot_id,
        owner_id=ctx.owner_id,
        actor_id=ctx.actor_id,
        entity_id=ctx.entity_id,
        env=ctx.env,
        engine_type=ctx.engine_type,
        tenant=ctx.tenant,
        apply_id=ctx.apply_id,
        budget=ctx.budget,
        source_session=ctx.source_session,
    )


class CliToolsMaterialiser(Materialiser):
    """Converges this bot's installed CLI tools toward the declaration.

    ``identity`` is the entry's ``name``; ``Intent.value`` is a
    ``CliToolDecl``, the service's own declaration type. Unlike the other
    fetching categories, ``resolve`` performs **no fetch** — the service owns
    fetching for both of its callers — so a source that is down produces a
    ``FAILED`` row out of ``write`` rather than aborting the category::

        resolve -> ResolveResult(intents=(Intent(
                       identity="mycli",
                       value=CliToolDecl(name="mycli", ...)),))
        plan    -> CategoryPlan(
                       entries=(PlannedEntry(<that intent>, "created"),),
                       removals=("retired-tool",))
        write   -> (EntryResult(ManifestCategory.CLI_TOOLS, "mycli",
                                EntryOutcome.CREATED),)
    """

    construct = ManifestCategory.CLI_TOOLS

    def __init__(self, cli_tool_service: CliToolService) -> None:
        self._tools = cli_tool_service

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """The syntactic checks, and nothing else. **No fetch here.**

        The other fetching categories fetch in ``resolve`` because that is where
        their failures belong. This one does not, and the difference is
        deliberate: the service owns fetching for both of its callers, and a
        fetch here would be a second implementation reached only by the manifest
        — the one arm where a divergence is hardest to notice, because a report
        row would still say the entry failed.

        The cost is that a fetch failure surfaces as a ``FAILED`` entry from
        ``write`` rather than as a resolve failure that aborts the category. For
        this category that is the better reading anyway: three tools that
        installed and one whose source was down is exactly what happened, and
        aborting would leave the bot with none of them.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        seen: set[str] = set()

        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                failures.append(
                    ResolveFailure(f"[{index}]", "a cli_tools entry must be a mapping")
                )
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                failures.append(
                    ResolveFailure(f"[{index}]", "a cli_tools entry must name a command")
                )
                continue
            if name in seen:
                # A bot cannot have one command twice, and the table's UNIQUE
                # constraint says so. Refused rather than de-duplicated: two
                # entries for one name means the author believes something that
                # is not true of the result.
                failures.append(
                    ResolveFailure(name, "declared more than once in this category")
                )
                continue
            seen.add(name)

            decl = CliToolDecl.from_entry(entry)
            if not decl.digest and declared_protocol(ctx, entry) is not SourceKind.GIT:
                # The schema refuses this at PUT. Re-asked here because a stored
                # document can predate a rule or have skipped the validator (a
                # hand-built apply in W8's lifecycle points), and the platform
                # distributing an unpinned executable is the one thing this
                # category exists not to do.
                #
                # **Except over git**, where the resolved commit SHA is already
                # the pin — the same exemption ``DIGEST_REQUIRED`` states at
                # ``PUT``, keyed on the same axis. A belt that refused what the
                # surface accepts would be the "accepted means appliable" rule
                # broken by the belt itself. ``declared_protocol`` answers from
                # the source declaration through the one parser, so inline and
                # ``from:`` are the same source — which is the whole of D5.
                failures.append(
                    ResolveFailure(
                        name,
                        "cli_tools requires a 'digest' — the platform is "
                        "distributing an executable, so the supply chain is "
                        "pinned or the entry is refused",
                    )
                )
                continue
            if not entry.get("from") and not entry.get("source"):
                failures.append(
                    ResolveFailure(name, "a cli_tools entry must name a source")
                )
                continue

            # No substitution here. ``${BOT_*}`` in a declared source is the
            # fetch funnel's to resolve, on its own road — which is also the
            # only road that can see a ``from``-named source's fields at all.
            # This materialiser used to substitute a copy of the address for
            # itself, and that copy is what a stale reader could acquire from;
            # the entry it passes down is now the entry it was given.
            intents.append(Intent(name, decl))

        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Diff the declaration against **the table**.

        Never against the engine's listing: a tool the platform installed must
        be planned for removal even when the engine's view has drifted, and a
        listing that came back short would silently leave it behind. The table
        is also what makes a dry run possible at all — asking the container
        would be a write path's worth of I/O for a preview.
        """
        installed = {
            record.name: record.convergence_key
            for record in self._tools.list(context_for(ctx))
        }
        declared = {intent.identity for intent in intents}

        planned = tuple(
            PlannedEntry(
                intent,
                (
                    EntryOutcome.UNCHANGED.value
                    # ``and`` first: an unpinned (git) declaration's key is
                    # ``None``, and ``None == None`` would make an absent row
                    # and an unpinned tool compare equal — "unchanged" for a
                    # tool that is not installed at all.
                    if intent.value.convergence_key is not None
                    and installed.get(intent.identity) == intent.value.convergence_key
                    else (
                        EntryOutcome.UPDATED.value
                        if intent.identity in installed
                        else EntryOutcome.CREATED.value
                    )
                ),
            )
            for intent in intents
        )
        # Sorted so a report — and a test — reads deterministically.
        removals = tuple(sorted(set(installed) - declared))
        return CategoryPlan(entries=planned, removals=removals)

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """One call: ``CliToolService.replace_all``.

        The service recomputes removals from the same table ``plan`` read, so
        the two cannot disagree; passing the plan's removals instead would make
        this materialiser the second place that decides what a full override
        deletes.

        An ``unchanged`` entry costs nothing inside the service either — it is
        not re-fetched and not redelivered — which is what makes re-applying an
        unchanged document perform no write.
        """
        outcomes = await self._tools.replace_all(
            context_for(ctx),
            [planned.intent.value for planned in plan.entries],
            installed_by=INSTALLED_BY_MANIFEST,
        )
        by_name = {outcome.name: outcome for outcome in outcomes}
        results: list[EntryResult] = []
        for planned in plan.entries:
            name = planned.intent.identity
            outcome = by_name.get(name)
            if outcome is None:  # pragma: no cover - the service answers per tool
                continue
            # ``reason`` on a failure, ``note`` on a success: they answer
            # opposite questions, and a client rendering failures must not show
            # a note as an error.
            failed = outcome.failed
            results.append(
                EntryResult(
                    self.construct,
                    name,
                    _OUTCOMES[outcome.status],
                    reason=outcome.detail if failed else None,
                    note=outcome.detail if not failed else None,
                )
            )
        return tuple(results)


__all__ = ["CliToolsMaterialiser", "context_for"]
