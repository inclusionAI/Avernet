"""绑定与触发器（06-evolution-run.zh-CN §2.2、§2.3、§3、§4）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from avernet_genome.models import BotRef

from .budget import Budget
from .enums import EventName, RiskCeiling

__all__ = [
    "Binding",
    "BotBindings",
    "EventTrigger",
    "ManualTrigger",
    "Rollout",
    "ScheduleTrigger",
    "Trigger",
    "cron_problems",
]


@dataclass(frozen=True)
class ScheduleTrigger:
    schedule: str


@dataclass(frozen=True)
class ManualTrigger:
    manual: bool = True


@dataclass(frozen=True)
class EventTrigger:
    event: EventName


Trigger = ScheduleTrigger | ManualTrigger | EventTrigger

_CRON_FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def cron_problems(expression: str) -> list[str]:
    """五字段 cron 的结构检查（UTC；不解释语义，只拒绝明显非法）。"""

    fields = (expression or "").split()
    if len(fields) != 5:
        return [f"cron expression {expression!r} must have five fields"]
    problems: list[str] = []
    for field_value, (low, high) in zip(fields, _CRON_FIELDS):
        for part in field_value.split(","):
            pieces = part.split("/")
            if len(pieces) > 2:
                problems.append(f"cron field {field_value!r} has too many '/'")
                break
            base = pieces[0]
            if base == "*":
                continue
            for bound in base.split("-"):
                if not bound.isdigit():
                    problems.append(f"cron field {field_value!r} must be numeric, '*' or a range")
                    break
                if not low <= int(bound) <= high:
                    problems.append(f"cron value {bound} outside {low}-{high}")
    return problems


@dataclass(frozen=True)
class Rollout:
    """多实例 Bot 的投放规则（08-promotion 读取）。"""

    canary_share: str
    auto_rollback: bool = False


@dataclass(frozen=True)
class Binding:
    """Bot 所有者写给一个策略版本的挂接配置。"""

    id: str
    strategy: str
    trigger: Trigger
    parent: str
    allowed_genes: list[str]
    verification_profile: str
    budget: Budget
    params: dict[str, Any] = field(default_factory=dict)
    auto_promote_ceiling: RiskCeiling | None = RiskCeiling.T1
    rollout: Rollout | None = None

    @property
    def strategy_id(self) -> str:
        return self.strategy.split("@", 1)[0]

    @property
    def strategy_version(self) -> str:
        return self.strategy.split("@", 1)[1] if "@" in self.strategy else ""

    def shape_problems(self) -> list[str]:
        """只查绑定自身的形状；与 Bot/策略有关的检查在 §3.2 的服务里。"""

        problems: list[str] = []
        prefix = f"binding {self.id}"
        if not self.id or not self.id.strip():
            problems.append("binding id must not be empty")
        if "@" not in self.strategy or not self.strategy_id or not self.strategy_version:
            problems.append(f"{prefix}: strategy must be '<id>@<version>'")
        if not self.allowed_genes:
            problems.append(f"{prefix}: allowed_genes must not be empty")
        if self.parent != "active":
            problems.append(f"{prefix}: parent must be 'active' in this iteration")
        if not self.verification_profile:
            problems.append(f"{prefix}: verification_profile must not be empty")
        problems.extend(f"{prefix}: {problem}" for problem in self.budget.problems())
        problems.extend(f"{prefix}: {problem}" for problem in self._trigger_problems())
        if self.auto_promote_ceiling is not None and self.auto_promote_ceiling > RiskCeiling.T2:
            problems.append(f"{prefix}: auto_promote_ceiling may not be T3")
        return problems

    def _trigger_problems(self) -> list[str]:
        if isinstance(self.trigger, ScheduleTrigger):
            return cron_problems(self.trigger.schedule)
        if isinstance(self.trigger, ManualTrigger):
            return [] if self.trigger.manual else ["manual trigger must be true when given"]
        if isinstance(self.trigger, EventTrigger):
            return []
        return [f"unknown trigger {self.trigger!r}"]


@dataclass(frozen=True)
class BotBindings:
    """一个 Bot 的绑定列表；`etag` 是乐观并发的版本标识。"""

    bot: BotRef
    bindings: list[Binding]
    etag: str
    updated_by: str | None = None

    def find(self, binding_id: str) -> Binding | None:
        return next((entry for entry in self.bindings if entry.id == binding_id), None)
