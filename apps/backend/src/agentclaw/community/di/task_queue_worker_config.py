"""Configuration for the in-process durable task worker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskQueueWorkerConfig:
    """In-process distributed-task-queue worker policy.

    Sourced from the ``task_queue_worker`` block of ``user_config``.

    BaaS and Teclaw production handlers are registered. The worker remains
    disabled by default until an environment provisions ``ac_task_queue`` and
    explicitly sets ``enabled: true``.

    - ``poll_interval_seconds`` / ``poll_jitter_seconds`` — idle cadence when a
      poll returns a non-full batch (jitter avoids fleet lockstep stampedes).
    - ``lease_seconds`` — claim lease; a crashed worker's task is reclaimable
      after this. Must exceed the longest expected handler runtime.
    - ``batch_size`` — max tasks claimed per poll; a full batch triggers an
      immediate re-poll so a backlog drains at fleet speed.
    - ``max_concurrency`` — in-flight handler cap per worker (<= batch_size).
    - ``retry_backoff_min_seconds`` / ``retry_backoff_max_seconds`` —
      exponential backoff floor and cap for the error-retry path. The first
      retry waits ``min``; each subsequent retry doubles, capped at ``max``.
      (There is no max-attempts cap — retries are bounded by the task's
      deadline, enforced DB-side.)
    """

    enabled: bool = False
    poll_interval_seconds: float = 2.0
    poll_jitter_seconds: float = 0.5
    lease_seconds: int = 60
    batch_size: int = 10
    max_concurrency: int = 10
    # Optional worker capacity reserved for a configured set of long handlers.
    # Empty types and zero slots preserve the original single-pool behavior.
    isolated_task_types: tuple[str, ...] = ()
    isolated_concurrency: int = 0
    retry_backoff_min_seconds: float = 1.0
    retry_backoff_max_seconds: float = 60.0

    def __post_init__(self) -> None:
        types = self.isolated_task_types
        slots = self.isolated_concurrency
        if slots < 0 or bool(types) != (slots > 0):
            raise ValueError('isolated task types and positive concurrency must be configured together')
        if types and (slots >= self.max_concurrency or self.max_concurrency > self.batch_size):
            raise ValueError('isolation must leave a normal slot and fit within the batch size')
        if any(not isinstance(t, str) or not t or t != t.strip() for t in types):
            raise ValueError('isolated task types must be unique nonempty exact names')
        if len({t.casefold() for t in types}) != len(types):
            raise ValueError('isolated task types must be unique nonempty exact names')


def parse_task_queue_worker(block: dict[str, Any]) -> TaskQueueWorkerConfig:
    defaults = TaskQueueWorkerConfig()
    isolated_types = block.get("isolated_task_types", defaults.isolated_task_types)
    if not isinstance(isolated_types, (list, tuple)) or not all(isinstance(x, str) for x in isolated_types):
        raise ValueError("task_queue_worker.isolated_task_types must be a list of strings")
    return TaskQueueWorkerConfig(
        enabled=bool(block.get("enabled", defaults.enabled)),
        poll_interval_seconds=float(block.get("poll_interval_seconds", defaults.poll_interval_seconds)),
        poll_jitter_seconds=float(block.get("poll_jitter_seconds", defaults.poll_jitter_seconds)),
        lease_seconds=int(block.get("lease_seconds", defaults.lease_seconds)),
        batch_size=int(block.get("batch_size", defaults.batch_size)),
        max_concurrency=int(block.get("max_concurrency", defaults.max_concurrency)),
        isolated_task_types=tuple(isolated_types),
        isolated_concurrency=int(block.get("isolated_concurrency", defaults.isolated_concurrency)),
        retry_backoff_min_seconds=float(block.get("retry_backoff_min_seconds", defaults.retry_backoff_min_seconds)),
        retry_backoff_max_seconds=float(block.get("retry_backoff_max_seconds", defaults.retry_backoff_max_seconds)),
    )
