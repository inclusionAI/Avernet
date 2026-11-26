from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class PublishCtx:
    """Context for the register hook (online publish creation).

    Carries the fields needed to call ChangeFlow register: the publish identity
    tuple, the bot identity, the frozen release plan (stages/batches/targets),
    and the declared initiator. Observe-only — the observer MUST NOT block the
    publish even if ChangeFlow is unreachable.
    """

    __slots__ = (
        "publish_id",
        "tenant",
        "env",
        "publish_type",
        "bot_id",
        "bot_name",
        "instance_count",
        "operator",
        "candidate_version",
        "previous_version",
        "revision",
        "stages",
    )

    def __init__(
        self,
        *,
        publish_id: int,
        tenant: str,
        env: str,
        publish_type: str,
        bot_id: int,
        bot_name: str | None,
        instance_count: int,
        operator: str,
        candidate_version: str,
        previous_version: str,
        revision: str,
        stages: list[dict[str, Any]],
    ) -> None:
        self.publish_id = publish_id
        self.tenant = tenant
        self.env = env
        self.publish_type = publish_type
        self.bot_id = bot_id
        self.bot_name = bot_name
        self.instance_count = instance_count
        self.operator = operator
        self.candidate_version = candidate_version
        self.previous_version = previous_version
        self.revision = revision
        self.stages = stages


class BatchCtx:
    """Context for the pre-check and batch-result hooks (per batch)."""

    __slots__ = (
        "publish_id",
        "change_id",
        "stage",
        "batch_index",
        "targets",
        "candidate_version",
        "bot_name",
        "outcome",
        "device_states",
        "error",
        "occurred_at",
    )

    def __init__(
        self,
        *,
        publish_id: int,
        change_id: str | None,
        stage: str,
        batch_index: int,
        targets: list[str],
        candidate_version: str,
        bot_name: str | None = None,
        outcome: str | None = None,
        device_states: list[dict[str, Any]] | None = None,
        error: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self.publish_id = publish_id
        self.change_id = change_id
        self.stage = stage
        self.batch_index = batch_index
        self.targets = targets
        self.candidate_version = candidate_version
        self.bot_name = bot_name
        self.outcome = outcome
        self.device_states = device_states
        self.error = error
        self.occurred_at = occurred_at


class TerminalCtx:
    """Context for the publish-terminal hook (publish reaches a terminal state)."""

    __slots__ = ("publish_id", "change_id", "outcome", "reason", "occurred_at")

    def __init__(
        self,
        *,
        publish_id: int,
        change_id: str | None,
        outcome: str,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self.publish_id = publish_id
        self.change_id = change_id
        self.outcome = outcome
        self.reason = reason
        self.occurred_at = occurred_at


class ChangeFlowObserver(Protocol):
    """Observe-only ChangeFlow integration hook for the BaaS publish lifecycle.

    ChangeFlow records facts and returns risk judgements; it never drives the
    publish. Implementations MUST be non-blocking: any failure (ChangeFlow
    unreachable, 4xx/5xx, verdict=risk) is logged/altered and the publish
    proceeds unchanged. The community default is NoopChangeFlowObserver.

    Implementations:
    - NoopChangeFlowObserver: community no-op (OSS purity, Rule 20/21).
    - ProdChangeFlowObserver: enterprise overlay, real HTTP via the new
      gateway with AK/SK signing + Mist-stored credentials.
    """

    async def on_register(self, ctx: PublishCtx) -> str | None:
        """Called when an online publish is created.

        Returns the ChangeFlow change_id on success (persisted by the caller
        into baas_publish.extra_config["cf"]["change_id"]); returns None on
        failure — the caller then skips subsequent batch/terminal reports for
        this publish and alerts, without blocking the publish.
        """
        ...

    async def on_pre_check(self, ctx: BatchCtx) -> None:
        """Called before each batch goes live (kind=pre, fire-and-forget).

        Non-blocking: the verdict is advisory only and never gates the publish.
        pending is recorded once without convergence.
        """
        ...

    async def on_batch_result(self, ctx: BatchCtx) -> None:
        """Called when a batch reaches a terminal status (succeeded/failed)."""
        ...

    async def on_publish_terminal(self, ctx: TerminalCtx) -> None:
        """Called when the publish reaches a terminal state (success/failed/cancelled)."""
        ...
