"""Shared async transport: request isolation, cancellation and lifecycle cleanup."""

import asyncio
from unittest.mock import patch

import httpx
import pytest
from agentclaw.community.adapters.knowledge_platform.catalog import EcbKnowledgeClient
from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)
from agentclaw.community.adapters.knowledge_platform.content_client import (
    EcbKnowledgeContentClient,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_content import ContentAccess
from agentclaw.community.plugins.http_client import AsyncHttpxClient


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, *, wait=False):
        self.closed = False
        self.wait = wait
        self.started = asyncio.Event()

    async def __aiter__(self):
        yield b'{"items":['
        self.started.set()
        if self.wait:
            await asyncio.Event().wait()
        yield b"]}"

    async def aclose(self):
        await asyncio.sleep(0)
        self.closed = True


def content_client(pool):
    config = KnowledgeIntegrationConfig(True, "https://knowledge.test")
    return EcbKnowledgeContentClient(
        EcbKnowledgeClient(config.catalog_origin, config), pool
    )


@pytest.mark.asyncio
async def test_shared_pool_does_not_replay_response_cookies_or_caller_headers():
    seen = []

    def respond(request):
        seen.append(dict(request.headers))
        return httpx.Response(
            200, json={}, headers={"set-cookie": "session=server; Path=/"}
        )

    original = httpx.AsyncClient
    with patch("httpx.AsyncClient", wraps=original) as constructor:
        pool = AsyncHttpxClient(transport=httpx.MockTransport(respond))
        client = content_client(pool)
        await client.call("GET", "/first", headers={"Cookie": "session=first"})
        await client.call("GET", "/second", headers={"Cookie": "session=second"})
        await client.call("GET", "/third", headers={})
        assert constructor.call_count == 1
        assert constructor.call_args.kwargs["trust_env"] is False
        assert constructor.call_args.kwargs["follow_redirects"] is False
        assert [headers.get("cookie") for headers in seen] == [
            "session=first",
            "session=second",
            None,
        ]
        await pool.teardown()
        await pool.teardown()
        with pytest.raises(RuntimeError, match="shut down"):
            await client.call("GET", "/closed", headers={})
        assert constructor.call_count == 1


@pytest.mark.asyncio
async def test_cancelled_json_read_closes_response_without_closing_shared_pool():
    stream = TrackedStream(wait=True)
    pool = AsyncHttpxClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    )
    client = content_client(pool)
    task = asyncio.create_task(client.call("GET", "/slow", headers={}))
    await asyncio.wait_for(stream.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
    assert not pool._pooled_client().is_closed
    await pool.teardown()


@pytest.mark.asyncio
async def test_raw_close_releases_response_and_pool_can_be_reused():
    streams = []

    def respond(_):
        stream = TrackedStream()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    pool = AsyncHttpxClient(transport=httpx.MockTransport(respond))
    client = content_client(pool)
    _, chunks = await client.raw("/raw", headers={})
    await anext(chunks)
    await chunks.aclose()
    assert streams[0].closed
    assert not pool._pooled_client().is_closed
    assert (await client.call("GET", "/next", headers={})).status == 200
    assert streams[1].closed
    await pool.teardown()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 500])
async def test_raw_upstream_rejection_closes_response(status):
    stream = TrackedStream()
    pool = AsyncHttpxClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, stream=stream))
    )
    with pytest.raises(KnowledgeError):
        await content_client(pool).raw("/raw", headers={})
    assert stream.closed
    await pool.teardown()


def test_access_repr_hides_credentials():
    assert "sensitive" not in repr(ContentAccess("actor", {"Cookie": "sensitive"}))


@pytest.mark.asyncio
async def test_raw_oversize_error_releases_unconsumed_response():
    class Oversized(TrackedStream):
        async def __aiter__(self):
            yield b"x" * (EcbKnowledgeContentClient.JSON_LIMIT + 1)
            await asyncio.Event().wait()

    stream = Oversized()
    pool = AsyncHttpxClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500, stream=stream))
    )
    with pytest.raises(KnowledgeError):
        await content_client(pool).raw("/raw", headers={})
    assert stream.closed
    await pool.teardown()
