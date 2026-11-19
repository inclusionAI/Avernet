"""五字段 cron（UTC）：`*`、`*/n`、`a-b`、`a-b/n`、逗号列表（06 §4.1）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = ["CronSchedule", "CronSyntaxError"]


class CronSyntaxError(ValueError):
    pass


_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _field(expression: str, low: int, high: int) -> set[int]:
    if not expression:
        raise CronSyntaxError("empty cron field")
    values: set[int] = set()
    for part in expression.split(","):
        step = 1
        if "/" in part:
            base, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronSyntaxError(f"bad step in {part!r}")
            step = int(step_text)
        else:
            base = part
        if base == "*":
            start, end = low, high
        elif "-" in base:
            left, _, right = base.partition("-")
            if not left.isdigit() or not right.isdigit():
                raise CronSyntaxError(f"bad range in {part!r}")
            start, end = int(left), int(right)
        else:
            if not base.isdigit():
                raise CronSyntaxError(f"bad value in {part!r}")
            start = end = int(base)
        if start < low or end > high or start > end:
            raise CronSyntaxError(f"value out of range in {part!r}")
        values.update(range(start, end + 1, step))
    return values


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    _dom_restricted: bool
    _dow_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> CronSchedule:
        fields = (expression or "").split()
        if len(fields) != 5:
            raise CronSyntaxError("a cron expression has five fields")
        parsed = [_field(field, *bounds) for field, bounds in zip(fields, _RANGES)]
        return cls(
            expression=expression,
            minutes=frozenset(parsed[0]),
            hours=frozenset(parsed[1]),
            days=frozenset(parsed[2]),
            months=frozenset(parsed[3]),
            weekdays=frozenset(0 if value == 7 else value for value in parsed[4]),
            _dom_restricted=fields[2] != "*",
            _dow_restricted=fields[4] != "*",
        )

    def matches(self, moment: datetime) -> bool:
        """UTC 的分钟匹配；日与星期同时受限时按 cron 的“或”语义。"""

        moment = moment.astimezone(UTC)
        if moment.minute not in self.minutes:
            return False
        if moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        day_ok = moment.day in self.days
        weekday = (moment.weekday() + 1) % 7  # cron: 0 = Sunday
        weekday_ok = weekday in self.weekdays
        if self._dom_restricted and self._dow_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok
