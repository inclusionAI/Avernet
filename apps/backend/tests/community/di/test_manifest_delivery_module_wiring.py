"""Which delivery strategy each family — and each teclaw mode — is wired to (W8).

The composition root no longer turns the deployment's
:class:`TeclawDeliveryMode` into a strategy by looking it up in a table. It has
a binding per strategy, and one provider per mode that contributes its
strategy to the :data:`DeliveryStrategies` map only when the deployment named
that mode. That moves two guarantees the retired table's import-time
exhaustiveness check used to give onto this file, asserted over the real graph
instead:

* every member of the enum names a strategy — a mode a deployment can write
  into its yaml and nothing can build is "the surface accepts what it cannot
  run", the rule this feature is built around;
* exactly one teclaw strategy reaches the family map, so the two providers can
  never both be effective and leave which row won to the order the module
  happens to install its providers in.

The mode is rebound on the injector rather than written into a yaml overlay
because it is the config object's value that every provider here reads, and the
point is which strategy comes out of the graph — not how the yaml scalar got
parsed, which ``delivery_mode``'s own tests cover.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    ArcaDelivery,
    DeliveryStrategies,
    DeliveryStrategyFactory,
    DevicePorts,
    EngineFamily,
    PlatformPorts,
    TeclawDeliveryMode,
    TeclawDeviceDelivery,
    TeclawPlatformDelivery,
)
from agentclaw.community.di import config as cfg

#: The strategy each mode is expected to put in the family map. One row per
#: mode, and the test below asserts the enum has no member this table misses —
#: so adding a mode without wiring it a provider fails here rather than at the
#: first apply on a deployment that set it.
_EXPECTED = {
    TeclawDeliveryMode.PLATFORM: TeclawPlatformDelivery,
    TeclawDeliveryMode.DEVICE: TeclawDeviceDelivery,
}


def _strategies(test_injector, mode: TeclawDeliveryMode):
    test_injector.binder.bind(
        cfg.BotConfigManifestConfig,
        to=cfg.BotConfigManifestConfig(teclaw_delivery_mode=mode),
    )
    return test_injector.get(DeliveryStrategies)


def test_every_teclaw_mode_binds_a_strategy() -> None:
    """The coverage the retired selection table checked at import."""
    assert set(_EXPECTED) == set(TeclawDeliveryMode)


@pytest.mark.parametrize("mode", list(TeclawDeliveryMode))
def test_the_deployments_mode_names_the_teclaw_strategy(test_injector, mode) -> None:
    """One provider is effective per mode, and it is the one for that mode.

    Asserted through the injector rather than by reading the module: what
    matters is the object an apply would be handed.
    """
    strategies = _strategies(test_injector, mode)

    assert isinstance(strategies[EngineFamily.TECLAW], _EXPECTED[mode])
    # Not merely "the expected class is in there": no other row may hold a
    # teclaw shape either, or which one a teclaw bot got would depend on the
    # order the module happened to install its providers in.
    others = [s for f, s in strategies.items() if f is not EngineFamily.TECLAW]
    assert not any(
        isinstance(s, (TeclawPlatformDelivery, TeclawDeviceDelivery)) for s in others
    )


@pytest.mark.parametrize("mode", list(TeclawDeliveryMode))
def test_every_family_is_bound_whatever_the_mode(test_injector, mode) -> None:
    """ARCA's row is not the teclaw switch's business, and the factory refuses a
    family with no strategy — so this is what makes the factory buildable at
    all."""
    strategies = _strategies(test_injector, mode)

    assert set(strategies) == set(EngineFamily)
    assert isinstance(strategies[EngineFamily.ARCA], ArcaDelivery)


def test_the_factory_is_handed_the_bound_strategies_and_builds_none(
    test_injector,
) -> None:
    """``manifest_delivery_strategies`` assembles nothing.

    The strategy a bot applies through is *identically* the object the graph
    bound, and the family adapter comes off its own binding — which is what
    lets a test swap either without reaching into the factory.
    """
    strategies = _strategies(test_injector, TeclawDeliveryMode.DEVICE)
    factory = test_injector.get(DeliveryStrategyFactory)

    assert factory.for_family(EngineFamily.ARCA) is strategies[EngineFamily.ARCA]
    assert factory.for_family(EngineFamily.TECLAW) is strategies[EngineFamily.TECLAW]
    # And the lookup answers off the engine authority the rest of the platform
    # asks, adapted once at its own binding.
    assert isinstance(factory.for_engine("teclaw"), TeclawDeviceDelivery)
    assert isinstance(factory.for_engine("claude_code"), ArcaDelivery)
    assert isinstance(factory.for_engine(None), ArcaDelivery)


def test_the_device_bundle_is_one_binding_both_shapes_read(test_injector) -> None:
    """ARCA and the device-backed teclaw shape write through the same ports.

    They are two strategies over one bundle, not two bundles that have to agree:
    the binding is the seam, so a deployment cannot end up with the ``arca``
    CLI binding on one and something else on the other. The qualifier is what
    keeps that bundle distinct from the store-backed one, which has the same
    ``Callable[[], MaterialiserPorts]`` shape and would otherwise share its key.
    """
    strategies = _strategies(test_injector, TeclawDeliveryMode.DEVICE)
    device_ports = test_injector.get(DevicePorts)

    assert strategies[EngineFamily.ARCA]._ports is device_ports
    assert strategies[EngineFamily.TECLAW]._ports is device_ports


def test_the_two_port_bundles_are_not_the_same_binding(test_injector) -> None:
    """The bug the qualifiers exist to prevent.

    Both bundles are ``Callable[[], MaterialiserPorts]``. Bound on that bare
    shape they would be one key, and injector would silently let whichever
    provider the module installed last answer for both — a teclaw bot writing
    into a container, or an ARCA bot writing into the platform's store, with
    nothing in the graph to say so.
    """
    device = test_injector.get(DevicePorts)
    platform = test_injector.get(PlatformPorts)

    assert device is not platform
    assert DevicePorts != PlatformPorts


@pytest.mark.parametrize("mode", list(TeclawDeliveryMode))
def test_the_family_map_carries_the_bound_strategy_object(test_injector, mode) -> None:
    """Each strategy is a binding; the mode decides which one the map names.

    Both teclaw shapes are bound on either mode — a binding is resolvable by
    definition, and the startup lifecycle walk constructs every one of them — so
    what the per-mode providers decide is which *bound object* becomes the
    family's answer. Asserted by identity: the row is the very object the graph
    holds for that class, not a second one assembled for the map.
    """
    strategies = _strategies(test_injector, mode)

    assert strategies[EngineFamily.TECLAW] is test_injector.get(_EXPECTED[mode])
    assert strategies[EngineFamily.ARCA] is test_injector.get(ArcaDelivery)


@pytest.mark.parametrize("mode", list(TeclawDeliveryMode))
def test_the_shape_the_mode_did_not_name_never_reaches_an_apply(
    test_injector, mode
) -> None:
    """It stays resolvable by name and never becomes the family's answer.

    That is the guarantee the apply path actually rests on: an apply asks
    :class:`DeliveryStrategyFactory` for a bot's family, so a shape that is not
    in the map cannot be handed to one however many bindings exist.
    """
    unnamed = next(cls for m, cls in _EXPECTED.items() if m is not mode)
    strategies = _strategies(test_injector, mode)

    assert not isinstance(strategies[EngineFamily.TECLAW], unnamed)
    assert isinstance(test_injector.get(unnamed), unnamed)
