"""Named executable task contracts for backend-owned BBS scheduled jobs.

A scheduled job is an internal named task, not a Bot or Skill. Keeping a typed
task object here makes the service contract explicit and gives the later queue
implementation a stable payload shape.
"""

from __future__ import annotations

from dataclasses import dataclass

BBS_DIGEST_TASK_NAME = "digest_task"
BBS_CONTENT_IMPORT_TASK_NAME = "content_import_task"
BBS_POLL_STATISTICS_TASK_NAME = "poll_statistics_task"
BBS_TOPIC_TAG_TASK_NAME = "topic_tag_task"

__all__ = [
    "BBS_CONTENT_IMPORT_TASK_NAME",
    "BBS_DIGEST_TASK_NAME",
    "BBS_POLL_STATISTICS_TASK_NAME",
    "BBS_TOPIC_TAG_TASK_NAME",
    "BbsSandboxTask",
]


@dataclass(frozen=True, slots=True)
class BbsSandboxTask:
    """One backend-owned task execution request."""

    name: str

    def idempotency_key(self) -> str:
        return f"bbs_task:{self.name}:v1"
