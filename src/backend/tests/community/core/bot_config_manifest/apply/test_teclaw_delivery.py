"""``TeclawDelivery``'s ports and closing step, and their two helpers (W8 Task 12).

Four combinations of the switch and the binding, plus the record-only
activation wrapper and the redeliver's own answers.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    DeliveryStrategyFactory,
    MaterialiserPorts,
    TeclawDelivery,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    ApplyStatus,
)
from agentclaw.community.core.bot_config_manifest.apply.record_only_activation import (
    RecordOnlyActivation,
)
from agentclaw.community.core.bot_config_manifest.apply.redeliver import TeclawRedeliver

from tests.community.core.bot_config_manifest.apply._fakes import make_context


def _run(coro):
    return asyncio.run(coro)


def _ports(tag: str) -> MaterialiserPorts:
    return MaterialiserPorts(
        script_service=tag, activation_service=tag, mcp_auth_service=tag,
        identity_service=tag, upload_service=tag, capability_reader=tag,
        package_validator=tag, entry_fetcher=tag, resource_service=tag,
    )


def _report() -> ApplyReport:
    return ApplyReport(
        apply_id="ap_1", bot_id="b_1", trigger="explicit",
        status=ApplyStatus.SUCCEEDED, started_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )


class _NotBound(RuntimeError):
    pass


class _Sync:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[str] = []

    def deliver_manifest_apply(self):
        self.calls.append("deliver_manifest_apply")
        return self.result

    def sync_symlinks(self, symlinks, **_):
        # A runtime edit's delivery: the redeliver must not take it, because
        # it composes for the occasion that leaves every category the engine's.
        self.calls.append("sync_symlinks")
        return self.result


def _redeliver(*, bound: bool, result: Any = {"success": True}):
    sync = _Sync(result)
    resolved: list[tuple[str, str]] = []

    def resolve(bot_id, owner_id):
        resolved.append((bot_id, owner_id))
        if not bound:
            raise _NotBound()
        return {"bot_id": bot_id}

    return TeclawRedeliver(resolve=resolve, dispatch=lambda device: sync, not_bound=_NotBound), sync, resolved


# ── ports ──────────────────────────────────────────────────────────────────


def test_switch_on_hands_the_platform_ports_and_off_the_device_ports() -> None:
    on = TeclawDelivery(
        platform_managed=True, platform_ports=lambda: _ports("store"), device_ports=lambda: _ports("device")
    )
    off = TeclawDelivery(
        platform_managed=False, platform_ports=lambda: _ports("store"), device_ports=lambda: _ports("device")
    )
    assert on.ports().identity_service == "store"
    assert off.ports().identity_service == "device"


def test_the_factory_binds_the_platform_ports_and_the_redeliver() -> None:
    redeliver, sync, _ = _redeliver(bound=True)
    factory = DeliveryStrategyFactory(
        is_teclaw=lambda e: e == "teclaw",
        teclaw_platform_managed=True,
        arca_ports=lambda: _ports("device"),
        teclaw_platform_ports=lambda: _ports("store"),
        redeliver=redeliver,
    )
    strategy = factory.for_engine("teclaw")
    assert strategy.ports().upload_service == "store"
    assert _run(strategy.finish(make_context(engine_type="teclaw"), _report())) is None
    assert sync.calls == ["deliver_manifest_apply"]


# ── finish: the four combinations ──────────────────────────────────────────


def _strategy(*, on: bool, redeliver) -> TeclawDelivery:
    return TeclawDelivery(
        platform_managed=on, platform_ports=lambda: _ports("store"),
        device_ports=lambda: _ports("device"), redeliver=redeliver,
    )


def test_on_and_bound_redelivers_once() -> None:
    redeliver, sync, resolved = _redeliver(bound=True)
    ctx = make_context(engine_type="teclaw", owner_id="u_owner")
    assert _run(_strategy(on=True, redeliver=redeliver).finish(ctx, _report())) is None
    assert sync.calls == ["deliver_manifest_apply"]
    # Resolved as the owner's bot, the identity the binding was made under.
    assert resolved == [("b_1", "u_owner")]


def test_on_and_unbound_does_nothing() -> None:
    redeliver, sync, _ = _redeliver(bound=False)
    ctx = make_context(engine_type="teclaw")
    assert _run(_strategy(on=True, redeliver=redeliver).finish(ctx, _report())) is None
    assert sync.calls == []


def test_on_and_a_failed_delivery_is_a_note_not_a_raise() -> None:
    redeliver, sync, _ = _redeliver(bound=True, result={"success": False, "message": "HTTP 503"})
    ctx = make_context(engine_type="teclaw")
    note = _run(_strategy(on=True, redeliver=redeliver).finish(ctx, _report()))
    assert note is not None and "HTTP 503" in note
    assert sync.calls == ["deliver_manifest_apply"]


def test_off_is_a_no_op_even_when_bound() -> None:
    redeliver, sync, resolved = _redeliver(bound=True)
    ctx = make_context(engine_type="teclaw")
    assert _run(_strategy(on=False, redeliver=redeliver).finish(ctx, _report())) is None
    assert sync.calls == [] and resolved == []


def test_a_raising_delivery_propagates_to_the_apply_service_which_notes_it() -> None:
    # The strategy does not swallow: the apply service's ``_apply_and_finish``
    # is the one place that turns a raise into a note (tested there).
    sync = _Sync(None)

    def boom(device):
        raise RuntimeError("transport down")

    redeliver = TeclawRedeliver(resolve=lambda b, o: {}, dispatch=boom, not_bound=_NotBound)
    with pytest.raises(RuntimeError):
        _run(_strategy(on=True, redeliver=redeliver).finish(make_context(engine_type="teclaw"), _report()))
    assert sync.calls == []


# ── record-only activation ─────────────────────────────────────────────────


class _Recording:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def list_installed_mcps(self, **kw):
        self.calls.append(("list_installed_mcps", kw))
        return {"a"}

    def platform_default_mcp_codes(self, **kw):
        self.calls.append(("platform_default_mcp_codes", kw))
        return frozenset({"p"})

    async def activate_mcp(self, **kw):
        self.calls.append(("activate_mcp", kw))
        return {}

    async def deactivate_mcp(self, **kw):
        self.calls.append(("deactivate_mcp", kw))
        return {}

    async def activate_skill(self, **kw):
        self.calls.append(("activate_skill", kw))
        return {}

    async def deactivate_skill(self, **kw):
        self.calls.append(("deactivate_skill", kw))
        return {}


def test_record_only_activation_passes_project_false_on_every_write() -> None:
    inner = _Recording()
    wrapped = RecordOnlyActivation(inner)
    ids = dict(bot_id="b", owner_id="o", actor_id="a")

    assert wrapped.list_installed_mcps(**ids) == {"a"}
    assert wrapped.platform_default_mcp_codes(**ids) == frozenset({"p"})
    _run(wrapped.activate_mcp(server_code="s", **ids))
    _run(wrapped.deactivate_mcp(server_code="s", **ids))
    _run(wrapped.activate_skill(skill_id="7", **ids))
    _run(wrapped.deactivate_skill(skill_id="7", **ids))

    writes = [(name, kw) for name, kw in inner.calls if name.startswith(("activate", "deactivate"))]
    assert [name for name, _ in writes] == ["activate_mcp", "deactivate_mcp", "activate_skill", "deactivate_skill"]
    assert all(kw["project"] is False for _, kw in writes)
    reads = [kw for name, kw in inner.calls if name in ("list_installed_mcps", "platform_default_mcp_codes")]
    assert all("project" not in kw for kw in reads)
