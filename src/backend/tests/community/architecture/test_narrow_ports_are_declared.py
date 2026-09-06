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

**What this file does not check is behaviour.** Every assertion here is
structural: a delegate that forwards the wrong argument, drops a keyword, or
picks the other family's ``project`` satisfies all of it. That is covered by
``core/bot_config_manifest/apply/test_device_port_forwarding.py``, which drives
each port's methods through a recording double, and by the store-backed
implementations' own suites under ``managed_files/``.

The last test pins the narrowing itself. ``ActivationPort`` omits ``project``
because whether a write projects to a running container is the delivery
strategy's choice, not a materialiser's; a port that grew the parameter would
hand that choice back to the callers it was built to keep it from.
"""

from __future__ import annotations

import inspect

import pytest

from agentclaw.community.core.bot_config_manifest.apply.activation_delegates import (
    DeviceActivation,
    PlatformActivation,
)
from agentclaw.community.core.bot_config_manifest.apply.identity_files import (
    DeviceIdentity,
)
from agentclaw.community.core.bot_config_manifest.apply.resource_files import (
    DeviceResource,
)
from agentclaw.community.core.bot_config_manifest.apply.skill_package_upload import (
    DeviceSkillPackageUpload,
)
from agentclaw.community.core.bot_config_manifest.managed_files.ports import (
    PlatformIdentity,
    PlatformResource,
    PlatformSkillPackageUpload,
)
from agentclaw.community.core.ports.activation_port import ActivationPort
from agentclaw.community.core.ports.identity_file_port import IdentityFilePort
from agentclaw.community.core.ports.resource_file_port import ResourceFilePort
from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)

#: (port, implementer) for every class bound to an outbound port field. Each
#: port has exactly two, split on where the write lands — the axis every
#: delivery family splits on: ARCA reaches the bot's container, platform-managed
#: teclaw writes platform state that the composed artifact delivers.
_PORT_IMPLEMENTERS = [
    (ActivationPort, DeviceActivation),
    (ActivationPort, PlatformActivation),
    (SkillPackageUploadPort, DeviceSkillPackageUpload),
    (SkillPackageUploadPort, PlatformSkillPackageUpload),
    (IdentityFilePort, DeviceIdentity),
    (IdentityFilePort, PlatformIdentity),
    (ResourceFilePort, DeviceResource),
    (ResourceFilePort, PlatformResource),
]

#: Every outbound port. Members must be abstract for the declarations to gate.
_PORTS = [ActivationPort, SkillPackageUploadPort, IdentityFilePort, ResourceFilePort]


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
    for holder in (ActivationPort, DeviceActivation, PlatformActivation):
        params = inspect.signature(getattr(holder, method_name)).parameters
        assert "project" not in params, (
            f"{holder.__name__}.{method_name} exposes `project`. The port and its "
            f"delegates deliberately do not: the delivery strategy chooses by "
            f"binding DeviceActivation or PlatformActivation."
        )


def test_upload_port_never_exposes_the_directory_route() -> None:
    """The narrowing is the point: ``upload_local_skill_files`` stays out.

    It converts one browser-selected directory's files into a package — the
    directory-upload route's vocabulary. During an apply the package arrives as
    fetched bytes, so the method has no meaning; a materialiser handed the whole
    Service API could still reach for it. The port is what makes that
    impossible, and neither implementation may quietly re-add it.
    """
    for holder in (
        SkillPackageUploadPort,
        DeviceSkillPackageUpload,
        PlatformSkillPackageUpload,
    ):
        assert not hasattr(holder, "upload_local_skill_files"), (
            f"{holder.__name__} exposes upload_local_skill_files. That method "
            f"belongs to LocalSkillUploadServiceProtocol — the Service API — and "
            f"the port exists to keep it away from the `skills` materialiser."
        )
