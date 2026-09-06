"""Arch gate: every implementer of an outbound port declares it in its bases.

An outbound port (``core/ports``, see its README) is the narrow contract a
caller states for something it calls out to. Python would let any object
satisfy one by shape, and this backend runs no static type checker
(mypy/pyright are configured for ``src/gateway``, ``src/baas`` and
``src/proxy``, not here), so a structurally-satisfied port is verified by
nothing: not at import, not at construction, not in CI. The relationship is
then discoverable only by walking the DI graph — field type, to the provider
that fills it, to the ``binder.bind`` that resolves it — which is four hops to
learn something a base class states in one word.

So the rule is nominal: **every implementer names the port it fills.**

The abstractness assertion is what makes that load-bearing. A Protocol whose
members are plain ``...`` stubs gates nothing on inheritance: drop a method and
the stub is inherited in its place, so the name still resolves, the call still
type-checks by shape, and the method silently returns ``None`` — the failure
mode ``test_protocol_base_ordering.py`` was written after shipping. Only
``@abstractmethod`` members turn a missing method into a construction error.

The last test pins the narrowing itself. ``ActivationPort`` omits ``project``
because whether a write projects to a running container is the delivery
strategy's choice, not a materialiser's; a port that grew the parameter would
hand that choice back to the callers it was built to keep it from.
"""

from __future__ import annotations

import inspect

import pytest

from agentclaw.community.core.bot_config_manifest.apply.activation_delegates import (
    ProjectingActivation,
    RecordOnlyActivation,
)
from agentclaw.community.core.ports.activation_port import ActivationPort

#: (port, implementer) for every class bound to an outbound port field.
_PORT_IMPLEMENTERS = [
    (ActivationPort, ProjectingActivation),
    (ActivationPort, RecordOnlyActivation),
]

#: Every outbound port. Members must be abstract for the declarations to gate.
_PORTS = [ActivationPort]


@pytest.mark.parametrize(
    ("port", "impl"), _PORT_IMPLEMENTERS, ids=lambda o: getattr(o, "__name__", str(o))
)
def test_implementer_declares_the_port_in_its_bases(port, impl) -> None:
    """Nominal, not structural: the port must be in the MRO.

    ``issubclass`` against a ``runtime_checkable`` Protocol passes on method
    names alone, so it stays green after someone deletes the base class. Only
    the MRO check pins the declaration.
    """
    assert port in impl.__mro__, (
        f"{impl.__name__} is bound to a {port.__name__} field but does not declare "
        f"{port.__name__} in its bases. Add the base rather than relying on "
        f"structural typing — this codebase runs no static type checker to catch "
        f"the drift."
    )


@pytest.mark.parametrize("port", _PORTS, ids=lambda p: p.__name__)
def test_port_members_are_abstract(port) -> None:
    """Every public member abstract, so a dropped method fails at construction."""
    public = {
        name
        for name, member in vars(port).items()
        if not name.startswith("_") and callable(member)
    }
    non_abstract = sorted(public - set(port.__abstractmethods__))
    assert not non_abstract, (
        f"{port.__name__} members {non_abstract} are plain `...` stubs. An "
        f"implementer that drops one inherits the stub and silently returns None; "
        f"mark them @abstractmethod so the omission fails at construction."
    )


@pytest.mark.parametrize(
    ("port", "impl"), _PORT_IMPLEMENTERS, ids=lambda o: getattr(o, "__name__", str(o))
)
def test_implementer_is_constructable(port, impl) -> None:
    """No leftover abstract members — the gate above is armed, not tripped."""
    assert not impl.__abstractmethods__, (
        f"{impl.__name__} does not implement {sorted(impl.__abstractmethods__)} "
        f"from {port.__name__}; it cannot be instantiated."
    )


@pytest.mark.parametrize(
    "method_name",
    ["activate_mcp", "deactivate_mcp", "activate_skill", "deactivate_skill"],
)
def test_activation_port_never_exposes_project(method_name) -> None:
    """The narrowing is the point: callers must not be able to pick projection.

    ``project`` selects whether a write reaches the running container. That is
    a property of the delivery family — ARCA writes into a live container,
    platform-managed teclaw closes with one whole-artifact redeliver — and it
    is settled by which delegate the strategy binds, not per call site. If the
    port grew the parameter, a materialiser could override the family's choice.
    """
    for holder in (ActivationPort, ProjectingActivation, RecordOnlyActivation):
        params = inspect.signature(getattr(holder, method_name)).parameters
        assert "project" not in params, (
            f"{holder.__name__}.{method_name} exposes `project`. The port and its "
            f"delegates deliberately do not: the delivery strategy chooses by "
            f"binding ProjectingActivation or RecordOnlyActivation."
        )
