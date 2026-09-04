"""In-memory job store with per-node progress events for SSE streaming."""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Job", "JobStore"]


@dataclass
class Job:
    id: str
    goal: str
    status: str = "pending"  # pending | running | completed | failed
    node_statuses: dict[str, str] = field(default_factory=dict)
    final_output: Any = None
    plan: dict[str, Any] | None = None
    full_plan: Any = None
    error: str | None = None
    _subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list, repr=False)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            queue.put_nowait(event)


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, goal: str) -> Job:
        job = Job(id=uuid.uuid4().hex, goal=goal)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        job = self._jobs[job_id]
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        job._subscribers.append(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job._subscribers.remove(queue)


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "goal": job.goal,
        "status": job.status,
        "node_statuses": job.node_statuses,
        "plan": job.plan,
        "final_output": job.final_output,
        "error": job.error,
    }
