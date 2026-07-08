"""OpenClawCronPort — native port for cron/scheduled-task operations.

Cron is pooled (client+pool), so port methods take `token: str | None = None`
for per-token routing.  Returns raw dicts/list[dict]/bool — the adapter builds
the core `CronJob` / `CronStatus` / `CronRunRecord` DTOs and handles !ok
errors.

NOTE: there is NO port method for `get_running_jobs` — that is adapter-side
composition over `cron_list` (filter by state.running_at_ms).
"""
from __future__ import annotations

from typing import Protocol


class OpenClawCronPort(Protocol):
    """Native cron operations over the OpenClaw gateway."""

    async def cron_list(
        self,
        include_disabled: bool = True,
        token: str | None = None,
    ) -> list[dict]:
        """Call `cron.list` and return a list of raw job dicts.

        Returns `[]` on gateway error or unexpected payload shape.
        """
        ...

    async def cron_get(
        self,
        job_id: str,
        token: str | None = None,
    ) -> dict | None:
        """Call `cron.get` (feature-probed) and return the raw job dict.

        If the gateway does not advertise `cron.get` in `hello.features`,
        falls back to a `cron.list` scan.  Returns `None` when not found.
        """
        ...

    async def cron_status(
        self,
        token: str | None = None,
    ) -> dict:
        """Call `cron.status` and return the raw status payload dict.

        Returns `{}` on gateway error.
        """
        ...

    async def cron_add(
        self,
        params: dict,
        token: str | None = None,
    ) -> dict:
        """Call `cron.add` with the pre-serialized `params` dict.

        `params` is built by the adapter (schedule/payload already converted to
        OpenClaw camelCase format, delivery field included).  Returns the raw
        created-job dict.  Raises `RuntimeError` on gateway error.
        """
        ...

    async def cron_update(
        self,
        job_id: str,
        patch: dict,
        token: str | None = None,
    ) -> dict:
        """Call `cron.update` with the pre-serialized `patch` dict.

        `patch` is built by the adapter.  Returns the raw updated-job dict.
        Raises `RuntimeError` on gateway error.
        """
        ...

    async def cron_remove(
        self,
        job_id: str,
        token: str | None = None,
    ) -> bool:
        """Call `cron.remove` and return True on success, False on error."""
        ...

    async def cron_run(
        self,
        job_id: str,
        mode: str,
        token: str | None = None,
    ) -> dict:
        """Call `cron.run` with the given mode string.

        Returns the raw run-result dict.  Raises `RuntimeError` on gateway
        error.
        """
        ...

    async def cron_runs(
        self,
        job_id: str,
        limit: int = 20,
        token: str | None = None,
    ) -> list[dict]:
        """Call `cron.runs` and return a list of raw run-record dicts.

        Returns `[]` on gateway error or unexpected payload shape.
        """
        ...


__all__ = ["OpenClawCronPort"]
