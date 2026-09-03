"""Who and what one CLI-tool operation runs as (W9, issue #1477).

Built by whichever caller is asking — an HTTP route or the manifest's
``cli_tools`` materialiser — and handed down unchanged to the service, the
store and the family's delivery port, so that no layer re-derives an identity.

It is deliberately the *apply* context's vocabulary narrowed to what a tool
operation needs: ``owner_id`` resolves the bot and its device binding,
``actor_id`` is who asked and what the row's audit columns record, and
``entity_id`` / ``entity_type`` are the storage coordinates. ``engine_type``
is carried but read by nobody here: the family difference lives in *which*
delivery port the strategy bound, never in a branch inside one.

It also satisfies ``apply/entry_fetch.py``'s :class:`FetchContext`, which is
what lets an HTTP-driven install fetch through the *same* funnel a manifest
apply does. ``apply_id``, ``budget`` and ``source_session`` are the three
fields only an apply has, and they are ``None`` for the API caller: an
unbudgeted single install files a receipt with no apply linkage, which is
exactly what that column's nullability means.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.bot_config_manifest.cli_tools.store import CliToolScope

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )
    from agentclaw.community.core.bot_config_manifest.apply.source_session import (
        SourceSession,
    )


@dataclass(frozen=True)
class CliToolContext:
    """The identity one install, removal or listing runs under."""

    bot_id: str
    #: The bot's owner. What the bot record and its device binding resolve
    #: against — never the actor, who may be a collaborator on a shared bot.
    owner_id: str
    #: Who is asking. The row's ``installed_by`` / ``modifier``; a manifest
    #: apply passes ``INSTALLED_BY_MANIFEST`` rather than a person.
    actor_id: str
    #: Storage key, resolved server-side from the bot record.
    entity_id: str
    env: str
    #: Carried for logs and for the report; the port a strategy bound is what
    #: actually decides how a tool is delivered.
    engine_type: str
    #: The tenant the fetch pipeline substitutes into ``${BOT_TENANT}``.
    tenant: str
    #: The personal-bot surface's fixed pair, as the identity and resources
    #: materialisers address every bot.
    entity_type: str = "staff"
    #: The three an apply has and a single API-driven install does not.
    apply_id: Optional[str] = None
    budget: Optional["ApplyFetchBudget"] = None
    source_session: Optional["SourceSession"] = None

    @property
    def scope(self) -> CliToolScope:
        """The object-store coordinates for this bot."""
        return CliToolScope(
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            bot_id=self.bot_id,
        )


__all__ = ["CliToolContext"]
