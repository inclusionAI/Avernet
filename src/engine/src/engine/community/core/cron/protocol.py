"""
CronService Protocol — scheduled-task plugin interface.

Each engine implementation under engines/<name>/cron.py provides a class that
structurally satisfies this Protocol. EngineManager exposes the active engine's
cron plugin via `EngineManager.get_instance().cron` (None if the engine does
not declare any cron capabilities).

Method names use the domain-specific form (`list_jobs`, `add_job`) rather than
the generic §5.1 template (`list`, `create`) to avoid collisions at call sites
— this is permitted per §5.1.

See src/engine/docs/heterogeneous-engine-architecture.md.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.plugin_api.cron.models import (
    CreateJobRequest,
    CronJob,
    CronRunRecord,
    CronStatus,
    UpdateJobRequest,
)
from engine.community.core.engine.context import AuthContext


@runtime_checkable
class CronService(Protocol):
    """Backend talks to the cron engine through this Protocol.

    All methods accept an optional `auth: AuthContext` — plugins that need to
    scope upstream connections per-tenant read `auth.token`. HTTP routers that
    don't propagate auth today just omit the argument (it defaults to `None`).
    """

    async def list_jobs(
        self, auth: AuthContext | None = None,
    ) -> list[CronJob]:
        """List all configured cron jobs."""
        ...

    async def get_job(
        self, job_id: str, auth: AuthContext | None = None,
    ) -> CronJob | None:
        """Look up a single job by id."""
        ...

    async def get_status(
        self, auth: AuthContext | None = None,
    ) -> CronStatus:
        """Report the cron service's overall status."""
        ...

    async def add_job(
        self, request: CreateJobRequest, auth: AuthContext | None = None,
    ) -> CronJob:
        """Create a new cron job."""
        ...

    async def update_job(
        self,
        job_id: str,
        request: UpdateJobRequest,
        auth: AuthContext | None = None,
    ) -> CronJob:
        """Update an existing job."""
        ...

    async def remove_job(
        self, job_id: str, auth: AuthContext | None = None,
    ) -> bool:
        """Remove a job. Returns True if the job existed and was removed."""
        ...

    async def run_job(
        self,
        job_id: str,
        force: bool = False,
        timeout: int | None = None,
        auth: AuthContext | None = None,
    ) -> dict:
        """Trigger a one-off run of the given job.

        force=True bypasses concurrency or rate-limit guards. timeout caps
        the run duration in seconds (None = no cap).
        """
        ...

    async def get_runs(
        self,
        job_id: str,
        limit: int = 20,
        auth: AuthContext | None = None,
    ) -> list[CronRunRecord]:
        """Return historical run records for a job, newest first."""
        ...

    async def get_running_jobs(
        self, auth: AuthContext | None = None,
    ) -> list[dict]:
        """List currently-executing jobs."""
        ...


__all__ = ["CronService"]
