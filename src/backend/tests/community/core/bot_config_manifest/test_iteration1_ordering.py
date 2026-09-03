"""Iteration 1's ordering rule, pinned (work-items §2.12; W8).

**ARCA.** On a first boot the startup script runs *before* any other category
has been delivered: ``script`` is the only ``PRE_CONTAINER`` construct, because
it is baked into the start command, and every other construct resolves a
device and lands only after the container is up. That is why a manifest's
``script`` must not depend on anything else the same manifest declares.

**#1508 deletes this file.** Delivering every category before the container
starts reverses the order and removes the rule; the test exists so the reversal
is a deliberate deletion rather than a surprise.

**teclaw.** The rule has no teclaw arm: ``script`` is unsupported there, and
with the platform-managed switch on every construct is delivered before the
container by the artifact — there is no first-boot ordering at all.
"""
from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    ArcaDelivery,
    MaterialiserPorts,
    TeclawDelivery,
)
from agentclaw.community.core.bot_config_manifest.apply.order import (
    APPLY_ORDER,
    ApplyPhase,
    steps_for,
)
from agentclaw.community.core.bot_config_manifest.apply.triggers import (
    ALL_TRIGGERS,
    TRIGGER_COLUMN_WIDTH,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestSection


def _ports() -> MaterialiserPorts:
    return MaterialiserPorts(*([None] * 9))


def test_on_arca_the_script_is_the_only_pre_container_construct() -> None:
    pre = steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))
    assert [s.construct for s in pre] == [ManifestSection.SCRIPT]
    on = steps_for(frozenset({ApplyPhase.ON_CONTAINER}))
    assert ManifestSection.SCRIPT not in {s.construct for s in on}
    # The strategy says the same thing the table does.
    assert ArcaDelivery(_ports).steps_for(frozenset({ApplyPhase.PRE_CONTAINER})) == pre


def test_on_arca_the_script_runs_first() -> None:
    script = next(s for s in APPLY_ORDER if s.construct == ManifestSection.SCRIPT)
    assert script.position == 0
    assert all(s.position > 0 for s in APPLY_ORDER if s is not script)


def test_on_teclaw_with_the_switch_on_there_is_no_first_boot_ordering() -> None:
    teclaw = TeclawDelivery(
        platform_managed=True, platform_ports=_ports, device_ports=_ports
    )
    assert teclaw.steps_for(frozenset({ApplyPhase.ON_CONTAINER})) == ()
    assert {s.construct for s in teclaw.steps_for(frozenset({ApplyPhase.PRE_CONTAINER}))} == {
        s.construct for s in APPLY_ORDER
    }


def test_every_trigger_fits_the_record_column() -> None:
    assert ALL_TRIGGERS == ("explicit", "put", "create:pre_container", "create:on_container")
    assert all(len(t) <= TRIGGER_COLUMN_WIDTH for t in ALL_TRIGGERS)


def test_the_manifest_layer_names_no_restart_republish_or_payload_rebuild() -> None:
    """§2.6 / D-1: a PUT takes effect without a restart on either family, and
    the manifest layer reaches for no restart, republish or start-command
    rebuild to make that so. Pinned on the sources: the words never appear."""
    import pathlib

    import agentclaw.community.core.bot_config_manifest as package

    root = pathlib.Path(package.__file__).parent
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("restart_bot(", "republish(", "_build_create_bot_payload(", "rebuild_payload("):
            if needle in text:
                offenders.append(f"{path.relative_to(root)}: {needle}")
    assert offenders == [], offenders
