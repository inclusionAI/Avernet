"""HTTP implementation of the TC resource-ready outbound port."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from agentclaw.community.core.ports.tc_resource_ready_port import (
    TcResourceReadyEvent,
    TcResourceReadyPublisherPort,
)
from agentclaw.community.plugin_api.http_client import HttpClient

_RESOURCE_READY_PATH = "/api/v1/knowledge/integrations/tc/files/upload-completed"


class HttpTcResourceReadyPublisher(TcResourceReadyPublisherPort):
    """Post through a small dedicated executor so unrelated work is isolated."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: HttpClient,
        timeout_seconds: float = 10.0,
        worker_threads: int = 2,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds_must_be_positive")
        if worker_threads < 1:
            raise ValueError("worker_threads_must_be_positive")
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=worker_threads,
            thread_name_prefix="tc-resource-ready",
        )

    async def publish(self, event: TcResourceReadyEvent) -> None:
        if not self._base_url:
            raise ValueError("ecb_base_url_not_configured")

        def request() -> None:
            response = self._http_client.post(
                f"{self._base_url}{_RESOURCE_READY_PATH}",
                json=event.as_payload(),
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, request)


__all__ = ["HttpTcResourceReadyPublisher"]
