"""What a fetch reads off its caller, and the store scope it derives.

Two names, held apart from every road that uses them. The dispatcher, both
protocol fetchers and the deliveries each read the caller's context and file
under one scope, so whichever of those modules held them would be imported by
the rest — and the fetch package would close a cycle around it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.content.models import (
    ContentScope,
)

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )


@runtime_checkable
class FetchContext(Protocol):
    """Exactly what a fetch reads off its caller's context, and nothing more.

    Nine attributes, no methods. ``ApplyContext`` satisfies it structurally and
    is the usual one; the ``cli_tools`` service passes its own object, because
    it is called by an HTTP route as well as by a materialiser and both must
    fetch through *this* funnel.

    A minimal satisfying value::

        ctx.bot_id        == "bot_42"
        ctx.entity_id     == "ent_7"
        ctx.env           == "prod"
        ctx.tenant        == "acme"
        ctx.engine_type   == "claude_code"
        ctx.actor_id      == "usr_collaborator"
        ctx.apply_id      == "ap_01HZX8"   # or None
        ctx.budget        == ApplyFetchBudget(...)   # or None
        ctx.source_session == SourceSession(...)     # or None

    The first six are read for the store scope, placeholder substitution and
    the receipt's ``modifier``. The last three are legitimately ``None`` for a
    caller that is not an apply: an unbudgeted single install files a receipt
    with no apply linkage, which is what that column's nullability means.

    Declaring the seam rather than annotating ``"ApplyContext"`` while a second
    type is passed keeps the dependency honest: a maintainer who adds a
    ``ctx.something`` read below adds it here too, and the other caller fails
    to type-check instead of failing at apply time.
    """

    bot_id: str
    #: Storage key for the bot; one axis of the content store's scope.
    entity_id: str
    env: str
    tenant: str
    engine_type: str
    #: Who is fetching. Lands on the receipt as ``modifier``.
    actor_id: str
    #: Stamped into every receipt this fetch files. ``None`` off the apply path.
    apply_id: Optional[str]
    budget: Optional[ApplyFetchBudget]
    source_session: Optional[SourceSession]


def scope_of(ctx: FetchContext) -> ContentScope:
    """The store scope for the bot an apply runs against::

        scope_of(ctx)
        # -> ContentScope(env="prod", entity_id="ent_7", bot_id="bot_42")

    Three axes, all read straight off the context. Every receipt this pipeline
    reads and every one it writes is filed under this scope, so they are one
    log. Called by ``source_fetchers.ObjectStoreFetcher._acquire``,
    ``source_fetchers.GitSourceFetcher._keep_last`` and
    ``entry_delivery.GitDelivery.file``.
    """
    return ContentScope(env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id)


__all__ = [
    "FetchContext",
    "scope_of",
]
