"""_CronPortMixin — cron job management port methods."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("openclaw-port")


class _CronPortMixin:
    """Domain mixin: cron job CRUD + run/runs (pooled, per-token)."""

    async def cron_list(
        self,
        include_disabled: bool = True,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Call `cron.list`; return raw job dicts.

        Relocated from `engines/openclaw/cron.py:list_jobs` up to the
        raw-payload extraction; the dict→CronJob DTO build moved to
        `core/adapters/openclaw/cron.py`.  Returns `[]` on error or bad shape.
        """
        client = await self._pooled_client(token)
        try:
            response = await client.send_request(
                "cron.list", {"includeDisabled": include_disabled}
            )
        except ConnectionError as e:
            log.error(f"[cron_list] connection failed: {e}")
            return []
        except Exception as e:
            log.exception(f"[cron_list] unexpected error: {e}")
            return []

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error(f"[cron_list] cron.list failed: {error_msg}")
            return []

        jobs_data = response.payload or []
        if isinstance(jobs_data, dict):
            jobs_data = jobs_data.get("jobs") or jobs_data.get("entries") or []
        if not isinstance(jobs_data, list):
            log.warning(f"[cron_list] unexpected payload shape: {type(jobs_data).__name__}")
            return []
        return [j for j in jobs_data if isinstance(j, dict)]

    async def cron_get(
        self,
        job_id: str,
        token: str | None = None,
    ) -> dict[str, Any] | None:
        """Call `cron.get` (feature-probed) or fall back to list scan.

        Relocated from `engines/openclaw/cron.py:get_job` up to the raw-payload
        extraction; the dict→CronJob DTO build moved to the adapter.
        """
        client = await self._pooled_client(token)

        hello = client.hello
        supports_cron_get = (
            hello is not None
            and hello.features is not None
            and "cron.get" in hello.features.methods
        )

        if supports_cron_get:
            try:
                response = await client.send_request("cron.get", {"jobId": job_id})
                if response.ok and response.payload:
                    payload = response.payload
                    if isinstance(payload, dict):
                        return payload
            except Exception as e:
                log.debug(f"[cron_get] cron.get failed, falling back to list: {e}")

        # Fallback: scan the list
        jobs = await self.cron_list(include_disabled=True, token=token)
        for j in jobs:
            if isinstance(j, dict) and (j.get("id") == job_id):
                return j
        return None

    async def cron_status(
        self,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `cron.status`; return the raw status payload dict.

        Relocated from `engines/openclaw/cron.py:get_status` up to the
        raw-payload extraction; the dict→CronStatus DTO build moved to the
        adapter.  Returns `{}` on error.
        """
        client = await self._pooled_client(token)
        try:
            response = await client.send_request("cron.status", {})
        except ConnectionError as e:
            log.error(f"[cron_status] connection failed: {e}")
            return {}
        except Exception as e:
            log.exception(f"[cron_status] unexpected error: {e}")
            return {}

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error(f"[cron_status] cron.status failed: {error_msg}")
            return {}

        payload = response.payload or {}
        if not isinstance(payload, dict):
            log.warning(f"[cron_status] unexpected payload type: {type(payload).__name__}")
            return {}
        return payload

    async def cron_add(
        self,
        params: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `cron.add` with the pre-serialized params dict.

        Relocated from `engines/openclaw/cron.py:add_job` — the schedule/payload
        conversion and delivery assembly now happen in the adapter, so the impl
        receives a ready-to-send dict.  Returns the raw created-job dict.
        Raises `RuntimeError` on gateway error.
        """
        client = await self._pooled_client(token)
        response = await client.send_request("cron.add", params)

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error(f"[cron_add] cron.add failed: {error_msg}")
            raise RuntimeError(f"Failed to create job: {error_msg}")

        payload = response.payload
        if not isinstance(payload, dict):
            raise RuntimeError(f"cron.add returned unexpected payload type: {type(payload).__name__}")
        return payload

    async def cron_update(
        self,
        job_id: str,
        patch: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `cron.update` with the pre-serialized patch dict.

        Relocated from `engines/openclaw/cron.py:update_job` — the patch
        assembly and notify/delivery conversion now happen in the adapter.
        Returns the raw updated-job dict.  Raises `RuntimeError` on gateway
        error.
        """
        client = await self._pooled_client(token)
        response = await client.send_request(
            "cron.update", {"id": job_id, "patch": patch}
        )

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error(f"[cron_update] cron.update failed: {error_msg}")
            raise RuntimeError(f"Failed to update job: {error_msg}")

        payload = response.payload
        if not isinstance(payload, dict):
            raise RuntimeError(f"cron.update returned unexpected payload type: {type(payload).__name__}")
        return payload

    async def cron_remove(
        self,
        job_id: str,
        token: str | None = None,
    ) -> bool:
        """Call `cron.remove`; return True on success, False on error.

        Relocated from `engines/openclaw/cron.py:remove_job` up to the
        bool-result extraction.
        """
        client = await self._pooled_client(token)
        try:
            response = await client.send_request("cron.remove", {"id": job_id})
        except ConnectionError as e:
            log.error(f"[cron_remove] connection failed: {e}")
            return False
        except Exception as e:
            log.exception(f"[cron_remove] unexpected error: {e}")
            return False

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error(f"[cron_remove] cron.remove failed: {error_msg}")
            return False
        return True

    async def cron_run(
        self,
        job_id: str,
        mode: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `cron.run` with `id` + `mode`; return raw run-result dict.

        Relocated from `engines/openclaw/cron.py:run_job`.  The mode string
        ("force" or "due") is decided by the adapter.  Raises `RuntimeError` on
        gateway error.
        """
        client = await self._pooled_client(token)
        response = await client.send_request(
            "cron.run", {"id": job_id, "mode": mode}
        )

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error(f"[cron_run] cron.run failed: {error_msg}")
            raise RuntimeError(f"Failed to run job: {error_msg}")

        return response.payload or {"ran": job_id}

    async def cron_runs(
        self,
        job_id: str,
        limit: int = 20,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Call `cron.runs`; return raw run-record dicts.

        Relocated from `engines/openclaw/cron.py:get_runs` up to the
        raw-payload extraction; the dict→CronRunRecord DTO build moved to the
        adapter.  Returns `[]` on error or bad shape.
        """
        client = await self._pooled_client(token)
        try:
            response = await client.send_request(
                "cron.runs", {"id": job_id, "limit": limit}
            )
        except ConnectionError as e:
            log.error(f"[cron_runs] connection failed: {e}")
            return []
        except Exception as e:
            log.exception(f"[cron_runs] unexpected error: {e}")
            return []

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error(f"[cron_runs] cron.runs failed: {error_msg}")
            return []

        runs_data = response.payload or []
        if isinstance(runs_data, dict) and "entries" in runs_data:
            runs_data = runs_data["entries"]
        if not isinstance(runs_data, list):
            log.warning(f"[cron_runs] unexpected payload shape: {type(runs_data).__name__}")
            return []
        return [r for r in runs_data if isinstance(r, dict)]
