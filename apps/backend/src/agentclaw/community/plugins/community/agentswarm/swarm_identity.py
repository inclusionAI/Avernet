"""One swarm identity per run, inherited by every persona worker.

A run owns exactly one AgentPass. The first persona to provision establishes it;
every later persona for the same run reuses it, contributes its own declared
resources to the run's scope, and gets a container of its own — without a second
application and without a second human gate.

Why the run's owner is the swarm bot, not each persona:
the identity is addressed by ``bot_id``, so N personas cannot share one identity if
each is its own bot. The swarm bot is the identity; a persona worker is a container
plus a device binding under it, which is the same shape the caller-instance path
already provisions.

Why the roster is discovered rather than declared:
the seam that reaches this adapter (``BotPlatform.create_bot``) carries **one**
persona per call and has no roster concept, so a first version must work while
personas arrive one at a time. The run's scope therefore grows by union on each
arrival. That is safe because the swarm never spends a human decision here: the
scope is submitted once per command and re-submitted only when a new persona
actually adds a resource — never on a timer, never concurrently for the same run.

The one thing a late-arriving persona cannot do is widen an existing grant without
another application. That is the honest cost of discovery over declaration, and it
is why ``_widen`` reports whether it had to re-apply.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: ``apply_reason`` prefix for a worker's device binding. Re-exported from the layer
#: that reads it, so the writer (here) and the reader (the device repository) cannot
#: drift: one literal, one home.
from agentclaw.community.core.repository.implementations.devices.device import (
    WORKER_INSTANCE_APPLY_REASON_PREFIX as SWARM_WORKER_APPLY_REASON_PREFIX,
)


def swarm_bot_id_for(run_id: str) -> str:
    """The run's swarm bot id — deterministic, so a re-run rebinds the same identity.

    Derived rather than allocated: a crashed and replayed run must converge on the
    identity it already minted rather than creating a second one, and the id is
    the Passport key.
    """
    digest = hashlib.sha256(f"swarm:{run_id}".encode("utf-8")).hexdigest()
    return f"agent_swarm_{digest[:16]}"


def worker_apply_reason(swarm_bot_id: str) -> str:
    """The reason an operator reads on a worker's binding row."""
    return f"{SWARM_WORKER_APPLY_REASON_PREFIX}{swarm_bot_id}"


@dataclass(frozen=True)
class SwarmIdentity:
    """What a run's identity answers once established."""

    bot_id: str
    agent_code: str
    mcp_codes: tuple[str, ...] = ()
    cli_items: tuple[Mapping[str, Any], ...] = ()


def _canonical_cli(item: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), repr(v)) for k, v in item.items()))


@dataclass
class _RunScope:
    """The run's accumulated scope, and the lock that keeps one writer at a time."""

    mcp_codes: dict[str, None] = field(default_factory=dict)
    cli_items: dict[tuple[tuple[str, str], ...], Mapping[str, Any]] = field(
        default_factory=dict
    )
    lock: threading.Lock = field(default_factory=threading.Lock)

    def merge(
        self,
        mcp_codes: Sequence[str],
        cli_items: Sequence[Mapping[str, Any]],
    ) -> tuple[bool, tuple[str, ...], tuple[Mapping[str, Any], ...]]:
        """Union the newcomer's resources in; answer whether anything was new."""
        grew = False
        for code in mcp_codes:
            if code and code not in self.mcp_codes:
                self.mcp_codes[code] = None
                grew = True
        for item in cli_items:
            canonical = _canonical_cli(item)
            if canonical not in self.cli_items:
                self.cli_items[canonical] = item
                grew = True
        return grew, tuple(self.mcp_codes), tuple(self.cli_items.values())