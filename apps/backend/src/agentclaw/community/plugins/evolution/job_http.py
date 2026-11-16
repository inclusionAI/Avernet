"""worker 侧 HTTP 客户端（06 §7、§10）：把 `WorkerTransport` 映射成对平台的请求。

这个文件随 worker 镜像一起发布；它只依赖 httpx，不依赖 FastAPI，也不依赖
平台侧的任何装配。服务端路由在 `adapters/http/evolution_jobs/router.py`。
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.evolution_run.job_wire import decode_files

__all__ = ["HttpWorkerTransport"]


class HttpWorkerTransport:
    """worker 侧的 `WorkerTransport`：把每个方法映射成一个 HTTP 请求。"""

    def __init__(self, endpoint: str, *, client: Any = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "HttpWorkerTransport":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        response = await self._http().post(f"{self._endpoint}{path}", json=payload)
        if response.status_code == 401:
            raise PermissionError(f"job protocol rejected the caller: {path}")
        if response.status_code >= 400:
            raise RuntimeError(
                f"job protocol error {response.status_code}: {response.text}"
            )
        return response

    async def claim(self, worker_id: str, token: str,
                    strategy_refs: list[str]) -> dict[str, Any] | None:
        response = await self._post(
            "/jobs:claim",
            {"worker_id": worker_id, "token": token, "strategy_refs": list(strategy_refs)},
        )
        if response.status_code == 204:
            return None
        return response.json()

    async def heartbeat(self, job_id: str, fencing_token: str) -> dict[str, Any]:
        response = await self._post(
            f"/jobs/{job_id}:heartbeat", {"fencing_token": fencing_token}
        )
        return response.json()

    async def files(self, job_id: str, fencing_token: str) -> dict[str, bytes]:
        response = await self._post(
            f"/jobs/{job_id}/files", {"fencing_token": fencing_token}
        )
        return decode_files(response.json()["files"])

    async def submit(self, job_id: str, fencing_token: str,
                     patch: dict[str, Any]) -> dict[str, Any]:
        response = await self._post(
            f"/jobs/{job_id}/candidates",
            {"fencing_token": fencing_token, "patch": patch},
        )
        return response.json()

    async def verdict(self, job_id: str, fencing_token: str,
                      candidate_id: str) -> dict[str, Any]:
        response = await self._http().get(
            f"{self._endpoint}/jobs/{job_id}/candidates/{candidate_id}/verdict",
            params={"fencing_token": fencing_token},
        )
        if response.status_code == 401:
            raise PermissionError("job protocol rejected the caller: verdict")
        if response.status_code >= 400:
            raise RuntimeError(
                f"job protocol error {response.status_code}: {response.text}"
            )
        return response.json()

    async def complete(self, job_id: str, fencing_token: str,
                       summary: dict[str, Any]) -> None:
        await self._post(
            f"/jobs/{job_id}:complete",
            {"fencing_token": fencing_token, "summary": summary},
        )

    async def fail(self, job_id: str, fencing_token: str, reason: str) -> None:
        await self._post(
            f"/jobs/{job_id}:fail",
            {"fencing_token": fencing_token, "reason": reason},
        )

    async def experience_sessions(
        self, job_id: str, fencing_token: str, *,
        days: int, limit: int = 500, revision: str | None = None
    ) -> list[Any]:
        response = await self._post(
            f"/jobs/{job_id}/experience/sessions",
            {"fencing_token": fencing_token, "days": days,
             "limit": limit, "revision": revision},
        )
        return response.json()

    async def experience_feedback(
        self, job_id: str, fencing_token: str, *,
        days: int, kinds: list[str] | None = None, limit: int = 500
    ) -> list[Any]:
        response = await self._post(
            f"/jobs/{job_id}/experience/feedback",
            {"fencing_token": fencing_token, "days": days,
             "kinds": kinds, "limit": limit},
        )
        return response.json()

    async def models_complete(
        self, job_id: str, fencing_token: str, *,
        messages: list[Any], model: str | None = None, max_tokens: int = 4096
    ) -> dict[str, Any]:
        response = await self._post(
            f"/jobs/{job_id}/models:complete",
            {"fencing_token": fencing_token, "messages": messages,
             "model": model, "max_tokens": max_tokens},
        )
        return response.json()

    async def agents_start(
        self, job_id: str, fencing_token: str, *,
        definition: str, workspace_id: str, prompt: str,
        idempotency_key: str, timeout_s: int = 1800
    ) -> str:
        response = await self._post(
            f"/jobs/{job_id}/agents:start",
            {"fencing_token": fencing_token, "definition": definition,
             "workspace_id": workspace_id, "prompt": prompt,
             "idempotency_key": idempotency_key, "timeout_s": timeout_s},
        )
        return response.json()["operation_id"]

    async def operation_get(
        self, job_id: str, fencing_token: str, operation_id: str
    ) -> dict[str, Any]:
        response = await self._http().get(
            f"{self._endpoint}/jobs/{job_id}/operations/{operation_id}",
            params={"fencing_token": fencing_token},
        )
        if response.status_code == 401:
            raise PermissionError("job protocol rejected the caller: operation")
        if response.status_code >= 400:
            raise RuntimeError(
                f"job protocol error {response.status_code}: {response.text}"
            )
        return response.json()

    async def operation_cancel(
        self, job_id: str, fencing_token: str, operation_id: str
    ) -> dict[str, Any]:
        response = await self._post(
            f"/jobs/{job_id}/operations/{operation_id}:cancel",
            {"fencing_token": fencing_token},
        )
        return response.json()

    async def log(
        self, job_id: str, fencing_token: str, lines: list[dict[str, Any]]
    ) -> None:
        await self._post(
            f"/jobs/{job_id}/log",
            {"fencing_token": fencing_token, "lines": lines},
        )

    async def artifacts_put(
        self, job_id: str, fencing_token: str, name: str, content: bytes
    ) -> str:
        response = await self._http().put(
            f"{self._endpoint}/jobs/{job_id}/artifacts/{name}",
            params={"fencing_token": fencing_token},
            content=content,
        )
        if response.status_code == 401:
            raise PermissionError("job protocol rejected the caller: artifact")
        if response.status_code >= 400:
            raise RuntimeError(
                f"job protocol error {response.status_code}: {response.text}"
            )
        return response.json()["artifact_id"]
