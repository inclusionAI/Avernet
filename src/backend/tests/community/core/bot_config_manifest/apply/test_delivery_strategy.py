"""The delivery seam (W8): what differs between engine families, and only that.

Three configurations, three phase tables. ARCA is ``APPLY_ORDER`` verbatim.
teclaw with the switch on puts every non-script construct before the container,
because the artifact is the delivery; with it off, after, because that is the
shape it ran before W8.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    ArcaDelivery,
    CreationSequence,
    DeliveryStrategyFactory,
    MaterialiserPorts,
    TeclawDelivery,
    teclaw_platform_managed_from_config,
)
from agentclaw.community.core.bot_config_manifest.apply.order import (
    ALL_PHASES,
    APPLY_ORDER,
    ApplyPhase,
    steps_for,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)


def _ports(tag: str) -> MaterialiserPorts:
    return MaterialiserPorts(*([tag] * 9))


_IS_TECLAW = lambda engine: (engine or "").lower() == "teclaw"  # noqa: E731


# ── phase tables ──────────────────────────────────────────────────────────


def test_arca_phases_are_the_order_tables_own() -> None:
    arca = ArcaDelivery(lambda: _ports("arca"))
    for step in APPLY_ORDER:
        assert arca.phase_of(step) is step.phase
    assert arca.steps_for(None) == steps_for(None)
    assert arca.steps_for(frozenset({ApplyPhase.PRE_CONTAINER})) == steps_for(
        frozenset({ApplyPhase.PRE_CONTAINER})
    )
    assert arca.creation_sequence is CreationSequence.CREATE_BETWEEN_PHASES
    assert arca.needs_container()


def test_teclaw_on_puts_every_non_script_construct_before_the_container() -> None:
    teclaw = TeclawDelivery(
        platform_managed=True,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
    )
    pre = teclaw.steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))
    assert {s.construct for s in pre} == {
        ManifestSection.SCRIPT,
        ManifestCategory.IDENTITY,
        ManifestCategory.RESOURCES,
        ManifestCategory.SKILLS,
        ManifestCategory.MCP,
        ManifestCategory.ENGINE_CONFIG,
        ManifestCategory.CLI_TOOLS,
    }
    assert teclaw.steps_for(frozenset({ApplyPhase.ON_CONTAINER})) == ()
    # Position order survives the re-phasing.
    assert [s.position for s in pre] == sorted(s.position for s in pre)
    assert teclaw.creation_sequence is CreationSequence.RECORD_APPLY_PROVISION
    assert not teclaw.needs_container()
    assert teclaw.ports() == _ports("store")


def test_teclaw_off_is_the_pre_w8_shape() -> None:
    teclaw = TeclawDelivery(
        platform_managed=False,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
    )
    on = teclaw.steps_for(frozenset({ApplyPhase.ON_CONTAINER}))
    assert {s.construct for s in on} == {
        s.construct for s in APPLY_ORDER if s.construct != ManifestSection.SCRIPT
    }
    assert [s.construct for s in teclaw.steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))] == [
        ManifestSection.SCRIPT
    ]
    assert teclaw.creation_sequence is CreationSequence.CREATE_BETWEEN_PHASES
    assert teclaw.needs_container()
    assert teclaw.ports() == _ports("device")


def test_both_phases_walk_every_construct_on_every_strategy() -> None:
    for strategy in (
        ArcaDelivery(lambda: _ports("a")),
        TeclawDelivery(platform_managed=True, platform_ports=lambda: _ports("s"), device_ports=lambda: _ports("d")),
        TeclawDelivery(platform_managed=False, platform_ports=lambda: _ports("s"), device_ports=lambda: _ports("d")),
    ):
        assert strategy.steps_for(ALL_PHASES) == tuple(
            sorted(APPLY_ORDER, key=lambda s: s.position)
        )


# ── the closing step ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finish_is_the_redeliver_only_when_platform_managed() -> None:
    calls: list[object] = []

    async def redeliver(ctx):
        calls.append(ctx)
        return "note"

    on = TeclawDelivery(
        platform_managed=True,
        platform_ports=lambda: _ports("s"),
        device_ports=lambda: _ports("d"),
        redeliver=redeliver,
    )
    off = TeclawDelivery(
        platform_managed=False,
        platform_ports=lambda: _ports("s"),
        device_ports=lambda: _ports("d"),
        redeliver=redeliver,
    )
    assert await on.finish(object(), object()) == "note"
    assert await off.finish(object(), object()) is None
    assert await ArcaDelivery(lambda: _ports("a")).finish(object(), object()) is None
    assert len(calls) == 1


# ── the factory and the switch ────────────────────────────────────────────


def test_factory_picks_by_the_engine_authority() -> None:
    factory = DeliveryStrategyFactory(
        is_teclaw=_IS_TECLAW,
        teclaw_platform_managed=False,
        arca_ports=lambda: _ports("a"),
    )
    assert isinstance(factory.for_engine("openclaw"), ArcaDelivery)
    assert isinstance(factory.for_engine("claude_code"), ArcaDelivery)
    assert isinstance(factory.for_engine("TeClaw"), TeclawDelivery)
    assert isinstance(factory.for_bot({"active_engine": "teclaw"}), TeclawDelivery)


def test_factory_reads_the_switch_once_and_refuses_an_unbound_platform_path() -> None:
    off = DeliveryStrategyFactory(
        is_teclaw=_IS_TECLAW, teclaw_platform_managed=False, arca_ports=lambda: _ports("a")
    )
    assert not off.for_engine("teclaw").platform_managed
    # On without platform ports is a misconfiguration, not a silent fallback
    # into the container.
    on_unbound = DeliveryStrategyFactory(
        is_teclaw=_IS_TECLAW, teclaw_platform_managed=True, arca_ports=lambda: _ports("a")
    )
    with pytest.raises(RuntimeError):
        on_unbound.for_engine("teclaw")
    on = DeliveryStrategyFactory(
        is_teclaw=_IS_TECLAW,
        teclaw_platform_managed=True,
        arca_ports=lambda: _ports("a"),
        teclaw_platform_ports=lambda: _ports("s"),
    )
    strategy = on.for_engine("teclaw")
    assert strategy.platform_managed
    assert strategy.ports() == _ports("s")
    # ARCA never sees the switch.
    assert isinstance(on.for_engine("openclaw"), ArcaDelivery)


@pytest.mark.parametrize(
    "tree,expected",
    [
        (None, False),
        ({}, False),
        ({"bot_config_manifest": {}}, False),
        ({"bot_config_manifest": {"teclaw_platform_managed": True}}, True),
        ({"bot_config_manifest": {"teclaw_platform_managed": "yes"}}, True),
        ({"bot_config_manifest": {"teclaw_platform_managed": "false"}}, False),
        ({"bot_config_manifest": {"teclaw_platform_managed": "off"}}, False),
        ({"bot_config_manifest": {"teclaw_platform_managed": 0}}, False),
        ({"bot_config_manifest": {"teclaw_platform_managed": 1}}, True),
        ({"bot_config_manifest": {"teclaw_platform_managed": None}}, False),
        ({"bot_config_manifest": "not-a-mapping"}, False),
    ],
)
def test_the_switch_parses_from_the_config_tree(tree, expected) -> None:
    assert teclaw_platform_managed_from_config(tree) is expected


@pytest.mark.parametrize("raw", ["nope", "2", 2, 0.5, [], {}])
def test_a_malformed_switch_raises_rather_than_enabling(raw) -> None:
    with pytest.raises(ValueError):
        teclaw_platform_managed_from_config(
            {"bot_config_manifest": {"teclaw_platform_managed": raw}}
        )
