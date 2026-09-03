"""Who and what one apply runs as.

Built once at the top of an apply and handed to every materialiser, so that a
materialiser never re-derives an identity.

It carries identity and resolved capabilities, and nothing else. A ``coords_for``
helper wrapping W10's ``CONFIG_SURFACE`` lived here and has been removed: no
materialiser called it. The two that ship reach their area through the owning
service (``BotStartupScriptService``, ``DirectActivationService``), which is
where the write and its guards already live, so a second addressing path was
speculative. W5/W6 can reach ``CONFIG_SURFACE`` directly when a materialiser
genuinely needs a coordinate — importing it lazily, because that table indexes
six core packages and one of them reaches the DI container at import time.
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
    env: str
    tenant: str
    engine_type: str
    bot_type: str
    #: The bot record. Carried rather than re-fetched so a materialiser that
    #: needs engine or template facts has them; unread by the two that ship.
    bot: dict[str, Any]
    #: Resolved once per apply and carried, rather than re-resolved per
    #: materialiser. Two reasons: the resolver needs the teclaw engine test
    #: injected, and a materialiser reaching for it would drag that dependency
    #: into every one of them; and a single resolution cannot disagree with
    #: itself midway through an apply.
    #:
    #: Re-asked at apply time rather than trusted from the ``PUT`` that accepted
    #: the document: a bot's engine can change after a manifest is stored, and
    #: the construct that was appliable then may not be now.
    capabilities: ManifestCapabilities
    #: The apply's own id, stamped into every receipt the fetch pipeline files
    #: (W11's linkage column) — so "what did apply X fetch" is an indexed read,
    #: which that table's own DDL says is why the column exists. ``None`` only
    #: for a dry run, which mints no id by the same rule that makes it write
    #: no report row; the entry identity half of the linkage is per fetch and
    #: rides the materialisers' call instead.
    apply_id: Optional[str] = None
    #: One apply's fetch allowance — the ledger that makes the fetch-time
    #: budget and byte cap of ``fetch/limits.py`` real (an audit caught them
    #: defined but threaded by nothing, while W5's fetches could legitimately
    #: outrun the 30-minute apply-lock TTL). Mutable by design inside the
    #: frozen context: consult before each fetch, charge after. ``None`` for
    #: callers that run no fetch pipeline (tests, hand-driven use).
    budget: Optional["ApplyFetchBudget"] = None
    #: One apply's named-source state (W7): the document's ``sources``, the
    #: strict-mode baselines read back from the last apply's report, and the
    #: git checkout cache. Mutable by design inside the frozen context — the
    #: same ruling as ``budget``, and for the same reason: the alternative is
    #: state on a DI-singleton fetcher, which would leak across applies.
    #: ``None`` for callers that run no ``from``/git pipeline (tests,
    #: hand-driven use); ``fetch_declared`` refuses such entries loudly.
    source_session: Optional[SourceSession] = None


__all__ = ["ApplyContext"]
