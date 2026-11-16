"""触发器调度（06 §4）：cron 匹配、幂等触发、跳过理由。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.evolution_run import (
    Binding,
    Budget,
    CronSchedule,
    CronSyntaxError,
    ManualTrigger,
    RunStatus,
    ScheduleTrigger,
    TriggerScheduler,
)

from .conftest import BOT


def _binding(trigger, **overrides) -> Binding:
    values = {
        "id": "bind_01",
        "strategy": "acme/identity-hardening@1.0.0",
        "trigger": trigger,
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "default@1",
        "budget": Budget(max_usd=5.0, max_wall_clock_s=3600),
        "params": {},
    }
    values.update(overrides)
    return Binding(**values)


def _at(minute: int, hour: int = 10) -> datetime:
    return datetime(2026, 11, 12, hour, minute, tzinfo=timezone.utc)


def test_cron_steps_and_exact_minutes() -> None:
    every_five = CronSchedule.parse("*/5 * * * *")
    assert every_five.matches(_at(10)) and every_five.matches(_at(15))
    assert not every_five.matches(_at(11))
    nightly = CronSchedule.parse("0 2 * * *")
    assert nightly.matches(_at(0, hour=2))
    assert not nightly.matches(_at(0, hour=3))


def test_cron_rejects_malformed_expressions() -> None:
    for expression in ("0 2 * *", "*/0 * * * *", "60 * * * *", "x * * * *"):
        with pytest.raises(CronSyntaxError):
            CronSchedule.parse(expression)


def test_cron_dom_and_dow_are_or_when_both_restricted() -> None:
    schedule = CronSchedule.parse("0 0 1 * 1")
    assert schedule.matches(datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc))  # the 1st
    assert schedule.matches(
        datetime(2026, 12, 7, 0, 0, tzinfo=timezone.utc)
    )  # a Monday
    assert not schedule.matches(datetime(2026, 12, 2, 0, 0, tzinfo=timezone.utc))


async def _scheduler(services) -> TriggerScheduler:
    await services.bindings.put_bindings(
        BOT, [_binding(ScheduleTrigger("*/5 * * * *"))], expected_etag=None, actor="u"
    )
    return TriggerScheduler(
        store=services.store,
        runs=services.runs,
        genome=services.genome,
        kills=services.kills,
    )


@pytest.mark.asyncio
async def test_tick_fires_once_per_minute(services) -> None:
    scheduler = await _scheduler(services)
    first = await scheduler.tick(_at(5))
    assert len(first) == 1 and first[0].status == "fired"
    run = await services.runs.get(BOT, first[0].run_id)
    assert run.status is RunStatus.QUEUED
    replay = await scheduler.tick(_at(5))
    assert replay[0].status == "skipped" and "already" in replay[0].reason


@pytest.mark.asyncio
async def test_tick_ignores_non_matching_minutes(services) -> None:
    scheduler = await _scheduler(services)
    assert await scheduler.tick(_at(6)) == []


@pytest.mark.asyncio
async def test_busy_binding_is_skipped_not_queued(services) -> None:
    scheduler = await _scheduler(services)
    await scheduler.tick(_at(5))
    later = await scheduler.tick(_at(10))
    assert later[0].status == "skipped" and later[0].reason == "binding busy"


@pytest.mark.asyncio
async def test_frozen_bot_is_skipped(services) -> None:
    scheduler = await _scheduler(services)
    services.kills.frozen = True
    result = await scheduler.tick(_at(5))
    assert result[0].status == "skipped" and result[0].reason == "bot frozen"


@pytest.mark.asyncio
async def test_drift_is_recorded_as_out_of_band_change(services) -> None:
    scheduler = await _scheduler(services)
    services.genome.drifted = True
    result = await scheduler.tick(_at(5))
    assert result[0].reason == "out_of_band_change"
    assert services.store.firing("bind_01", "2026-11-12T10:05")["status"] == "skipped"


@pytest.mark.asyncio
async def test_manual_bindings_never_fire_on_tick(services) -> None:
    await services.bindings.put_bindings(
        BOT, [_binding(ManualTrigger())], expected_etag=None, actor="u"
    )
    scheduler = TriggerScheduler(
        store=services.store,
        runs=services.runs,
        genome=services.genome,
        kills=services.kills,
    )
    assert await scheduler.tick(_at(5)) == []


@pytest.mark.asyncio
async def test_event_trigger_is_idempotent_per_event(services) -> None:
    from agentclaw.community.core.evolution_run import EventTrigger, EventName

    await services.bindings.put_bindings(
        BOT,
        [_binding(EventTrigger(EventName.FAILURE_RATE_ALERT))],
        expected_etag=None,
        actor="u",
    )
    scheduler = TriggerScheduler(
        store=services.store,
        runs=services.runs,
        genome=services.genome,
        kills=services.kills,
    )
    first = await scheduler.fire_event(BOT, "bind_01", "evt_1")
    assert first.status == "fired"
    again = await scheduler.fire_event(BOT, "bind_01", "evt_1")
    assert again.status == "skipped"
