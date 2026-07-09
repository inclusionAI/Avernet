"""ClaudeCodeCronPort — native port for scheduled job management.

Cron operations are pooled (client + relay), so port methods take
``token: str | None = None`` for per-token routing. Returns raw
dicts / list[dict] / bool — the adapter builds the core ``CronJob`` DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

==========================  ================================================
Port method                 Relay RPC (method name on the wire)
==========================  ================================================
``cron_list_jobs``          ``cron.list`` (includeDisabled=True)
``cron_get_job``            ``cron.get``
``cron_get_status``         ``cron.status``
``cron_get_runs``           ``cron.runs``
``cron_get_running_jobs``   (impl-side: filter running from status)
``cron_add_job``            ``cron.add``
``cron_update_job``         ``cron.update``
``cron_remove_job``         ``cron.remove``
``cron_run_job``            ``cron.run``
==========================  ================================================
"""
from __future__ import annotations

from typing import Protocol


class ClaudeCodeCronPort(Protocol):
    """Native cron job management over the claude_code gateway (vendored Node relay)."""

    async def cron_list_jobs(
        self,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``cron.list`` (includeDisabled=True); return raw job dicts.

        Includes disabled jobs in the result.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def cron_get_job(
        self,
        job_id: str,
        token: str | None = None,
    ) -> dict | None:
        """Call ``cron.get`` for a single job.

        Returns ``None`` when the job_id is not present.

        Args:
            job_id: The job identifier to look up.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def cron_get_status(
        self,
        token: str | None = None,
    ) -> dict:
        """Call ``cron.status``; return raw scheduler status dict.

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def cron_get_runs(
        self,
        job_id: str,
        limit: int = 20,
        token: str | None = None,
    ) -> list[dict]:
        """Call ``cron.runs``; return raw run history dicts for a job.

        Args:
            job_id: The job identifier whose runs to fetch.
            limit: Maximum number of run records (default 20).
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def cron_get_running_jobs(
        self,
        token: str | None = None,
    ) -> list[dict]:
        """Return currently running jobs (impl-side filter from status).

        Args:
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def cron_add_job(
        self,
        job: dict,
        token: str | None = None,
    ) -> dict:
        """Call ``cron.add`` to create a new scheduled job.

        Args:
            job: Raw job definition dict (name, cron, command, ...).
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw created job dict.
        """
        ...

    async def cron_update_job(
        self,
        job_id: str,
        patch: dict,
        token: str | None = None,
    ) -> dict:
        """Call ``cron.update`` to patch an existing job.

        Args:
            job_id: The job identifier to update.
            patch: Partial job dict to merge.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw job dict after update.
        """
        ...

    async def cron_remove_job(
        self,
        job_id: str,
        token: str | None = None,
    ) -> bool:
        """Call ``cron.remove``; return True on success, False on error.

        Args:
            job_id: The job identifier to remove.
            token: MCP token for per-token pool routing; None -> default client.
        """
        ...

    async def cron_run_job(
        self,
        job_id: str,
        token: str | None = None,
    ) -> dict:
        """Call ``cron.run`` to trigger an immediate job run.

        Args:
            job_id: The job identifier to run.
            token: MCP token for per-token pool routing; None -> default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...


__all__ = ["ClaudeCodeCronPort"]
