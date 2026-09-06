"""Every device port implementation forwards its call faithfully.

The four ``Device*`` classes are pure delegation: each takes the service the
ARCA path already used and re-exposes the narrow port over it. Nothing about
that is visible to the architecture gate, which checks that a port is declared,
that its members are abstract and that the class is constructable — all true of
a delegate that passes the wrong argument, drops a keyword, or picks the wrong
delivery behaviour.

So this drives every method of every port through a recording double and
asserts the inner service saw exactly what the caller passed. The cases are
generated from each port's own signature rather than written out, so a method
added to a port cannot be forgotten here: the parametrisation grows with it.

``project`` is checked separately and explicitly, because it is the one value
a delegate *adds* rather than forwards, and the whole reason the activation
pair exists — ``DeviceActivation`` projects onto the live container,
``PlatformActivation`` records only. Swapping them would silently change what a
manifest apply does to a running bot.

The platform implementations are not here. Three of them are not delegates at
all — they write to the managed-files store — and their behaviour is covered
against a real store in ``managed_files/test_store_ports.py`` and
``test_skill_port.py``. ``PlatformActivation`` is a delegate, and its
``project=False`` is pinned in ``test_teclaw_delivery.py``; it is repeated in
the ``project`` case below so the pair is asserted together.
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
from agentclaw.community.core.ports.activation_port import ActivationPort
from agentclaw.community.core.ports.identity_file_port import IdentityFilePort
from agentclaw.community.core.ports.resource_file_port import ResourceFilePort
from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)


class _Recorder:
    """Accepts any call the port declares and records how it arrived."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"ok": True}

        async def record_async(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"ok": True}

        # The port decides which methods are coroutines; mirror that so the
        # delegate's `await` lands on an awaitable and its `return` does not.
        return record_async if name in self._async_names else record

    _async_names: frozenset[str] = frozenset()


def _sentinels(port, method_name: str) -> dict:
    """One distinguishable value per parameter, from the port's own signature.

    Values are keyed to the parameter name, so a delegate that swaps two
    arguments of the same type — the failure a hand-written test with
    plausible-looking values misses — shows up as a mismatch.
    """
    sig = inspect.signature(getattr(port, method_name))
    out = {}
    for name, p in sig.parameters.items():
        if name == "self":
            continue
        annotation = str(p.annotation)
        if "bytes" in annotation:
            out[name] = f"<{name}>".encode()
        elif "bool" in annotation:
            out[name] = True
        elif "Mapping" in annotation or "dict" in annotation:
            out[name] = {"marker": name}
        else:
            out[name] = f"<{name}>"
    return out


def _port_methods(port) -> list[str]:
    return sorted(m for m in vars(port) if not m.startswith("_") and callable(vars(port)[m]))


#: (port, device implementation). Every field of MaterialiserPorts that is
#: typed by a port and bound to a delegate on the ARCA path.
_DEVICE_IMPLS = [
    (ActivationPort, DeviceActivation),
    (IdentityFilePort, DeviceIdentity),
    (ResourceFilePort, DeviceResource),
    (SkillPackageUploadPort, DeviceSkillPackageUpload),
]

_CASES = [
    pytest.param(port, impl, method, id=f"{impl.__name__}.{method}")
    for port, impl in _DEVICE_IMPLS
    for method in _port_methods(port)
]


@pytest.mark.parametrize(("port", "impl", "method"), _CASES)
@pytest.mark.asyncio
async def test_device_delegate_forwards_every_argument(port, impl, method) -> None:
    """What the caller passed is what the wrapped service receives."""
    recorder = _Recorder()
    async_names = {
        m for m in _port_methods(port)
        if inspect.iscoroutinefunction(getattr(port, m))
    }
    recorder._async_names = frozenset(async_names)

    delegate = impl(recorder)
    supplied = _sentinels(port, method)

    result = getattr(delegate, method)(**supplied)
    if inspect.isawaitable(result):
        await result

    assert recorder.calls, (
        f"{impl.__name__}.{method} did not call the wrapped service at all."
    )
    called, args, kwargs = recorder.calls[-1]
    assert called == method, (
        f"{impl.__name__}.{method} forwarded to {called!r} instead."
    )

    # Positional or keyword is the service's business; what matters is that
    # every value arrived, under the name the port gave it.
    inner_sig = inspect.signature(getattr(port, method))
    positional = [n for n in inner_sig.parameters if n != "self"]
    arrived = dict(zip(positional, args))
    arrived.update(kwargs)

    for name, value in supplied.items():
        assert name in arrived, (
            f"{impl.__name__}.{method} dropped {name!r} on the way through."
        )
        assert arrived[name] == value, (
            f"{impl.__name__}.{method} forwarded {name!r} as {arrived[name]!r}, "
            f"not the {value!r} it was given — arguments may be swapped."
        )


@pytest.mark.parametrize(
    ("impl", "expected"), [(DeviceActivation, True), (PlatformActivation, False)],
    ids=["device projects", "platform records only"],
)
@pytest.mark.parametrize(
    "method", ["activate_mcp", "deactivate_mcp", "activate_skill", "deactivate_skill"]
)
@pytest.mark.asyncio
async def test_activation_delegate_pins_project(impl, expected, method) -> None:
    """The one value a delegate adds rather than forwards.

    ``project`` decides whether the write reaches the running container. It is
    the delivery family's choice, fixed by which delegate the strategy binds —
    so a delegate forwarding the other family's value would silently change
    what an apply does to a live bot, with every other test still passing.
    """
    recorder = _Recorder()
    recorder._async_names = frozenset(
        {"activate_mcp", "deactivate_mcp", "activate_skill", "deactivate_skill"}
    )
    delegate = impl(recorder)

    await getattr(delegate, method)(**_sentinels(ActivationPort, method))

    _, _, kwargs = recorder.calls[-1]
    assert kwargs.get("project") is expected, (
        f"{impl.__name__}.{method} forwarded project={kwargs.get('project')!r}; "
        f"this family requires {expected!r}."
    )


@pytest.mark.parametrize(("port", "impl"), _DEVICE_IMPLS, ids=lambda o: getattr(o, "__name__", str(o)))
def test_device_delegate_adds_no_surface_of_its_own(port, impl) -> None:
    """A delegate re-exposes the port and nothing else.

    The point of each wrapper is to narrow — 15 methods to 3 for identity, 7 to
    3 for resources. A public method beyond the port's would be surface the
    materialiser was meant not to reach.

    Walks the MRO rather than the class's own ``__dict__``: the activation
    delegates define none of their six methods themselves — they inherit every
    one from ``_DelegatingActivation`` and add only ``_PROJECT`` — so a
    ``vars()`` check would find nothing to compare and pass whatever the shared
    base exposed.
    """
    extra = {
        m for m in dir(impl)
        if not m.startswith("_") and callable(getattr(impl, m))
    } - set(_port_methods(port))
    assert not extra, (
        f"{impl.__name__} exposes {sorted(extra)} beyond {port.__name__}."
    )
