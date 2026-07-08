"""_CronPortMixin — scheduled job management (relay RPC, camelCase wire)."""
from __future__ import annotations

import logging

log = logging.getLogger("claude-code-community-port")


class _CronPortMixin:
    """Domain mixin: cron.{list,get,status,runs,add,update,remove,run} +
    get_running_jobs (impl-side filter)."""

    async def cron_list_jobs(self, token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request(
            "cron.list", {"includeDisabled": True})
        if not resp.ok:
            return []
        data = resp.payload or []
        if isinstance(data, dict):
            data = data.get("jobs") or data.get("entries") or []
        return [j for j in data if isinstance(j, dict)] if isinstance(data, list) else []

    async def cron_get_job(self, job_id: str,
                           token: str | None = None) -> dict | None:
        resp = await (await self._relay()).send_request(
            "cron.get", {"jobId": job_id})
        if resp.ok and isinstance(resp.payload, dict):
            return resp.payload
        # Fallback: scan the list.
        for j in await self.cron_list_jobs(token=token):
            if isinstance(j, dict) and j.get("id") == job_id:
                return j
        return None

    async def cron_get_status(self, token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request("cron.status", {})
        if not resp.ok:
            return {}
        return resp.payload if isinstance(resp.payload, dict) else {}

    async def cron_get_runs(self, job_id: str, limit: int = 20,
                            token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request(
            "cron.runs", {"id": job_id, "limit": limit})
        if not resp.ok:
            return []
        data = resp.payload or []
        if isinstance(data, dict) and "entries" in data:
            data = data["entries"]
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    async def cron_get_running_jobs(self, token: str | None = None) -> list[dict]:
        status = await self.cron_get_status(token=token)
        running = status.get("running") if isinstance(status, dict) else None
        if isinstance(running, list):
            return [j for j in running if isinstance(j, dict)]
        # Else filter the full list by a running-state field as best-effort.
        jobs = await self.cron_list_jobs(token=token)
        return [j for j in jobs
                if isinstance(j, dict) and j.get("state") == "running"]

    async def cron_add_job(self, job: dict, token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request("cron.add", job)
        if not resp.ok:
            raise RuntimeError(
                f"cron.add failed: "
                f"{resp.error.message if resp.error else 'unknown'}")
        if not isinstance(resp.payload, dict):
            raise RuntimeError("cron.add returned unexpected payload type")
        return resp.payload

    async def cron_update_job(self, job_id: str, patch: dict,
                              token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request(
            "cron.update", {"id": job_id, "patch": patch})
        if not resp.ok:
            raise RuntimeError(
                f"cron.update failed: "
                f"{resp.error.message if resp.error else 'unknown'}")
        if not isinstance(resp.payload, dict):
            raise RuntimeError("cron.update returned unexpected payload type")
        return resp.payload

    async def cron_remove_job(self, job_id: str,
                              token: str | None = None) -> bool:
        resp = await (await self._relay()).send_request(
            "cron.remove", {"id": job_id})
        if not resp.ok:
            return False
        payload = resp.payload
        if isinstance(payload, dict):
            return bool(payload.get("removed") or payload.get("ok"))
        return True

    async def cron_run_job(self, job_id: str, token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request(
            "cron.run", {"id": job_id, "mode": "force"})
        if resp.ok:
            return {"success": True, "payload": resp.payload or {"ran": job_id}}
        err = resp.error
        return {"success": False,
                "error": {"code": err.code if err else "UNKNOWN",
                          "message": err.message if err else "Unknown error"}}
