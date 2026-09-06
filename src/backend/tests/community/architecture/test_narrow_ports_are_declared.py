"""Arch gate: an object bound to a narrow port declares that port in its bases.

The apply engine hands its materialisers narrow write ports rather than whole
services — ``MaterialiserPorts.activation_service`` is typed ``ActivationPort``,
and two different objects are bound to it depending on the delivery family.
Python would accept either by shape alone, and the backend runs no static type
checker (mypy/pyright are configured for ``src/gateway``, ``src/baas`` and
``src/proxy``, not here), so a structurally-satisfied port is verified by
nothing: not at import, not at construction, not in CI. The relationship is
then discoverable only by walking the DI graph — field type, to the provider
that fills it, to the ``binder.bind`` that resolves it — which is four hops to
learn something a base class says in one word.

So the rule is nominal, Java-style: **every implementer names the port it
fills.** ``DirectActivationService`` names it transitively, because its own
Protocol widens the port (adding ``project``, which the record-only path
cannot offer); ``RecordOnlyActivation`` names it directly.

The second assertion is what makes the first load-bearing. A Protocol whose
members are plain ``...`` stubs does not gate anything on inheritance: drop a
method and the stub is inherited in its place, so the name still resolves, the
call still type-checks by shape, and the method silently returns ``None`` — the
failure mode ``test_protocol_base_ordering.py`` was written after shipping. Only
``@abstractmethod`` members turn a missing method into a construction error.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.apply.record_only_activation import (
    RecordOnlyActivation,
)
from agentclaw.community.core.skill_center.activation_port import ActivationPort
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)

#: (port, implementer) for every object bound to a narrow port field.
_PORT_IMPLEMENTERS = [
    (ActivationPort, DirectActivationService),
    (ActivationPort, RecordOnlyActivation),
]

#: Ports whose members must stay abstract for the declarations above to gate.
_ABSTRACT_PORTS = [ActivationPort, DirectActivationServiceProtocol]


@pytest.mark.parametrize(
    ("port", "impl"),
    _PORT_IMPLEMENTERS,
    ids=lambda o: getattr(o, "__name__", str(o)),
)
def test_implementer_declares_the_port_in_its_bases(port, impl) -> None:
    """Nominal, not structural: the port must be in the MRO.

    ``issubclass`` against a ``runtime_checkable`` Protocol passes on method
    names alone, so it would stay green after someone deleted the base class.
    The MRO check is what pins the declaration.
    """
    assert port in impl.__mro__, (
        f"{impl.__name__} is bound to a {port.__name__} field but does not declare "
        f"{port.__name__} in its bases (directly or through a Protocol that widens "
        f"it). Add the base rather than relying on structural typing — this "
        f"codebase runs no static type checker to catch the drift."
    )


@pytest.mark.parametrize("port", _ABSTRACT_PORTS, ids=lambda p: p.__name__)
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
    ("port", "impl"),
    _PORT_IMPLEMENTERS,
    ids=lambda o: getattr(o, "__name__", str(o)),
)
def test_implementer_is_constructable(port, impl) -> None:
    """No leftover abstract members — the gate above is armed, not tripped."""
    assert not impl.__abstractmethods__, (
        f"{impl.__name__} does not implement {sorted(impl.__abstractmethods__)} "
        f"from {port.__name__}; it cannot be instantiated."
    )
