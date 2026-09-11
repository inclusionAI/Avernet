"""Who and what one apply runs as.

Built once at the top of an apply and handed to every materialiser, so that a
materialiser never re-derives an identity. It carries identity, addressing and
resolved capabilities, and nothing else.

A materialiser reaches its area through the owning service
(``BotStartupScriptService``, ``DirectActivationService``), which is where the
write and its guards already live, so there is no addressing helper here. One
that genuinely needs a coordinate imports ``CONFIG_SURFACE`` directly, and
lazily: that table indexes six core packages, one of which reaches the DI
container at import time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from agentclaw.community.core.bot_config_manifest.apply.budget import (  # noqa: F401
    ApplyFetchBudget,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCapabilities,
)

@dataclass(frozen=True)
class ApplyContext:
    """The identity and addressing one apply runs under.

    Built once at the top of an apply and handed to every materialiser's
    ``resolve``, ``plan`` and ``write``. One filled-in example::

        ApplyContext(
            bot_id="bot_42",
            owner_id="usr_owner",
            actor_id="usr_collaborator",
            entity_id="ent_7",
            env="prod",
            tenant="acme",
            engine_type="claude_code",
            bot_type="arca",
            bot={...},                       # the bot record; see below
            capabilities=ManifestCapabilities(...),
            apply_id="ap_01HZX8",            # None on a dry run
            budget=ApplyFetchBudget(...),    # None without a fetch pipeline
            source_session=SourceSession(...),   # None likewise
        )

    ``bot`` is normally the full bot record read from the database. On exactly
    one path it is a **minimal stand-in**: the creation job's pre-container
    phase runs before the bot row exists, so
    ``config_manifest_apply_service`` substitutes a four-key dict::

        {
            "bot_id": "bot_42",
            "entity_id": "ent_7",
            "active_engine": "claude_code",
            "bot_type": "arca",
        }

    No shipped materialiser reads ``bot``, and the one that runs in that phase
    (``script``) reads only ``engine_type``, ``env`` and ``tenant``, so the
    stand-in is sufficient there.

    Created by: ``services/config_manifest_apply_service._context``.
    Consumed by: every materialiser stage, ``apply/orchestrator``, and the
    fetch pipeline through the narrower ``entry_fetch.FetchContext`` protocol.

    ``owner_id`` and ``actor_id`` differ on a shared bot: the bot is resolved as
    the *owner's*, while the actor is whoever is applying. Materialisers that
    call a bot-configuration service pass both, exactly as the routers do.
    """

    bot_id: str
    #: The bot's owner. What the addressed-bot coordinates resolve against.
    owner_id: str
    #: Who is applying. On a shared bot this is a collaborator, not the owner —
    #: the distinction an audit field must not lose.
    actor_id: str
    #: Storage key, resolved server-side from the bot record. Never a request
    #: parameter and never a response field.
    entity_id: str
    #: The deployment environment, e.g. ``"prod"``. One of the three axes
    #: ``${BOT_*}`` placeholders substitute against, and one axis of the
    #: content store's scope.
    env: str
    #: The tenant, e.g. ``"acme"``, read from the ambient request context.
    #: The second placeholder axis.
    tenant: str
    #: The bot's active engine, e.g. ``"claude_code"``. The third placeholder
    #: axis, and what capabilities resolve against.
    engine_type: str
    #: The bot's family, e.g. ``"arca"`` or ``"teclaw"``. Decides which
    #: delivery strategy runs and how the phases are ordered.
    bot_type: str
    #: The bot record, carried rather than re-fetched so a materialiser needing
    #: engine or template facts has them. On the creation job's pre-container
    #: phase this is the four-key stand-in described in the class docstring,
    #: because the row does not exist yet. No shipped materialiser reads it.
    bot: dict[str, Any]
    #: Which constructs this bot can actually take, resolved from
    #: ``engine_type`` and ``bot_type``. A materialiser asks it two things:
    #: ``capabilities.supports(ManifestSection.SCRIPT)`` and, when that is
    #: false, ``capabilities.reason_for(...)`` for the refusal string it puts
    #: on the entry's report row.
    #:
    #: Resolved once per apply and carried, so a single resolution cannot
    #: disagree with itself midway through. Re-asked at apply time rather than
    #: trusted from the ``PUT`` that accepted the document: a bot's engine can
    #: change after a manifest is stored, and the construct that was appliable
    #: then may not be now.
    capabilities: ManifestCapabilities
    #: This apply's own id, e.g. ``"ap_01HZX8"``, stamped into every receipt the
    #: fetch pipeline files so "what did apply X fetch" is an indexed read.
    #: ``None`` only for a dry run, which mints no id by the same rule that
    #: makes it write no report row. The entry-identity half of that linkage is
    #: per fetch and rides the materialiser's call instead.
    apply_id: Optional[str] = None
    #: This apply's fetch allowance: 300 seconds and 500 MiB, from
    #: ``fetch/limits.py``. Mutable by design inside the frozen context —
    #: consult before each fetch, charge after. ``None`` for callers that run
    #: no fetch pipeline (tests, hand-driven use), and every budget check is
    #: written to tolerate that.
    budget: Optional["ApplyFetchBudget"] = None
    #: This apply's named-source state: the document's ``sources`` map, the
    #: strict-mode baselines read back from the last apply's report, and the
    #: git checkout cache. Mutable by design inside the frozen context, for the
    #: same reason as ``budget``: the alternative is state on a DI-singleton
    #: fetcher, which would leak across applies. ``None`` for callers that run
    #: no ``from``/git pipeline; ``fetch_declared`` refuses such entries loudly
    #: rather than fetching anonymously.
    source_session: Optional[SourceSession] = None


__all__ = ["ApplyContext"]
