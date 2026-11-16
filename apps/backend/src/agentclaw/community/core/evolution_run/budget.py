"""预算与其用量（06-evolution-run.zh-CN §2.1、§11.1）。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["Budget", "BudgetUsage"]


def _decimal(value: float | int | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class Budget:
    """单次运行的花费上限；`None` 的维度不设限。"""

    max_usd: float
    max_wall_clock_s: int
    max_rollouts: int | None = None
    max_tokens: int | None = None

    def problems(self) -> list[str]:
        found: list[str] = []
        if isinstance(self.max_usd, bool) or not isinstance(self.max_usd, (int, float)):
            found.append("budget.max_usd must be a number")
        elif self.max_usd < 0:
            found.append("budget.max_usd must be non-negative")
        if self.max_wall_clock_s <= 0:
            found.append("budget.max_wall_clock_s must be positive")
        for name in ("max_rollouts", "max_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                found.append(f"budget.{name} must be non-negative when set")
        return found

    def within(self, ceiling: Budget) -> bool:
        """每个维度都不超过 ``ceiling``。"""

        if self.max_usd > ceiling.max_usd:
            return False
        if self.max_wall_clock_s > ceiling.max_wall_clock_s:
            return False
        for name in ("max_rollouts", "max_tokens"):
            mine, theirs = getattr(self, name), getattr(ceiling, name)
            if theirs is not None and (mine is None or mine > theirs):
                return False
        return True


@dataclass(frozen=True)
class BudgetUsage:
    """同单位的已花费金额；已花费的数额永不重置（§7.3）。"""

    usd: Decimal = Decimal("0")
    wall_clock_s: int = 0
    rollouts: int = 0
    tokens: int = 0

    @classmethod
    def of(
        cls,
        *,
        usd: float | int | str | Decimal = 0,
        wall_clock_s: int = 0,
        rollouts: int = 0,
        tokens: int = 0,
    ) -> BudgetUsage:
        return cls(
            usd=_decimal(usd),
            wall_clock_s=wall_clock_s,
            rollouts=rollouts,
            tokens=tokens,
        )

    def plus(self, other: BudgetUsage) -> BudgetUsage:
        return BudgetUsage(
            usd=self.usd + other.usd,
            wall_clock_s=max(self.wall_clock_s, other.wall_clock_s),
            rollouts=self.rollouts + other.rollouts,
            tokens=self.tokens + other.tokens,
        )

    def exhausted_dimension(self, budget: Budget) -> str | None:
        """耗尽的维度名，未耗尽返回 ``None``。"""

        if self.usd > _decimal(budget.max_usd):
            return "usd"
        if self.wall_clock_s > budget.max_wall_clock_s:
            return "wall_clock_s"
        if budget.max_rollouts is not None and self.rollouts > budget.max_rollouts:
            return "rollouts"
        if budget.max_tokens is not None and self.tokens > budget.max_tokens:
            return "tokens"
        return None

    def as_dict(self) -> dict[str, float | int]:
        return {
            "usd": float(self.usd),
            "wall_clock_s": self.wall_clock_s,
            "rollouts": self.rollouts,
            "tokens": self.tokens,
        }
