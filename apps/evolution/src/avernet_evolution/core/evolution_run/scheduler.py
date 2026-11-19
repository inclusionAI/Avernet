"""触发器调度器（06-evolution-run.zh-CN §4）：cron 与事件到 `RunService.submit`。

触发按 (binding, fire_key) 幂等：重复的 tick 或重放的每分钟只产生一次运行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from avernet_genome.models import BotRef

from .bindings import EventTrigger, ManualTrigger, ScheduleTrigger
from .bindings_service import _bindings_from_document
from .cron import CronSchedule, CronSyntaxError
from .enums import RunStatus
from .records import TriggerRecord
from .store import EvolutionStore

__all__ = ["FiringResult", "TriggerScheduler"]


@dataclass(frozen=True)
class FiringResult:
    bot: BotRef
    binding_id: str
    fire_key: str
    status: str  # "fired" | "skipped"
    run_id: str | None = None
    reason: str = ""


@dataclass
class TriggerScheduler:
    store: EvolutionStore
    runs: object
    genome: object
    kills: object
    _schedules: dict[str, CronSchedule | None] = field(default_factory=dict, repr=False)

    async def tick(self, now: datetime | None = None) -> list[FiringResult]:
        """一次调度；同一分钟内重复调用不会重复触发。"""

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        fire_key = moment.strftime("%Y-%m-%dT%H:%M")
        results: list[FiringResult] = []
        for bot, document in self.store.list_bindings():
            for binding in _bindings_from_document(document):
                if not isinstance(binding.trigger, ScheduleTrigger):
                    continue
                schedule = self._schedule(binding.id, binding.trigger.schedule)
                if schedule is None or not schedule.matches(moment):
                    continue
                results.append(await self._fire(bot, binding.id, fire_key, moment))
        return results

    async def fire_event(
        self,
        bot: BotRef,
        binding_id: str,
        event_id: str,
        *,
        now: datetime | None = None,
    ) -> FiringResult:
        """事件触发：每个事件 id 只产生一次运行（§4.2）。"""

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        document = self.store.get_bindings(bot.owner_id, bot.bot_id)
        if document is None:
            return FiringResult(bot, binding_id, event_id, "skipped", reason="no bindings")
        binding = next(
            (b for b in _bindings_from_document(document["document"]) if b.id == binding_id),
            None,
        )
        if binding is None:
            return FiringResult(bot, binding_id, event_id, "skipped", reason="unknown binding")
        if not isinstance(binding.trigger, EventTrigger):
            return FiringResult(
                bot,
                binding_id,
                event_id,
                "skipped",
                reason="binding is not event-triggered",
            )
        return await self._fire(bot, binding_id, f"event:{event_id}", moment)

    async def _fire(self, bot: BotRef, binding_id: str, fire_key: str, moment: datetime) -> FiringResult:
        prior = self.store.firing(binding_id, fire_key)
        if prior is not None:
            return FiringResult(
                bot,
                binding_id,
                fire_key,
                "skipped",
                reason=f"already {prior['status']}",
            )

        skip_reason = await self._skip_reason(bot, binding_id)
        if skip_reason is not None:
            self.store.record_firing(binding_id, fire_key, "skipped", skip_reason)
            return FiringResult(bot, binding_id, fire_key, "skipped", reason=skip_reason)

        try:
            run_id = await self.runs.submit(
                bot,
                binding_id,
                idempotency_key=f"{binding_id}/{fire_key}",
                trigger=TriggerRecord(kind="schedule", fire_time=moment),
            )
        except Exception as exc:  # noqa: BLE001 - 拒绝是正常结果，记为跳过
            reason = f"{type(exc).__name__}: {exc}"
            self.store.record_firing(binding_id, fire_key, "skipped", reason)
            return FiringResult(bot, binding_id, fire_key, "skipped", reason=reason)
        self.store.record_firing(binding_id, fire_key, "fired", None)
        return FiringResult(bot, binding_id, fire_key, "fired", run_id=run_id)

    async def _skip_reason(self, bot: BotRef, binding_id: str) -> str | None:
        if await self.kills.paused():
            return "platform paused"
        if await self.kills.bot_frozen(bot):
            return "bot frozen"
        if await self.genome.drift(bot) is not None:
            return "out_of_band_change"
        _, active = self.store.list_runs(
            bot.owner_id,
            bot.bot_id,
            status=[RunStatus.QUEUED, RunStatus.RUNNING],
            binding_id=binding_id,
        )
        if active:
            return "binding busy"
        return None

    def _schedule(self, binding_id: str, expression: str) -> CronSchedule | None:
        if binding_id in self._schedules:
            return self._schedules[binding_id]
        try:
            parsed: CronSchedule | None = CronSchedule.parse(expression)
        except CronSyntaxError:
            parsed = None
        self._schedules[binding_id] = parsed
        return parsed


def is_manual(binding) -> bool:
    return isinstance(binding.trigger, ManualTrigger)
