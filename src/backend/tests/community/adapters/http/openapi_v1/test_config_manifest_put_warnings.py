"""The ``PUT`` response's notes never turn a stored write into a ``500``.

``start_put_apply`` already swallows a failing apply start (the document is
stored; §2.7). Resolving the bot's delivery strategy for the response's
container note can fail the same way on a misconfigured deployment, so it is
guarded the same way and the note is simply left out.
"""
from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.bots.config_manifest_support import (
    SCRIPT_DELIVERY_NOTE,
    delivery_or_none,
    put_warnings,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    ManifestWriteResult,
)

_BOT = {"bot_id": "b_1", "status": "PENDING", "active_engine": "teclaw"}


class _ApplyServiceWhoseStrategyIsMisconfigured:
    def delivery_for_bot(self, bot):
        raise RuntimeError("teclaw_platform_managed is on but no platform ports are bound")


class _ApplyService:
    def __init__(self, strategy) -> None:
        self._strategy = strategy

    def delivery_for_bot(self, bot):
        return self._strategy


class _ContainerBound:
    def needs_container(self) -> bool:
        return True


def _result(*, declares_script: bool) -> ManifestWriteResult:
    return ManifestWriteResult(record=object(), warnings=("a note",), declares_script=declares_script)


def test_a_strategy_that_cannot_be_resolved_is_none_not_a_500():
    assert delivery_or_none(_ApplyServiceWhoseStrategyIsMisconfigured(), _BOT) is None
    strategy = _ContainerBound()
    assert delivery_or_none(_ApplyService(strategy), _BOT) is strategy


def test_without_a_strategy_the_container_note_is_left_out():
    warnings = put_warnings(_result(declares_script=True), strategy=None, bot=_BOT)
    assert warnings == ["a note", SCRIPT_DELIVERY_NOTE]


def test_with_a_container_bound_strategy_a_pending_bot_is_warned():
    warnings = put_warnings(_result(declares_script=False), strategy=_ContainerBound(), bot=_BOT)
    assert warnings[0] == "a note" and len(warnings) == 2
    assert warnings[1].startswith("the bot is PENDING")
