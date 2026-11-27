"""Opt-in MultiRollout query schema. Legacy responses omit the extension."""

from typing import Literal
from pydantic import BaseModel, Field, model_serializer


class MultiRolloutControl(BaseModel):
    schema: Literal["product-runtime/multi-rollout@1"] = Field(
        description="Local versioned controller projection, not ACCS wire."
    )
    run_id: str
    phase: Literal[
        "CREATED",
        "SAMPLING",
        "EXECUTING",
        "SELECTING",
        "ADAPTING",
        "WAITING_FEEDBACK",
        "SUCCEEDED",
        "FAILED",
        "STOPPED",
    ]
    outcome: str
    reason: str
    strategy: Literal["parallel_best_of", "adaptive_until_accepted"]
    generation: int
    focus: int
    winner: int | None
    created_at: int
    deadline: int
    waiting_until: int | None
    configured: int
    started: int
    completed: int
    accepted: int
    incomplete: int
    active_candidate_ids: list[int]
    limits: dict[str, int]
    used: dict[str, int]
    reserved_slots: int
    peak_concurrency: int
    cancellations: dict[str, str]
    strategy_elapsed_ms: dict[str, int]
    strategy_attempts: list[dict]
    candidates: list[dict] = Field(
        description="Ordered actual rollout identities, immutable plans, body Bindings and trusted scores."
    )


class RolloutQueryResponse(BaseModel):
    rollouts: list[dict]
    revision: int = Field(
        description="Aggregate CAS revision; retain original request/revision on unknown report outcomes."
    )
    current_rollout_id: int = Field(
        description="Legacy current rollout; MultiRollout presentation/interaction focus, not its report allowlist."
    )
    stopped: bool
    multi_rollout_control: MultiRolloutControl | None = Field(
        default=None,
        description="Present only for explicitly selected MultiRollout@1.0 profiles.",
    )

    @model_serializer(mode="wrap")
    def omit_legacy_extension(self, handler):
        result = handler(self)
        if self.multi_rollout_control is None:
            result.pop("multi_rollout_control", None)
        return result
