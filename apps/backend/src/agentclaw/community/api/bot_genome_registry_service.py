"""Service API Protocol of the Genome Registry (RSI-03).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_genome/genome_registry_protocol.py``) so the concrete service
can inherit it without a ``core -> api`` waiver; adapters import it from
here, as for the Manifest contracts.
"""

from __future__ import annotations

from agentclaw.community.core.bot_genome.genome_registry_protocol import (
    GenomeRegistryProtocol,
)

__all__ = ["GenomeRegistryProtocol"]
