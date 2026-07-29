"""Service API gate: every registered Protocol matches its concrete service.

``api/README.md`` has promised this file in two places since the Service API
layer was introduced — it was never written, so nothing checked that a concrete
service still satisfies the Protocol adapters inject. The README states the
contract:

    Conformance is **structural**: concrete services under
    ``core/<module>/services/`` do *not* inherit from the Protocol (that would
    force a ``core → api`` import, which the layering rule forbids). Instead
    ``test_service_api_conformance.py`` parametrizes over every
    ``(Protocol, ConcreteService)`` pair and asserts ``issubclass`` against the
    ``@runtime_checkable`` Protocol — so a missing or renamed method on the
    concrete class fails CI rather than only showing up as a router-time
    ``AttributeError``.

Two checks per pair, because ``issubclass`` on a ``runtime_checkable`` Protocol
verifies method **names only**:

1. ``issubclass`` — catches a removed or renamed method (the README's contract).
2. Signature equality — catches a *renamed parameter*, which the name check
   cannot see. Without it, renaming ``entity_id`` on the implementation passes
   both type checking and this gate while every request fails at runtime.

``_PAIRS`` starts with the contract added in this PR. It is a registry, not a
discovery walk: most Protocols in ``api/`` still declare
``*args: Any, **kwargs: Any``, against which a signature check is vacuous, so
listing them here would assert nothing while implying coverage. Add a pair when
its Protocol is given real signatures.
"""
from __future__ import annotations

import inspect

import pytest

from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.services.engine_config import EngineConfigService


# (Protocol, ConcreteService) pairs whose Protocol declares real signatures.
_PAIRS = [
    (EngineConfigServiceProtocol, EngineConfigService),
]

_IDS = [f"{p.__name__}->{c.__name__}" for p, c in _PAIRS]


def _protocol_methods(protocol: type) -> list[str]:
    """Method names the Protocol declares (excluding typing/object machinery)."""
    return sorted(
        name
        for name, value in vars(protocol).items()
        if callable(value) and not name.startswith("_")
    )


@pytest.mark.unit
@pytest.mark.parametrize(("protocol", "concrete"), _PAIRS, ids=_IDS)
def test_concrete_service_satisfies_protocol(protocol, concrete) -> None:
    """The README's structural gate: a missing/renamed method fails CI."""
    assert issubclass(concrete, protocol), (
        f"{concrete.__name__} no longer satisfies {protocol.__name__}. "
        f"Protocol declares: {_protocol_methods(protocol)}; "
        f"missing on the concrete class: "
        f"{[m for m in _protocol_methods(protocol) if not hasattr(concrete, m)]}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("protocol", "concrete"), _PAIRS, ids=_IDS)
def test_protocol_signatures_match_the_implementation(protocol, concrete) -> None:
    """Parameter names/kinds must match — ``issubclass`` only checks names.

    A renamed keyword argument keeps the method present, so the check above
    stays green while every call through the Protocol raises ``TypeError``.
    """
    mismatches: list[str] = []
    for name in _protocol_methods(protocol):
        impl = getattr(concrete, name, None)
        if impl is None:
            continue  # reported by the issubclass test
        declared = inspect.signature(getattr(protocol, name))
        actual = inspect.signature(impl)
        if list(declared.parameters) != list(actual.parameters):
            mismatches.append(
                f"{name}: protocol {list(declared.parameters)} "
                f"!= impl {list(actual.parameters)}"
            )
    assert not mismatches, (
        f"{concrete.__name__} drifted from {protocol.__name__}:\n  "
        + "\n  ".join(mismatches)
    )


@pytest.mark.unit
def test_registry_is_not_empty() -> None:
    """Guard the guard — an emptied registry would pass everything silently."""
    assert _PAIRS, "no (Protocol, ConcreteService) pairs registered"
