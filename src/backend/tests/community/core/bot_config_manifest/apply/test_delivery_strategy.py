"""The delivery seam (W8): what differs between engine families, and only that.

Three strategies, three phase tables — and the tables live on the strategies,
not on ``APPLY_ORDER``, which carries only construct and position. ARCA is
``_ARCA_PHASES``: ``script`` before the container, everything else after.
``TeclawPlatformDelivery`` puts every construct before it, because the artifact
is the delivery; ``TeclawDeviceDelivery`` puts the rest after, because that is
the shape teclaw ran before W8.

The three are ordinary objects with no mode, switch or flag of their own: a
deployment's choice is made once, by the tables below — the yaml scalar to a
``TeclawDeliveryMode``, the mode to a strategy — and by then there is nothing
left to decide. So these tests name a class where they used to pass an
argument, and the selection is tested where selection happens.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    _ARCA_PHASES,
    _TECLAW_DEVICE_PHASES,
    _TECLAW_PLATFORM_PHASES,
    ArcaDelivery,
    CreationSequence,
    DeliveryStrategyFactory,
    EngineFamily,
    MaterialiserPorts,
    TECLAW_DELIVERY_BY_MODE,
    TeclawDeliveryBindings,
    TeclawDeliveryMode,
    TeclawDeviceDelivery,
    TeclawPlatformDelivery,
    family_from_engine_test,
    teclaw_delivery_for_mode,
    teclaw_delivery_mode_from_config,
)
from agentclaw.community.core.bot_config_manifest.apply.order import (
    APPLY_ORDER,
    ApplyPhase,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)


async def _no_redeliver(ctx) -> None:
    """The closing step, doing nothing — for a test that is not about it."""
    return None


def _ports(tag: str) -> MaterialiserPorts:
    return MaterialiserPorts(*([tag] * 10))


def _arca() -> ArcaDelivery:
    return ArcaDelivery(lambda: _ports("arca"))


def _platform() -> TeclawPlatformDelivery:
    return TeclawPlatformDelivery(
        ports=lambda: _ports("store"), redeliver=_no_redeliver
    )


def _device(cli_tool_service=None) -> TeclawDeviceDelivery:
    return TeclawDeviceDelivery(
        ports=lambda: _ports("device"),
        cli_tool_service=cli_tool_service or (lambda: "teclaw-cli"),
    )


_IS_TECLAW = lambda engine: (engine or "").lower() == "teclaw"  # noqa: E731


# ── phase tables ──────────────────────────────────────────────────────────


def test_arca_phases_are_the_strategys_own_table() -> None:
    arca = _arca()
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
    assert [s.construct for s in arca.steps_for(ApplyPhase.PRE_CONTAINER)] == [
        ManifestSection.SCRIPT
    ]
    assert arca.creation_sequence is CreationSequence.CREATE_BETWEEN_PHASES
    assert arca.needs_container()
    assert arca.family is EngineFamily.ARCA


@pytest.mark.parametrize(
    "name,table",
    [
        ("_ARCA_PHASES", _ARCA_PHASES),
        ("_TECLAW_PLATFORM_PHASES", _TECLAW_PLATFORM_PHASES),
        ("_TECLAW_DEVICE_PHASES", _TECLAW_DEVICE_PHASES),
    ],
)
def test_every_phase_table_names_every_construct_in_the_order(name, table) -> None:
    """The import-time assertion in ``delivery.py``, named here too.

    Adding a construct to ``APPLY_ORDER`` without giving a family a phase for
    it is a ``KeyError`` at the first apply that walks it — on whichever bot
    happened to declare it first, with that bot's apply lock held. Every table
    is checked, not just ARCA's: a phase answered by a lookup has exactly as
    many ways to be incomplete as there are tables.
    """
    assert set(table) == {step.construct for step in APPLY_ORDER}


def test_the_platform_shape_puts_every_construct_before_the_container() -> None:
    teclaw = _platform()
    pre = teclaw.steps_for(ApplyPhase.PRE_CONTAINER)
    assert {s.construct for s in pre} == {
        ManifestSection.SCRIPT,
        ManifestCategory.IDENTITY,
        ManifestCategory.RESOURCES,
        ManifestCategory.SKILLS,
        ManifestCategory.MCP,
        ManifestCategory.ENGINE_CONFIG,
        ManifestCategory.CLI_TOOLS,
    }
    assert teclaw.steps_for(ApplyPhase.ON_CONTAINER) == ()
    # Position order survives the re-phasing.
    assert [s.position for s in pre] == sorted(s.position for s in pre)
    assert teclaw.creation_sequence is CreationSequence.RECORD_APPLY_PROVISION
    assert not teclaw.needs_container()
    assert teclaw.ports() == _ports("store")
    assert teclaw.family is EngineFamily.TECLAW


def test_the_device_shape_is_the_pre_w8_shape() -> None:
    """...with the one exception W9 added: ``cli_tools`` is always
    platform-managed, so it is PRE_CONTAINER even here. Every other non-script
    construct still waits for the container, which is what "the pre-W8 shape"
    meant."""
    teclaw = _device()
    on = teclaw.steps_for(ApplyPhase.ON_CONTAINER)
    assert {s.construct for s in on} == {
        s.construct
        for s in APPLY_ORDER
        if s.construct not in (ManifestSection.SCRIPT, ManifestCategory.CLI_TOOLS)
    }
    assert [s.construct for s in teclaw.steps_for(ApplyPhase.PRE_CONTAINER)] == [
        ManifestSection.SCRIPT,
        ManifestCategory.CLI_TOOLS,
    ]
    assert teclaw.creation_sequence is CreationSequence.CREATE_BETWEEN_PHASES
    assert teclaw.needs_container()
    assert teclaw.family is EngineFamily.TECLAW


def test_cli_tools_is_on_container_on_arca() -> None:
    """W9 did not change the ARCA reading: an ARCA tool is a write into a live
    container."""
    step = next(s for s in APPLY_ORDER if s.construct is ManifestCategory.CLI_TOOLS)
    assert _arca().phase_of(step) is ApplyPhase.ON_CONTAINER


@pytest.mark.parametrize("build", [_platform, _device])
def test_cli_tools_is_pre_container_on_either_teclaw_shape(build) -> None:
    """The artifact is teclaw's delivery and it is composed before
    provisioning, so this category cannot wait for a container — and it must
    not follow the deployment's mode, because it is always platform-managed,
    like ``mcp``.

    On an existing bot the two phases run back to back and the distinction is
    invisible. It decides something on exactly one path: the W13 creation whose
    platform-managed sequence has no phase B at all, where an ON_CONTAINER
    ``cli_tools`` would simply never run.
    """
    step = next(s for s in APPLY_ORDER if s.construct is ManifestCategory.CLI_TOOLS)
    assert build().phase_of(step) is ApplyPhase.PRE_CONTAINER


@pytest.mark.parametrize("build", [_platform, _device])
def test_a_teclaw_creation_installs_tools_before_it_composes(build) -> None:
    """The property the phase rule exists for.

    A teclaw creation composes its **first** artifact from platform state; if
    ``cli_tools`` ran after the container, a bot created from a manifest would
    come up without the tools it declared — and under the platform-managed
    sequence it would never run at all, because that sequence has no phase B.
    So the category has to be in the phase that runs before provisioning, in
    either shape.
    """
    pre = build().steps_for(ApplyPhase.PRE_CONTAINER)
    assert ManifestCategory.CLI_TOOLS in {s.construct for s in pre}


def test_the_device_shape_substitutes_the_teclaw_cli_port() -> None:
    """The category is always platform-managed, so its *port* cannot be the
    device bundle's either.

    That bundle carries the **ARCA** CLI port, which would call ARCA-only
    engine endpoints on a teclaw bot — and, since ``phase_of`` puts this
    category before the container, would run with no container to call at all.
    The one strategy that hands out a bundle built for another family is the
    one that corrects it.
    """
    teclaw_cli = object()
    ports = _device(cli_tool_service=lambda: teclaw_cli).ports()
    assert ports.cli_tool_service is teclaw_cli
    # Every other port still comes from the device bundle.
    assert ports.resource_service == "device"


def test_the_platform_shape_substitutes_nothing() -> None:
    """Its bundle is built teclaw-side already, by the composition root
    provider that binds the store these ports write to — so there is no
    mismatch to correct, and no second wiring site to keep in agreement."""
    assert _platform().ports() == _ports("store")


def test_arca_keeps_its_own_cli_port() -> None:
    """The substitution is the device-backed teclaw shape's alone — an ARCA
    bot's tools do go into its live container."""
    assert _arca().ports().cli_tool_service == "arca"


def test_a_device_strategy_cannot_be_built_without_a_cli_service() -> None:
    """The wiring that used to be expressible and wrong.

    ``cli_tool_service`` was optional, so a teclaw strategy could be built
    carrying the ARCA CLI port — the exact mismatch the substitution exists to
    correct. It is a required argument now, which is the whole guard: the
    composition root binds one, and a rig that forgets fails at construction
    rather than at an apply.
    """
    with pytest.raises(TypeError):
        TeclawDeviceDelivery(ports=lambda: _ports("device"))


def test_the_arca_table_itself_is_untouched_by_w9() -> None:
    """The per-family rule lives in the teclaw tables; ARCA's own still says
    ``ON_CONTAINER``, and the position is the shared one. A change here would
    silently re-phase ARCA too."""
    step = next(s for s in APPLY_ORDER if s.construct is ManifestCategory.CLI_TOOLS)
    assert (_ARCA_PHASES[step.construct], step.position) == (
        ApplyPhase.ON_CONTAINER,
        6,
    )


@pytest.mark.parametrize("build", [_platform, _device])
def test_script_is_pre_container_on_either_teclaw_shape(build) -> None:
    """``script`` is unsupported on teclaw, but the phase is still answered —
    and answered from the family's own table, since ``ApplyStep`` carries none.
    A step that fell out of both phases would be skipped silently instead of
    walking the orchestrator's no-support path and being reported."""
    step = next(s for s in APPLY_ORDER if s.construct is ManifestSection.SCRIPT)
    strategy = build()
    assert strategy.phase_of(step) is ApplyPhase.PRE_CONTAINER
    assert ManifestSection.SCRIPT in {
        s.construct for s in strategy.steps_for(ApplyPhase.PRE_CONTAINER)
    }


def _every_strategy():
    return (_arca(), _platform(), _device())


def test_no_phase_walks_every_construct_on_every_strategy() -> None:
    """``None`` — stated or defaulted — is the whole apply, on every strategy.

    The two spellings must not drift: an omitted phase and an explicit ``None``
    are the same statement, and a strategy that answered them differently would
    make the HTTP routes and the orchestrator's default disagree.
    """
    whole = tuple(sorted(APPLY_ORDER, key=lambda s: s.position))
    for strategy in _every_strategy():
        assert strategy.steps_for() == whole
        assert strategy.steps_for(None) == whole
        assert len(whole) == len(APPLY_ORDER) == 7


def test_the_two_phases_partition_the_whole_apply_on_every_strategy() -> None:
    """Each half, re-sorted together, is exactly the whole apply.

    This is what lets the creation job deliver in two calls and the HTTP routes
    in one without either losing or repeating a construct. A step that fell out
    of both phases, or into both, would break one of the two.
    """
    whole = tuple(sorted(APPLY_ORDER, key=lambda s: s.position))
    for strategy in _every_strategy():
        pre = strategy.steps_for(ApplyPhase.PRE_CONTAINER)
        on = strategy.steps_for(ApplyPhase.ON_CONTAINER)
        assert tuple(sorted(pre + on, key=lambda s: s.position)) == whole
        assert set(pre).isdisjoint(on)


# ── the closing step ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_the_platform_shape_closes_an_apply() -> None:
    calls: list[object] = []

    async def redeliver(ctx):
        calls.append(ctx)
        return "note"

    platform = TeclawPlatformDelivery(
        ports=lambda: _ports("s"), redeliver=redeliver
    )
    assert await platform.finish(object(), object()) == "note"
    # Neither other strategy takes a redeliver at all, so neither can run one.
    assert await _device().finish(object(), object()) is None
    assert await _arca().finish(object(), object()) is None
    assert len(calls) == 1


# ── selecting the teclaw shape: the mode table ────────────────────────────


def _bindings() -> TeclawDeliveryBindings:
    return TeclawDeliveryBindings(
        platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"),
        redeliver=_no_redeliver,
        cli_tool_service=lambda: "teclaw-cli",
    )


@pytest.mark.parametrize(
    "mode,expected,tag",
    [
        (TeclawDeliveryMode.PLATFORM, TeclawPlatformDelivery, "store"),
        (TeclawDeliveryMode.DEVICE, TeclawDeviceDelivery, "device"),
    ],
)
def test_the_mode_table_builds_one_strategy(mode, expected, tag) -> None:
    strategy = teclaw_delivery_for_mode(mode, _bindings())
    assert isinstance(strategy, expected)
    assert strategy.ports().resource_service == tag


def test_the_mode_table_is_exhaustive() -> None:
    """The import-time check in ``delivery.py``, named here.

    A mode a deployment can write into its yaml and nothing can build is "the
    surface accepts what it cannot run" — and adding a shape is a class and a
    row, which is the point of a table rather than a branch.
    """
    assert set(TECLAW_DELIVERY_BY_MODE) == set(TeclawDeliveryMode)


def test_only_the_selected_shape_is_built() -> None:
    """The unselected row never runs, so it can never be handed to anything.

    The bundle carries both port thunks because the rows share a signature;
    what proves only one is used is that neither thunk is *called* by selection
    itself — a strategy reaches its graph on its first apply, not at boot.
    """
    built: list[str] = []

    bindings = TeclawDeliveryBindings(
        platform_ports=lambda: built.append("platform") or _ports("store"),
        device_ports=lambda: built.append("device") or _ports("device"),
        redeliver=_no_redeliver,
        cli_tool_service=lambda: "teclaw-cli",
    )
    strategy = teclaw_delivery_for_mode(TeclawDeliveryMode.DEVICE, bindings)
    assert built == []
    strategy.ports()
    assert built == ["device"]


# ── the factory: a lookup over built strategies ───────────────────────────


def _factory(*, arca=None, teclaw=None) -> DeliveryStrategyFactory:
    return DeliveryStrategyFactory(
        family_of=family_from_engine_test(_IS_TECLAW),
        strategies={
            EngineFamily.ARCA: arca or _arca(),
            EngineFamily.TECLAW: teclaw or _platform(),
        },
    )


def test_the_factory_picks_by_the_engine_authority() -> None:
    factory = _factory()
    assert isinstance(factory.for_engine("openclaw"), ArcaDelivery)
    assert isinstance(factory.for_engine("claude_code"), ArcaDelivery)
    assert isinstance(factory.for_engine("TeClaw"), TeclawPlatformDelivery)
    assert isinstance(
        factory.for_bot({"active_engine": "teclaw"}), TeclawPlatformDelivery
    )
    # An unknown or absent engine is the authority's "not teclaw".
    assert isinstance(factory.for_engine(None), ArcaDelivery)


def test_the_factory_hands_back_the_object_it_was_given() -> None:
    """It builds nothing and configures nothing.

    Whichever teclaw shape a deployment runs was decided by the mode table
    before this object existed; the factory's whole job is to say which family
    a bot is and return the row. So the strategy a bot applies through is
    *identically* the one the composition root bound — not a copy, and not
    something assembled per call out of collaborators the factory was holding.
    """
    teclaw = _device()
    arca = _arca()
    factory = _factory(arca=arca, teclaw=teclaw)
    assert factory.for_engine("teclaw") is teclaw
    assert factory.for_engine("claude_code") is arca
    assert factory.for_engine("teclaw") is factory.for_engine("teclaw")
    assert factory.for_family(EngineFamily.TECLAW) is teclaw


def test_the_factory_refuses_a_family_with_no_strategy() -> None:
    """At construction, in the composition root — never as a ``KeyError`` on
    the first apply of a bot that happened to be that family.

    This is what replaced the old "the switch is on and nothing is bound"
    guard. That state existed because the strategy took a switch *and* both
    bundles, so it could be built half-configured; a strategy that cannot be
    constructed without its own collaborators leaves only one thing to check —
    that every family has one.
    """
    with pytest.raises(ValueError) as excinfo:
        DeliveryStrategyFactory(
            family_of=family_from_engine_test(_IS_TECLAW),
            strategies={EngineFamily.ARCA: _arca()},
        )
    assert "teclaw" in str(excinfo.value)


def test_the_family_adapter_translates_the_authority_once() -> None:
    """``is_teclaw`` answers a boolean because that is what the rest of the
    platform asks it; the seam keys by family. One adapter, at the boundary."""
    family_of = family_from_engine_test(_IS_TECLAW)
    assert family_of("teclaw") is EngineFamily.TECLAW
    assert family_of("TECLAW") is EngineFamily.TECLAW
    assert family_of("claude_code") is EngineFamily.ARCA
    assert family_of(None) is EngineFamily.ARCA


# ── the deployment's mode, read from config ───────────────────────────────


_PLATFORM = TeclawDeliveryMode.PLATFORM
_DEVICE = TeclawDeliveryMode.DEVICE


@pytest.mark.parametrize(
    "tree,expected",
    [
        (None, _DEVICE),
        ({}, _DEVICE),
        ({"bot_config_manifest": {}}, _DEVICE),
        ({"bot_config_manifest": {"teclaw_platform_managed": True}}, _PLATFORM),
        ({"bot_config_manifest": {"teclaw_platform_managed": "yes"}}, _PLATFORM),
        ({"bot_config_manifest": {"teclaw_platform_managed": "false"}}, _DEVICE),
        ({"bot_config_manifest": {"teclaw_platform_managed": "off"}}, _DEVICE),
        ({"bot_config_manifest": {"teclaw_platform_managed": 0}}, _DEVICE),
        ({"bot_config_manifest": {"teclaw_platform_managed": 1}}, _PLATFORM),
        ({"bot_config_manifest": {"teclaw_platform_managed": None}}, _DEVICE),
        ({"bot_config_manifest": "not-a-mapping"}, _DEVICE),
    ],
)
def test_the_mode_parses_from_the_config_tree(tree, expected) -> None:
    """The yaml key is still the boolean switch deployments wrote; what it
    parses to is a mode, and that is the last the boolean is seen."""
    assert teclaw_delivery_mode_from_config(tree) is expected


@pytest.mark.parametrize("raw", ["nope", "2", 2, 0.5, [], {}])
def test_a_malformed_switch_raises_rather_than_enabling(raw) -> None:
    with pytest.raises(ValueError):
        teclaw_delivery_mode_from_config(
            {"bot_config_manifest": {"teclaw_platform_managed": raw}}
        )
