"""Bounded user-context HTTP to the configured ECB origin; no business engine."""

import json

import anyio
import httpx
from agentclaw.community.adapters.knowledge_platform.catalog import EcbKnowledgeClient
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    AsyncContentTransport,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_content import ContentReply


class EcbKnowledgeContentClient:
    JSON_LIMIT = 2 * 1024 * 1024

    def __init__(
        self, remote: EcbKnowledgeClient, async_transport: AsyncContentTransport
    ):
        self.remote, self.transport = remote, async_transport

    def client(self):
        self.remote.config.require_enabled()
        if not self.remote.origin:
            raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503)
        return self.transport

    @staticmethod
    async def bounded(response, limit):
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > limit:
                raise KnowledgeError("KNOWLEDGE_RESPONSE_TOO_LARGE", 502)
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def reply(status, body):
        if status >= 500 or 300 <= status < 400:
            raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503)
        # The known ECB 4xx detail contains field validation, duplicate evidence,
        # CAS conflicts or login requirements; never forward headers/cookies.
        try:
            payload = json.loads(body) if body else {}
            if not isinstance(payload, (dict, list)):
                raise ValueError("invalid response")
        except (ValueError, UnicodeDecodeError):
            raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503) from None
        return ContentReply(
            status,
            body or b"{}",
            {"Content-Type": "application/json", "Cache-Control": "no-store"},
        )

    async def call(
        self, method, path, *, headers, params=None, body=None, files=None, data=None
    ):
        # ECB session validation needs its own trusted origin, not the browser Referer.
        # This context supplements the allowlisted session cookies; it is not identity.
        try:
            client = self.client()
            async with client.stream(
                method,
                self.remote.origin + path,
                headers={
                    **headers,
                    "Accept-Encoding": "identity",
                    "Referer": self.remote.origin + "/",
                },
                timeout=60,
                params=params,
                json=body,
                files=files,
                data=data,
            ) as response:
                raw = await self.bounded(response, self.JSON_LIMIT)
                return self.reply(response.status_code, raw)
        except httpx.HTTPError:
            raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503) from None

    async def job(self, *, kb_id, job_id, headers):
        from agentclaw.community.adapters.knowledge_platform.job_reader import (
            read_business_job,
        )

        return await read_business_job(
            self, kb_id=kb_id, job_id=job_id, headers=headers
        )

    async def raw(self, path, *, headers):
        client = self.client()
        response = None
        try:
            response = await client.send(
                client.build_request(
                    "GET",
                    self.remote.origin + path,
                    headers={
                        **headers,
                        "Accept-Encoding": "identity",
                        "Referer": self.remote.origin + "/",
                    },
                    timeout=60,
                ),
                stream=True,
            )
            if response.status_code != 200:
                raw = await self.bounded(response, self.JSON_LIMIT)
                result = self.reply(response.status_code, raw)
                await response.aclose()
                return result, None
            result = ContentReply(
                200,
                b"",
                {
                    "Content-Type": response.headers.get("content-type", "text/plain"),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
            if "content-disposition" in response.headers:
                result.headers["Content-Disposition"] = response.headers[
                    "content-disposition"
                ]
        except BaseException as exc:
            # A returned response belongs to this request, never to the shared
            # pool. Also release it if bounded reading fails or gets cancelled.
            if response is not None:
                with anyio.CancelScope(shield=True):
                    await response.aclose()
            if isinstance(exc, (httpx.HTTPError, KnowledgeError)):
                raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503) from None
            raise

        async def chunks():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                with anyio.CancelScope(shield=True):
                    await response.aclose()

        return result, chunks()
