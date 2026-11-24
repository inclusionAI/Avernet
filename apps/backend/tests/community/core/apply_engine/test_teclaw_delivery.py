"""The teclaw strategies' ports and closing step, and their two helpers (W8 Task 12).

Both shapes a deployment can run — ``TeclawPlatformDelivery`` and
``TeclawDeviceDelivery`` — across the redeliver's bound/unbound/failed answers,
plus the record-only activation wrapper.

Each strategy is built with its own collaborators and no mode of its own, so
"what the deployment chose" is which class the rig names, not an argument
either class carries. Which class a deployment's mode names is a wiring fact,
exercised separately in
``tests/community/di/test_apply_engine_module_wiring.py``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from agentclaw.community.core.apply_engine.delivery import (
    MaterialiserPorts,
    TeclawDeviceDelivery,
    TeclawPlatformDelivery,
)
from agentclaw.community.core.apply_engine.outcomes import (
    ApplyReport,
    ApplyStatus,
)
from agentclaw.community.core.apply_engine.activation_delegates import (
    PlatformActivation,
)
from agentclaw.community.core.apply_engine.redeliver import TeclawRedeliver

from tests.community.core.apply_engine._fakes import make_context
async def _no_redeliver(ctx) -> None:
    """The closing step, doing nothing. Required on every teclaw strategy —
    the composition root always binds one — so a test that is not about the
    redeliver still has to say which one it means."""
    return None


def _run(coro):
    return asyncio.run(coro)


def _ports(tag: str) -> MaterialiserPorts:
    return MaterialiserPorts(
        script_service=tag, activation_service=tag, mcp_auth_service=tag,
        mcp_config_service=tag,
        identity_service=tag, upload_service=tag, skill_references=tag, skill_sets=tag,
        package_validator=tag, content=tag, resource_service=tag, resource_rows=tag,
        cli_tool_service=tag,
        shared_skills=tag,
        file_sync=None,
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


def test_each_strategy_hands_out_its_own_bundle() -> None:
    """Neither is handed the other's, so neither has to choose.

    The platform one writes to the store; the device one writes into the
    container, through the bundle ARCA also runs on — with the CLI port
    substituted, because that category is always platform-managed on this
    family whatever the deployment runs.
    """
    platform = TeclawPlatformDelivery(
        ports=lambda: _ports("store"), redeliver=_no_redeliver
    )
    device = TeclawDeviceDelivery(
        ports=lambda: _ports("device"), cli_tool_service=lambda: "teclaw-cli"
    )
    assert platform.ports().identity_service == "store"
    assert device.ports().identity_service == "device"
    assert device.ports().cli_tool_service == "teclaw-cli"
    # The store-backed bundle is already built teclaw-side, so the platform
    # strategy substitutes nothing.
    assert platform.ports().cli_tool_service == "store"


def test_the_platform_shape_carries_the_store_ports_and_the_redeliver() -> None:
    """Both, and its constructor requires both."""
    redeliver, sync, _ = _redeliver(bound=True)
    strategy = TeclawPlatformDelivery(
        ports=lambda: _ports("store"), redeliver=redeliver
    )
    assert strategy.ports().upload_service == "store"
    assert _run(strategy.finish(make_context(engine_type="teclaw"), _report())) is None
    assert sync.calls == ["deliver_manifest_apply"]


def test_the_device_shape_cannot_be_handed_a_redeliver_at_all() -> None:
    """Its constructor does not take one, so a device-backed deployment cannot
    redeliver by accident — and the provider that builds it is never handed the
    bundle that holds one."""
    with pytest.raises(TypeError):
        TeclawDeviceDelivery(  # type: ignore[call-arg]
            ports=lambda: _ports("device"),
            cli_tool_service=lambda: "teclaw-cli",
            redeliver=_redeliver(bound=True)[0],
        )
    strategy = TeclawDeviceDelivery(
        ports=lambda: _ports("device"), cli_tool_service=lambda: "teclaw-cli"
    )
    _, sync, resolved = _redeliver(bound=True)
    assert strategy.ports().upload_service == "device"
    assert _run(strategy.finish(make_context(engine_type="teclaw"), _report())) is None
    assert sync.calls == [] and resolved == []


# ── finish: the redeliver's answers ────────────────────────────────────────


def _strategy(*, on: bool, redeliver):
    if on:
        return TeclawPlatformDelivery(
            ports=lambda: _ports("store"), redeliver=redeliver
        )
    return TeclawDeviceDelivery(
        ports=lambda: _ports("device"), cli_tool_service=lambda: "teclaw-cli"
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


def test_the_device_shape_closes_nothing_even_when_a_container_is_bound() -> None:
    """Its ports projected as they wrote; there is nothing left to close."""
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

    def set_supplied_capabilities(self, **kw):
        self.calls.append(("set_supplied_capabilities", kw))
        return frozenset({"s"}), frozenset({"p"})

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
    wrapped = PlatformActivation(inner)
    ids = dict(bot_id="b", owner_id="o", actor_id="a")

    assert wrapped.list_installed_mcps(**ids) == {"a"}
    assert wrapped.set_supplied_capabilities(**ids) == (frozenset({"s"}), frozenset({"p"}))
    _run(wrapped.activate_mcp(server_code="s", **ids))
    _run(wrapped.deactivate_mcp(server_code="s", **ids))
    _run(wrapped.activate_skill(skill_id="7", **ids))
    _run(wrapped.deactivate_skill(skill_id="7", **ids))

    writes = [(name, kw) for name, kw in inner.calls if name.startswith(("activate", "deactivate"))]
    assert [name for name, _ in writes] == ["activate_mcp", "deactivate_mcp", "activate_skill", "deactivate_skill"]
    assert all(kw["project"] is False for _, kw in writes)
    reads = [kw for name, kw in inner.calls if name in ("list_installed_mcps", "set_supplied_capabilities")]
    assert all("project" not in kw for kw in reads)


def _weather_report():
    from dataclasses import replace
    from agentclaw.community.core.apply_engine.capabilities import Category
    from agentclaw.community.core.apply_engine.outcomes import CategoryResult, EntryOutcome, EntryResult
    return replace(_report(), categories=(CategoryResult(Category.CAPABILITIES, entries=(
        EntryResult(Category.CAPABILITIES, "mcp/weather", EntryOutcome.UNCHANGED),)),))


def test_platform_apply_confirms_artifact_and_passport_before_receipting_mcp():
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult, RuntimeProjectionStatus

    ctx = make_context(engine_type="teclaw")
    ctx.capability_state.manifest_mcp_candidates["mcp/weather"] = frozenset({"weather"})
    class Redelivery:
        async def __call__(self, _ctx):
            raise AssertionError("manifest must use receipt path")
        async def deliver_with_receipt(self, _ctx):
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=frozenset({"weather", "calendar"}))
    async def passport(_ctx):
        return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED,
            passport_updated=True, passport_mcp_codes=frozenset({"weather"}))
    strategy = TeclawPlatformDelivery(ports=lambda: _ports("store"), redeliver=Redelivery(), passport=passport)
    assert _run(strategy.finish(ctx, _weather_report())) is None
    assert ctx.capability_state.delivered_mcp_codes == {"weather"}


@pytest.mark.parametrize(("receipt", "expected"), [
    (None, "not_bound"),
    ({"success": True}, "unknown"),
    ({"success": False, "message": "unavailable"}, "failed"),
])
def test_missing_or_legacy_receipt_never_confirms_a_server(receipt, expected):
    redeliver, _, _ = _redeliver(bound=receipt is not None, result=receipt)
    result = _run(redeliver.deliver_with_receipt(make_context(engine_type="teclaw")))
    assert result.delivered_mcp_codes is None
    assert result.status == expected


def test_platform_passport_failure_does_not_confirm_artifact():
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult

    ctx = make_context(engine_type="teclaw")
    ctx.capability_state.manifest_mcp_candidates["mcp/weather"] = frozenset({"weather"})
    class Redelivery:
        async def deliver_with_receipt(self, _ctx):
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=frozenset({"weather"}))
    async def passport(_ctx):
        return RuntimeProjectionResult.pending(code="PASSPORT", reason="unavailable")
    strategy = TeclawPlatformDelivery(ports=lambda: _ports("store"), redeliver=Redelivery(), passport=passport)
    assert "Passport" in _run(strategy.finish(ctx, _weather_report()))
    assert not ctx.capability_state.delivered_mcp_codes


def test_partial_report_can_confirm_delivered_mcp_despite_other_failure():
    from dataclasses import replace
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult, RuntimeProjectionStatus

    ctx = make_context(engine_type="teclaw")
    ctx.capability_state.manifest_mcp_candidates["mcp/weather"] = frozenset({"weather"})
    class Redelivery:
        async def deliver_with_receipt(self, _ctx):
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=frozenset({"weather"}))
    async def passport(_ctx):
        return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED,
            passport_updated=True, passport_mcp_codes=frozenset({"weather"}))
    strategy = TeclawPlatformDelivery(ports=lambda: _ports("store"), redeliver=Redelivery(), passport=passport)
    assert _run(strategy.finish(ctx, replace(_weather_report(), status=ApplyStatus.PARTIAL))) is None
    assert ctx.capability_state.delivered_mcp_codes == {"weather"}

@pytest.mark.parametrize("candidates", [{"mcp/weather": frozenset({"weather"})}, {"skill_sets/default": frozenset()}])
def test_genome_platform_delivery_keeps_existing_path_without_passport(candidates):
    from dataclasses import replace
    from agentclaw.community.core.apply_engine.compiler import StateRef

    calls = []
    ctx = make_context(engine_type="teclaw")
    ctx.capability_state.manifest_mcp_candidates.update(candidates)
    class Redelivery:
        async def __call__(self, _ctx):
            calls.append("legacy")
            return None
        async def deliver_with_receipt(self, _ctx):
            calls.append("receipt")
            raise AssertionError("Genome must retain its delivery path")
    async def passport(_ctx):
        calls.append("passport")
        raise RuntimeError("unavailable")
    strategy = TeclawPlatformDelivery(ports=lambda: _ports("store"), redeliver=Redelivery(), passport=passport)
    report = replace(_report(), state=StateRef("genome", "revision"))
    assert _run(strategy.finish(ctx, report)) is None
    assert calls == ["legacy"]


@pytest.mark.parametrize("category", [
    pytest.param("remove_last_mcp", id="remove-last-mcp"),
    pytest.param("disable_group", id="disable-group"),
    pytest.param("remove_default", id="remove-default"),
])
def test_platform_manifest_capability_changes_sync_passport_even_without_candidates(category):
    from dataclasses import replace
    from agentclaw.community.core.apply_engine.capabilities import Category
    from agentclaw.community.core.apply_engine.outcomes import CategoryResult, EntryOutcome, EntryResult
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus
    from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionResult, RuntimeProjectionStatus

    result = (CategoryResult(Category.CAPABILITIES, entries=(
        EntryResult(Category.CAPABILITIES, "skill_sets/group", EntryOutcome.UPDATED),))
        if category == "disable_group" else CategoryResult(Category.CAPABILITIES,
            removals=("mcp/weather" if category == "remove_last_mcp" else "skill_sets/default",)))
    report = replace(_report(), categories=(result,))
    calls = []
    class Redelivery:
        async def deliver_with_receipt(self, _ctx):
            calls.append("deliver")
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=frozenset())
    async def passport(_ctx):
        calls.append("passport")
        return RuntimeProjectionResult(status=RuntimeProjectionStatus.CONVERGED, passport_updated=True,
                                       passport_mcp_codes=frozenset())
    strategy = TeclawPlatformDelivery(ports=lambda: _ports("store"), redeliver=Redelivery(), passport=passport)
    assert _run(strategy.finish(make_context(engine_type="teclaw"), report)) is None
    assert calls == ["deliver", "passport"]


def test_failed_only_capability_does_not_trigger_passport():
    from dataclasses import replace
    from agentclaw.community.core.apply_engine.capabilities import Category
    from agentclaw.community.core.apply_engine.outcomes import CategoryResult, EntryOutcome, EntryResult
    from agentclaw.community.core.ports.manifest_delivery import ManifestDeliveryReceipt, ManifestDeliveryStatus

    ctx = make_context(engine_type="teclaw")
    ctx.capability_state.manifest_mcp_candidates["mcp/weather"] = frozenset({"weather"})
    report = replace(_report(), categories=(CategoryResult(Category.CAPABILITIES, aborted=True, entries=(
        EntryResult(Category.CAPABILITIES, "mcp/weather", EntryOutcome.FAILED, reason="failed"),)),))
    class Redelivery:
        async def deliver_with_receipt(self, _ctx):
            return ManifestDeliveryReceipt(status=ManifestDeliveryStatus.CONFIRMED, delivered_mcp_codes=frozenset())
    async def passport(_ctx):
        raise AssertionError("failed write cannot justify a Passport update")
    strategy = TeclawPlatformDelivery(ports=lambda: _ports("store"), redeliver=Redelivery(), passport=passport)
    assert _run(strategy.finish(ctx, report)) is None
