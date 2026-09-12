"""The delivery seam (W8): what differs between engine families, and only that.

Three configurations, three phase tables — and the tables live on the
strategies, not on ``APPLY_ORDER``, which carries only construct and position.
ARCA is ``_ARCA_PHASES``: ``script`` before the container, everything else
after. teclaw with the switch on puts every non-script construct before it,
because the artifact is the delivery; with it off, after, because that is the
shape it ran before W8.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    _ARCA_PHASES,
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
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)


def _ports(tag: str) -> MaterialiserPorts:
    return MaterialiserPorts(*([tag] * 10))


_IS_TECLAW = lambda engine: (engine or "").lower() == "teclaw"  # noqa: E731


# ── phase tables ──────────────────────────────────────────────────────────


def test_arca_phases_are_the_strategys_own_table() -> None:
    arca = ArcaDelivery(lambda: _ports("arca"))
    assert [(s.construct, arca.phase_of(s)) for s in APPLY_ORDER] == [
        (ManifestSection.SCRIPT, ApplyPhase.PRE_CONTAINER),
        (ManifestCategory.IDENTITY, ApplyPhase.ON_CONTAINER),
        (ManifestCategory.RESOURCES, ApplyPhase.ON_CONTAINER),
        (ManifestCategory.SKILLS, ApplyPhase.ON_CONTAINER),
        (ManifestCategory.MCP, ApplyPhase.ON_CONTAINER),
        (ManifestCategory.ENGINE_CONFIG, ApplyPhase.ON_CONTAINER),
        (ManifestCategory.CLI_TOOLS, ApplyPhase.ON_CONTAINER),
    ]
    for step in APPLY_ORDER:
        assert arca.phase_of(step) is _ARCA_PHASES[step.construct]
    assert arca.steps_for(None) == tuple(
        sorted(APPLY_ORDER, key=lambda s: s.position)
    )
    assert [s.construct for s in arca.steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))] == [
        ManifestSection.SCRIPT
    ]
    assert arca.creation_sequence is CreationSequence.CREATE_BETWEEN_PHASES
    assert arca.needs_container()


def test_the_arca_phase_table_names_every_construct_in_the_order() -> None:
    """The import-time assertion in ``delivery.py``, named here too: adding a
    construct to ``APPLY_ORDER`` without giving ARCA a phase for it is a
    ``KeyError`` at the first apply that walks it."""
    assert set(_ARCA_PHASES) == {step.construct for step in APPLY_ORDER}


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
    """...with the one exception W9 added: ``cli_tools`` is always
    platform-managed, so it is PRE_CONTAINER even here. Every other non-script
    construct still waits for the container, which is what "the pre-W8 shape"
    meant."""
    teclaw = TeclawDelivery(
        platform_managed=False,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
    )
    on = teclaw.steps_for(frozenset({ApplyPhase.ON_CONTAINER}))
    assert {s.construct for s in on} == {
        s.construct
        for s in APPLY_ORDER
        if s.construct not in (ManifestSection.SCRIPT, ManifestCategory.CLI_TOOLS)
    }
    assert [s.construct for s in teclaw.steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))] == [
        ManifestSection.SCRIPT,
        ManifestCategory.CLI_TOOLS,
    ]
    assert teclaw.creation_sequence is CreationSequence.CREATE_BETWEEN_PHASES
    assert teclaw.needs_container()
    assert teclaw.ports() == _ports("device")


def test_cli_tools_is_on_container_on_arca() -> None:
    """W9 did not change the ARCA reading: an ARCA tool is a write into a live
    container."""
    arca = ArcaDelivery(lambda: _ports("arca"))
    step = next(s for s in APPLY_ORDER if s.construct is ManifestCategory.CLI_TOOLS)
    assert arca.phase_of(step) is ApplyPhase.ON_CONTAINER


@pytest.mark.parametrize("switch", [True, False])
def test_cli_tools_is_pre_container_on_teclaw_under_either_switch(switch) -> None:
    """The artifact is teclaw's delivery and it is composed before
    provisioning, so this category cannot wait for a container — and it must
    not key on the switch, because it is always platform-managed, like ``mcp``.

    On an existing bot the two phases run back to back and the distinction is
    invisible. It decides something on exactly one path: the W13 creation whose
    switch-on sequence has no phase B at all, where an ON_CONTAINER
    ``cli_tools`` would simply never run.
    """
    teclaw = TeclawDelivery(
        platform_managed=switch,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
    )
    step = next(s for s in APPLY_ORDER if s.construct is ManifestCategory.CLI_TOOLS)
    assert teclaw.phase_of(step) is ApplyPhase.PRE_CONTAINER


@pytest.mark.parametrize("switch", [True, False])
def test_a_teclaw_creation_installs_tools_before_it_composes(switch) -> None:
    """The property the phase rule exists for.

    A teclaw creation composes its **first** artifact from platform state; if
    ``cli_tools`` ran after the container, a bot created from a manifest would
    come up without the tools it declared — and under the switch-on sequence it
    would never run at all, because that sequence has no phase B. So the
    category has to be in the phase that runs before provisioning, under either
    switch position.
    """
    teclaw = TeclawDelivery(
        platform_managed=switch,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
    )
    pre = teclaw.steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))
    assert ManifestCategory.CLI_TOOLS in {s.construct for s in pre}


@pytest.mark.parametrize("switch", [True, False])
def test_teclaw_always_gets_the_teclaw_cli_port_whatever_the_switch(switch) -> None:
    """The category is always platform-managed, so its *port* cannot follow the
    switch either.

    With the switch off the device bundle carries the **ARCA** CLI port, which
    would call ARCA-only engine endpoints on a teclaw bot — and, since
    ``phase_of`` puts this category before the container, would run with no
    container to call at all. The strategy substitutes the teclaw port into
    whichever bundle it hands out.
    """
    teclaw_cli = object()
    teclaw = TeclawDelivery(
        platform_managed=switch,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
        cli_tool_service=teclaw_cli,
    )
    ports = teclaw.ports()
    assert ports.cli_tool_service is teclaw_cli
    # Every other port still comes from the bundle the switch selects.
    assert ports.resource_service == ("store" if switch else "device")


def test_arca_keeps_its_own_cli_port() -> None:
    """The substitution is the teclaw strategy's alone — an ARCA bot's tools do
    go into its live container."""
    arca = ArcaDelivery(lambda: _ports("arca"))
    assert arca.ports().cli_tool_service == "arca"


def test_a_teclaw_strategy_with_no_cli_service_bound_leaves_the_bundle_alone() -> None:
    """The bare wiring (no W9 service bound) behaves as it did before."""
    teclaw = TeclawDelivery(
        platform_managed=False,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
    )
    assert teclaw.ports() == _ports("device")


def test_the_arca_table_itself_is_untouched_by_w9() -> None:
    """The per-family rule lives in ``TeclawDelivery``; ARCA's own table still
    says ``ON_CONTAINER``, and the position is the shared one. A change here
    would silently re-phase ARCA too."""
    step = next(s for s in APPLY_ORDER if s.construct is ManifestCategory.CLI_TOOLS)
    assert (_ARCA_PHASES[step.construct], step.position) == (
        ApplyPhase.ON_CONTAINER,
        6,
    )


@pytest.mark.parametrize("switch", [True, False])
def test_script_is_pre_container_on_teclaw_under_either_switch(switch) -> None:
    """``script`` is unsupported on teclaw, but the phase is still answered —
    and answered without consulting any shared table, since ``ApplyStep``
    carries none. A step that fell out of both phases would be skipped
    silently instead of walking the orchestrator's no-support path and being
    reported."""
    teclaw = TeclawDelivery(
        platform_managed=switch,
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
    )
    step = next(s for s in APPLY_ORDER if s.construct is ManifestSection.SCRIPT)
    assert teclaw.phase_of(step) is ApplyPhase.PRE_CONTAINER
    assert ManifestSection.SCRIPT in {
        s.construct for s in teclaw.steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))
    }


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
