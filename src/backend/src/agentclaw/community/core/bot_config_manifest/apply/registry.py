"""What materialises each construct, and the three-stage contract they honour.

**The contract is ``resolve`` → ``plan`` → ``write``**, and each stage has its
own currency. Worked end to end for ``mcp``, on a bot that already has ``gh``
installed plus an ``old`` server the document no longer declares::

    # The declared entries the orchestrator hands in:
    [{"server_code": "gh"}, {"server_code": "slack"}]

    # 1. resolve(ctx, entries) -> ResolveResult
    #    Declared entries become intents. Everything that can fail before
    #    touching the bot fails here.
    ResolveResult(
        intents=(Intent(identity="gh", value="gh", note=None),
                 Intent(identity="slack", value="slack", note=None)),
        failures=(),
    )

    # 2. plan(ctx, intents) -> CategoryPlan
    #    Read-only. Each intent is classified against what is actually there,
    #    and what is there but no longer declared becomes a removal.
    CategoryPlan(
        entries=(PlannedEntry(Intent("gh", "gh"), "unchanged"),
                 PlannedEntry(Intent("slack", "slack"), "created")),
        removals=("old",),
    )

    # 3. write(ctx, plan) -> Sequence[EntryResult]
    #    Executes it. "unchanged" calls nothing; removals are deactivated.
    (EntryResult(ManifestCategory.MCP, "gh", EntryOutcome.UNCHANGED),
     EntryResult(ManifestCategory.MCP, "slack", EntryOutcome.CREATED))

Had ``slack`` been unpermitted, ``resolve`` would have answered
``ResolveResult(intents=(...gh...), failures=(ResolveFailure("slack", "this
tenant does not have permission to enable this MCP server"),))`` and the
orchestrator would have aborted the category without calling ``plan`` at all —
so ``gh`` reports ``skipped`` and ``old`` is never removed.

**The registry is sparse on purpose.** ``APPLY_ORDER`` names every construct the
vocabulary defines; this maps only the ones some shipped code can act on. A
construct declared in a document with no entry here is an **expected state**,
not a gap: the orchestrator fails its entries with a readable reason and aborts
the category, so nothing is destroyed, and the window closes by registering a
materialiser rather than by deleting a branch.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol, Sequence, runtime_checkable

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext

if TYPE_CHECKING:  # pragma: no cover — the registry stays import-light; see below
    from agentclaw.community.core.ports.activation_port import (
        ActivationPort,
    )
    from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import EntryFetcher
    from agentclaw.community.core.ports.identity_file_port import (
        IdentityFilePort,
    )
    from agentclaw.community.core.ports.resource_file_port import (
        ResourceFilePort,
    )
    from agentclaw.community.core.ports.skill_package_upload_port import (
        SkillPackageUploadPort,
    )
    from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
        CliToolService,
    )
    from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
        BotStartupScriptServiceProtocol,
    )
    from agentclaw.community.core.mcp.mcp_auth_service_protocol import (
        MCPAuthServiceProtocol,
    )
    from agentclaw.community.core.skill_center.capability_state_contract import (
        BotCapabilityStateReaderProtocol,
    )
    from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyConstruct,
    EntryResult,
)


@dataclass(frozen=True)
class Intent:
    """One declared entry, resolved into something writable.

    ``value`` is the materialiser's own currency and differs completely per
    category::

        # mcp: the server code, again — there is nothing else to write
        Intent(identity="gh", value="gh")

        # script: the substituted body, never the raw document text
        Intent(identity="script", value="#!/bin/sh\necho prod\n")

        # skills: the materialiser's private package dataclass
        Intent(identity="code-review", value=_SkillPackage(...),
               note="delivered from the platform's stored copy (keep_last): "
                    "the git fetch failed")

    Created by: each materialiser's ``resolve``.
    Consumed by: that same materialiser's ``plan`` and ``write``, wrapped in a
    :class:`PlannedEntry`.

    ``identity`` is what the category keys entries by. The orchestrator never
    inspects ``value``, which is what keeps category knowledge out of it.

    ``note`` is a successful write's caveat, surfaced on the entry's report
    row — today's only producer is a ``keep_last`` fallback, whose published
    contract is that the report states it. It rides with the intent because the
    fetch resolved it and the write reports it, and neither stage should reach
    into the other's currency.
    """

    #: How the entry names itself, e.g. ``"gh"`` or ``"data/faq.csv"``. The
    #: same string that ends up as ``EntryResult.identity``.
    identity: str
    #: Whatever this materialiser needs in order to write, already substituted
    #: and validated. Opaque to everything except the materialiser that made it.
    value: Any = None
    #: A caveat to put on the entry's report row even though the write
    #: succeeded. ``None`` on the ordinary path.
    note: Optional[str] = None


@dataclass(frozen=True)
class ResolveFailure:
    """One entry that could not be turned into an intent, and why::

        ResolveFailure(
            identity="slack",
            reason=("this tenant does not have permission to enable this "
                    "MCP server"),
        )

    Created by: each materialiser's ``resolve``.
    Consumed by: ``apply/orchestrator``, which turns each into a ``FAILED``
    :class:`~...outcomes.EntryResult` and marks every other entry in the
    category ``SKIPPED``.

    A single one of these aborts its whole category: under overwrite a partial
    set is *destructive*, because writing ``{A}`` when the declaration was
    ``{A, B}`` deletes B.
    """

    #: The entry's own name, or ``entry_identity``'s ``"[0]"`` fallback when
    #: the entry was too malformed to name itself.
    identity: str
    #: Report-safe text, handed to the report verbatim. May name a credential;
    #: never carries its value.
    reason: str


@dataclass(frozen=True)
class ResolveResult:
    """What ``resolve`` learned, keyed so the orchestrator can report per entry.

    The two halves are independent: an entry is in exactly one of them, and a
    result may carry both::

        # every entry resolved; the category proceeds to plan
        ResolveResult(intents=(Intent("gh", "gh"),), failures=())

        # one entry failed: the category is aborted, and the resolved
        # intents are never written
        ResolveResult(
            intents=(Intent("gh", "gh"),),
            failures=(ResolveFailure("slack", "this tenant does not have "
                                     "permission to enable this MCP server"),),
        )

    Created by: each materialiser's ``resolve``.
    Consumed by: ``apply/orchestrator``, which checks :attr:`ok` before calling
    ``plan``.

    Both halves are returned rather than raising on the first problem: a caller
    fixing a document should see every entry that failed, not discover them one
    resubmission at a time.
    """

    #: The entries that resolved, in declaration order.
    intents: tuple[Intent, ...] = ()
    #: The entries that did not. Non-empty means the category is aborted.
    failures: tuple[ResolveFailure, ...] = ()

    @property
    def ok(self) -> bool:
        """True when every declared entry resolved."""
        return not self.failures


@dataclass(frozen=True)
class PlannedEntry:
    """One intent, classified against what is actually there::

        PlannedEntry(intent=Intent("gh", "gh"), outcome="unchanged")
        PlannedEntry(intent=Intent("slack", "slack"), outcome="created")

    Created by: each materialiser's ``plan``.
    Consumed by: that materialiser's ``write``, which reads ``outcome`` to
    decide whether to call the service at all.
    """

    intent: Intent
    #: The plain **string** value of an :class:`~...outcomes.EntryOutcome`, not
    #: the enum: ``"created"``, ``"updated"`` or ``"unchanged"``. Never
    #: ``"failed"`` or ``"skipped"``, which are the orchestrator's to assign.
    outcome: str


@dataclass(frozen=True)
class CategoryPlan:
    """What ``write`` would do, computed without doing any of it::

        CategoryPlan(
            entries=(PlannedEntry(Intent("gh", "gh"), "unchanged"),
                     PlannedEntry(Intent("slack", "slack"), "created")),
            removals=("old",),
        )

        # A converged document: nothing to write, nothing to remove.
        # ``is_noop`` is True here and only here.
        CategoryPlan(
            entries=(PlannedEntry(Intent("gh", "gh"), "unchanged"),),
            removals=(),
        )

    Created by: each materialiser's ``plan``.
    Consumed by: that materialiser's ``write``, and by ``dry_run``, which stops
    here.

    ``dry_run`` returning after this stage is why the stage exists as its own
    call: a preview that cannot write is one that is *missing the call*, rather
    than one that is disciplined about not making it.
    """

    #: One per resolved intent, in declaration order.
    entries: tuple[PlannedEntry, ...] = ()
    #: Identities present in the area and no longer declared — what overwriting
    #: removes, e.g. ``("old",)``. Sorted by the materialisers that produce
    #: them, so a report reads deterministically. Reported separately from entry
    #: outcomes because a removal has no declared entry to attach to.
    removals: tuple[str, ...] = field(default=())

    @property
    def is_noop(self) -> bool:
        """True when applying this plan would write nothing at all.

        The convergence criterion depends on this: re-applying an unchanged
        document must not merely produce equal output, it must make **no
        writes**, and that is observable only if the plan can say so.
        """
        return not self.removals and all(
            entry.outcome == "unchanged" for entry in self.entries
        )


@runtime_checkable
class Materialiser(Protocol):
    """Three stages, because three acceptance criteria need boundaries there.

    The currencies, in order: ``Sequence[dict]`` in, :class:`ResolveResult`,
    :class:`CategoryPlan`, ``Sequence[EntryResult]`` out. This module's own
    docstring works one category through all three.

    Six ship: ``script``, ``mcp``, ``identity``, ``skills``, ``resources`` and
    ``cli_tools``. All three stages are ``async``, including the ones whose
    shipped implementations never await.

    Every member is ``@abstractmethod`` and each materialiser **inherits** this
    Protocol rather than merely satisfying it structurally. Omitting a stage
    then fails at construction naming it, rather than as an ``AttributeError``
    the first time a category reaches that stage: for ``write``, that would be
    mid-apply on a real bot, after ``resolve`` and ``plan`` had already
    succeeded.
    """

    #: Which construct this materialises, e.g. ``ManifestCategory.MCP``. Also
    #: the key :func:`build_materialisers` files the instance under, so a
    #: materialiser cannot be registered under the wrong one.
    construct: ApplyConstruct

    @abstractmethod
    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Declared entries → intents.

        ``entries`` are the raw mappings straight out of the parsed document,
        in declaration order, e.g. ``[{"server_code": "gh"}, {"path":
        "data/faq.csv", "from": "content"}]``. A category declared empty
        (``mcp: []``) arrives as ``[]`` and must still be handled: it means
        "remove everything", not "do nothing".

        Everything that can fail **before touching the bot** fails here:
        placeholder substitution, validators, permission checks. The fetch
        lands in this stage and nowhere else, which is why the
        transient-failure criterion is satisfied by construction rather than by
        each materialiser remembering to satisfy it.
        """
        ...

    @abstractmethod
    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Read current state, classify each intent, and compute removals.

        ``intents`` is :attr:`ResolveResult.intents`, reached only when that
        result carried no failures.

        **Read-only.** Nothing here writes, and ``dry_run`` stops after it.
        """
        ...

    @abstractmethod
    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Execute the plan, and answer one :class:`~...outcomes.EntryResult`
        per planned entry.

        Removals are performed but produce **no** result rows: they are carried
        on :attr:`CategoryPlan.removals` and reported through
        ``CategoryResult.removals`` instead, because a removal has no declared
        entry to attach an outcome to.

        Reached only when ``resolve`` produced no failures. A plan that is
        ``is_noop`` must perform no write at all — that absence is what the
        convergence test asserts.
        """
        ...


def build_materialisers(
    *,
    script_service: BotStartupScriptServiceProtocol,
    activation_service: ActivationPort,
    mcp_auth_service: MCPAuthServiceProtocol,
    identity_service: IdentityFilePort,
    upload_service: SkillPackageUploadPort,
    capability_reader: BotCapabilityStateReaderProtocol,
    package_validator: SkillPackageValidator,
    entry_fetcher: EntryFetcher,
    resource_service: ResourceFilePort,
    cli_tool_service: CliToolService,
) -> dict[ApplyConstruct, Materialiser]:
    """The registry, built from injected services::

        {
            ManifestSection.SCRIPT:          ScriptMaterialiser(...),
            ManifestCategory.MCP:            McpMaterialiser(...),
            ManifestCategory.IDENTITY:       IdentityMaterialiser(...),
            ManifestCategory.SKILLS:         SkillsMaterialiser(...),
            ManifestCategory.RESOURCES:      ResourcesMaterialiser(...),
            ManifestCategory.CLI_TOOLS:      CliToolsMaterialiser(...),
        }

    Six keys, and ``ManifestCategory.ENGINE_CONFIG`` deliberately absent: a
    document declaring it takes the orchestrator's no-materialiser path, which
    is an expected state rather than a gap.

    Called by: the composition root, once, and by the test rigs that assemble
    an engine.

    A function taking its dependencies rather than a module-level dict: the
    materialisers hold service references, and a module-level registry would
    both construct services at import time and pull the bot-configuration graph
    into anything that merely wants the ordering table.

    The map is keyed by each materialiser's own ``construct`` rather than by a
    name written here, so a materialiser cannot be registered under the wrong
    key. The fetch-side dependencies (``package_validator``,
    ``entry_fetcher``) exist because ``skills`` and ``identity`` materialise
    fetched bytes: the validator is the upload path's own gate, the entry
    fetcher is the fetch funnel, and neither belongs inside the engine.
    ``cli_tools`` takes neither — it is handed one dependency, the service both
    *it* and the management API call, which already holds the family's delivery
    port.
    """
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.cli_tools import (
        CliToolsMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.identity import (
        IdentityMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.mcp import (
        McpMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.resources import (
        ResourcesMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.script import (
        ScriptMaterialiser,
    )
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.skills import (
        SkillsMaterialiser,
    )

    materialisers: tuple[Materialiser, ...] = (
        ScriptMaterialiser(script_service),
        McpMaterialiser(activation_service, mcp_auth_service),
        IdentityMaterialiser(identity_service, entry_fetcher),
        SkillsMaterialiser(
            upload_service,
            activation_service,
            capability_reader,
            package_validator,
            entry_fetcher,
        ),
        ResourcesMaterialiser(resource_service, entry_fetcher),
        CliToolsMaterialiser(cli_tool_service),
    )
    return {m.construct: m for m in materialisers}


__all__ = [
    "CategoryPlan",
    "Intent",
    "Materialiser",
    "PlannedEntry",
    "ResolveFailure",
    "ResolveResult",
    "build_materialisers",
]
