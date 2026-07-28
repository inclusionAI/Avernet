"""The public ``cluster_name`` ↔ engine rule for the bots API (Track B).

``cluster_name`` is a public enum in strict bijection with the engine:

- ``ANDC`` ⟺ engine ``teclaw`` (the ``teclaw`` device provider).
- ``ACRA`` ⟺ every other engine (the ARCA / baas default provider).

The pair is fully determined by the engine, so on read the cluster is *derived*
from the bot's ``active_engine``; on create the caller-supplied pair is
*validated* against the same rule. Both directions live here so the rule has a
single source of truth.
"""

from __future__ import annotations

from typing import Literal

from agentclaw.community.adapters.http.openapi_v1.responses import ClusterMismatchError

# Public cluster names. ``ANDC`` is the teclaw cluster; ``ACRA`` is everything
# else. These are external aliases for the internal ``teclaw`` / ARCA providers.
ClusterName = Literal["ACRA", "ANDC"]

TECLAW_ENGINE = "teclaw"
TECLAW_CLUSTER: ClusterName = "ANDC"
DEFAULT_CLUSTER: ClusterName = "ACRA"


def cluster_for_engine(engine: str | None) -> ClusterName:
    """Return the cluster an engine belongs to (``ANDC`` for teclaw, else ``ACRA``)."""
    return TECLAW_CLUSTER if engine == TECLAW_ENGINE else DEFAULT_CLUSTER


def validate_engine_cluster(engine: str | None, cluster: str) -> None:
    """Raise :class:`ClusterMismatchError` if ``cluster`` isn't the engine's cluster."""
    expected = cluster_for_engine(engine)
    if cluster != expected:
        raise ClusterMismatchError(
            f"engine {engine!r} belongs to cluster {expected!r}, not {cluster!r}"
        )
