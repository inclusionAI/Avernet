"""Which delivery binding each caller gets (W9, spec rev 8 D-14).

``cli_tools`` has three delivery bindings, not two, because *who pushes the
artifact* differs by caller rather than by engine. Getting this wrong is
invisible until production and expensive both ways round:

* a management-API install bound without the redeliver answers 200 while the
  running container keeps its previous tool set;
* a manifest apply bound *with* it pushes an artifact mid-apply — ``cli_tools``
  final, ``resources`` or ``skills`` not yet written — and then
  ``TeclawDelivery.finish`` pushes the correct one over it.

So the bindings are asserted here rather than left to the reader of a factory.
"""
from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.cli_tools.arca_port import (
    ArcaCliToolPort,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.bot_service import (
    FAMILY_ARCA,
    FAMILY_TECLAW,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    CliToolServiceFactory,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.teclaw_port import (
    TeclawCliToolPort,
)


def _port(test_injector, family: str):
    factory = test_injector.get(CliToolServiceFactory)
    return factory(family)._delivery


def test_the_apply_binding_pushes_no_artifact_of_its_own(test_injector) -> None:
    """``TeclawDelivery.finish`` makes that apply's single push."""
    port = _port(test_injector, "teclaw")
    assert isinstance(port, TeclawCliToolPort)
    assert port._redeliver is None


def test_the_management_api_binding_carries_the_redeliver(test_injector) -> None:
    """Its path has no closing step, so the port is what reaches the container."""
    port = _port(test_injector, FAMILY_TECLAW)
    assert isinstance(port, TeclawCliToolPort)
    assert port._redeliver is not None


def test_the_two_teclaw_bindings_are_not_the_same_key(test_injector) -> None:
    """The bug this guards is a rename collapsing them back into one."""
    assert FAMILY_TECLAW != "teclaw"


def test_arca_is_one_binding_for_both_callers(test_injector) -> None:
    """Nothing differs there: the engine call *is* the delivery on either path,
    so there is no closing push for a caller to own."""
    assert isinstance(_port(test_injector, FAMILY_ARCA), ArcaCliToolPort)
