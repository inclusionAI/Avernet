"""Closed, bounded policy for the local MultiRollout@1.0 extension."""

from dataclasses import dataclass, field
from typing import Literal

from ..errors import ContextCollabValidationError as Invalid
from ..l4_context_primitives.product_values import text


def bounded(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise Invalid(f"{name} must be an integer in {low}..{high}")


@dataclass(frozen=True)
class MultiRolloutLimits:
    max_rollouts: int
    max_elapsed_seconds: int
    max_stage_dispatches: int
    max_strategy_calls: int
    max_no_progress: int

    def __post_init__(self):
        for name, high in (
            ("max_rollouts", 32),
            ("max_elapsed_seconds", 86400),
            ("max_stage_dispatches", 3200),
            ("max_strategy_calls", 64),
            ("max_no_progress", 8),
        ):
            bounded(getattr(self, name), 1, high, name)


@dataclass(frozen=True)
class ParallelBestOf:
    samples: int
    max_concurrency: int
    selection_ref: str
    kind: Literal["parallel_best_of"] = field(default="parallel_best_of", init=False)

    def __post_init__(self):
        bounded(self.samples, 1, 32, "samples")
        bounded(self.max_concurrency, 1, min(8, self.samples), "max_concurrency")
        text(self.selection_ref, maximum=128)


@dataclass(frozen=True)
class AdaptiveUntilAccepted:
    feedback_mode: Literal["interactive", "autonomous"]
    adaptation_ref: str
    feedback_timeout_seconds: int | None = None
    kind: Literal["adaptive_until_accepted"] = field(
        default="adaptive_until_accepted", init=False
    )

    def __post_init__(self):
        if self.feedback_mode not in {"interactive", "autonomous"}:
            raise Invalid("unknown feedback_mode")
        text(self.adaptation_ref, maximum=128)
        if self.feedback_mode == "interactive":
            bounded(self.feedback_timeout_seconds, 1, 86400, "feedback_timeout_seconds")
        elif self.feedback_timeout_seconds is not None:
            raise Invalid("autonomous policy must omit feedback_timeout_seconds")


@dataclass(frozen=True)
class MultiRolloutMethodPolicy:
    strategy: ParallelBestOf | AdaptiveUntilAccepted
    limits: MultiRolloutLimits
    isolation_ref: str
    method: Literal["MultiRollout"] = field(default="MultiRollout", init=False)

    def __post_init__(self):
        if (
            type(self.strategy) not in {ParallelBestOf, AdaptiveUntilAccepted}
            or type(self.limits) is not MultiRolloutLimits
        ):
            raise Invalid("typed MultiRollout strategy and limits are required")
        text(self.isolation_ref, maximum=128)
        if (
            isinstance(self.strategy, ParallelBestOf)
            and self.limits.max_rollouts != self.strategy.samples
        ):
            raise Invalid("parallel max_rollouts must equal samples")
        if (
            isinstance(self.strategy, AdaptiveUntilAccepted)
            and self.strategy.feedback_timeout_seconds is not None
            and self.strategy.feedback_timeout_seconds > self.limits.max_elapsed_seconds
        ):
            raise Invalid("feedback timeout exceeds total deadline")


MULTI_POLICY_TYPES = (
    MultiRolloutMethodPolicy,
    MultiRolloutLimits,
    ParallelBestOf,
    AdaptiveUntilAccepted,
)
