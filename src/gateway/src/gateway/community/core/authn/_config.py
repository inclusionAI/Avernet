"""Parse ``authn.yaml`` into an ordered strategy registry (spec rev 3).

``authn.yaml`` maps each identity type to an ordered list of strategy names.
The composition root supplies the strategy pool (name -> instance); this module
resolves the names into the ordered ``dict[PrincipalType, tuple[AuthStrategy]]``
the runner consumes, validating at build time that every name exists in the pool
and that a plugin's ``principal_type`` matches the chain it was placed in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from gateway.community.spi.authn import AuthStrategy, PrincipalType

from ._parsing import parse_principal_type

# Parsed chains: identity type -> ordered list of strategy names.
Chains = dict[PrincipalType, list[str]]


def load_chains(path: str | Path) -> Chains:
    """Load and validate the type -> [strategy-name] chains from ``authn.yaml``."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    table = raw.get("identity_strategies", {})
    chains: Chains = {}
    for type_name, spec in table.items():
        ptype = parse_principal_type(type_name, source="authn.yaml")
        chain = _parse_chain(spec)
        if not chain:
            raise ValueError(f"empty strategy chain for type {type_name!r}")
        chains[ptype] = chain
    return chains


def build_strategy_registry(
    chains: Chains, pool: dict[str, AuthStrategy]
) -> dict[PrincipalType, tuple[AuthStrategy, ...]]:
    """Resolve named chains against the strategy pool into an ordered registry.

    Raises ``ValueError`` if a name is missing from the pool or a plugin's
    ``principal_type`` does not match the chain's type.
    """
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]] = {}
    for ptype, names in chains.items():
        ordered: list[AuthStrategy] = []
        for name in names:
            strategy = pool.get(name)
            if strategy is None:
                raise ValueError(
                    f"authn.yaml references unknown strategy {name!r} for type {ptype}"
                )
            if strategy.principal_type is not ptype:
                raise ValueError(
                    f"strategy {name!r} has type {strategy.principal_type}, "
                    f"cannot be in the {ptype} chain"
                )
            ordered.append(strategy)
        registry[ptype] = tuple(ordered)
    return registry


# ── parsing helpers ──────────────────────────────────────────────────────────


def _parse_chain(spec: Any) -> list[str]:
    chain = (spec or {}).get("chain", [])
    if not isinstance(chain, list) or not all(isinstance(x, str) for x in chain):
        raise ValueError("authn.yaml chain must be a list of strategy-name strings")
    return list(chain)
