"""Bounded filter fields shared by search transports."""

from __future__ import annotations

# Search-core intentionally does not know every application's filter schema. This
# tuple is the cross-transport subset that indexed stores must support. Host
# applications may add fields, but those additions are outside this package's
# performance contract.
SEARCH_FILTER_FIELDS = (
    "availability",
    "runtime_state",
    "domains",
    "trust_level",
    "worker_id",
    "profile_key",
)

__all__ = ["SEARCH_FILTER_FIELDS"]
