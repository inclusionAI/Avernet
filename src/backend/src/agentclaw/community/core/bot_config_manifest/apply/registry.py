"""What materialises each construct, and the three-stage contract they honour.

**The registry is sparse on purpose.** ``APPLY_ORDER`` names every construct the
vocabulary defines; this maps only the ones some shipped code can act on. A
construct declared in a document with no entry here is an **expected state**,
not a gap: the orchestrator fails its entries with a readable reason and aborts
the category, so nothing is destroyed, and W5/W6 close the window by registering
a materialiser rather than by deleting a branch.
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

    ``identity`` is what the category keys entries by; ``value`` is whatever the
    materialiser needs to write, already substituted and validated. The
    orchestrator never inspects ``value`` — it is the materialiser's own
    currency — which is what keeps category knowledge out of the orchestrator.

    ``note`` is a successful write's caveat, surfaced by the materialiser on
    the entry's report row — today's only producer is a ``keep_last``
    fallback, whose published contract is that the report states it. It rides
    with the intent because the fetch resolved it and the write reports it,
    and neither stage should reach into the other's currency.
    """

    identity: str
    value: Any = None
    note: Optional[str] = None


@dataclass(frozen=True)
class ResolveFailure:
    """One entry that could not be turned into an intent, and why.

    A single one of these aborts its whole category (§3.2 all-or-nothing): under
    overwrite a partial set is *destructive*, because writing ``{A}`` when the
    declaration was ``{A, B}`` deletes B.
    """

    identity: str
    reason: str


@dataclass(frozen=True)
class ResolveResult:
    """What ``resolve`` learned, keyed so the orchestrator can report per entry.

    Both halves are returned rather than raising on the first problem: a caller
    fixing a document should see every entry that failed, not discover them one
    resubmission at a time.
    """

    intents: tuple[Intent, ...] = ()
    failures: tuple[ResolveFailure, ...] = ()

    @property
    def ok(self) -> bool:
        """True when every declared entry resolved."""
        return not self.failures


@dataclass(frozen=True)
class PlannedEntry:
    """One intent, classified against what is actually there."""

    intent: Intent
    #: ``created`` / ``updated`` / ``unchanged`` — never ``failed`` or
    #: ``skipped``, which are the orchestrator's to assign.
    outcome: str


@dataclass(frozen=True)
class CategoryPlan:
    """What ``write`` would do, computed without doing any of it.

    ``dry_run`` returns after this stage, which is why the stage exists as its
    own call: a preview that cannot write is one that is *missing the call*,
    rather than one that is disciplined about not making it.
    """

    entries: tuple[PlannedEntry, ...] = ()
    #: Identities present in the area and no longer declared — what overwriting
    #: removes. Reported separately from entry outcomes because a removal has no
    #: declared entry to attach to.
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

    Every member is ``@abstractmethod`` and each materialiser **inherits** this
    Protocol — the shape ``BotConfigManifestServiceProtocol`` and the repository
    contracts already use here. Omitting a stage then fails at construction
    naming it, rather than as an ``AttributeError`` the first time a category
    reaches that stage: for ``write``, that would be mid-apply on a real bot,
    after ``resolve`` and ``plan`` had already succeeded.
    """

    #: Which construct this materialises. Read by the registry test that pins
    #: every key here to an ``APPLY_ORDER`` row.
    construct: ApplyConstruct

    @abstractmethod
    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Declared entries → intents.

        Everything that can fail **before touching the bot** fails here:
        placeholder substitution, the W10 seam's validators, permission checks.
        W5's fetch lands in this stage and nowhere else — which is why the
        transient-failure criterion is satisfied for W5 by construction rather
        than by W5 remembering to satisfy it.
        """
        ...

    @abstractmethod
    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Read current state, classify each intent, and compute removals.

        **Read-only.** Nothing here writes, and ``dry_run`` stops after it.
        """
        ...

    @abstractmethod
    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Execute the plan.

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
    """The registry, built from injected services.

    A function taking its dependencies rather than a module-level dict: the
    materialisers hold service references, and a module-level registry would
    both construct services at import time (``test_no_module_level_service_instances``
    exists for that class of thing) and pull the bot-configuration graph into
    anything that merely wants the ordering table.

    **W4 registered two, W5 four, W6 five, W9 six.** The map is keyed by each
    materialiser's own ``construct`` rather than by a name written here — so
    a materialiser cannot be registered under the wrong key. The fetch-side
    dependencies (``package_validator``, ``entry_fetcher``) exist because the
    two W5 categories materialise fetched bytes: the validator is the upload
    path's own gate, the entry fetcher is the W2/W3/W11 funnel, and neither
    belongs inside the engine. ``cli_tools`` (W9) takes neither: it is handed one
    dependency, the service both *it* and the management API call, which already
    holds the family's delivery port — so the family difference stays where W6
    put it. ``engine_config`` arrives when X2/T3 lets it back in; until then a
    document declaring it takes the orchestrator's no-materialiser path: an
    expected state, not a gap.
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
