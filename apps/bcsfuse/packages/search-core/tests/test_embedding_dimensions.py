"""Index generations opt into dimensions on both preparation and query clients."""

import asyncio
import json

import httpx
import pytest
from search_core.config.embedding import EmbeddingSettings
from search_core.providers.async_model_http import AsyncModelHttpClient
from search_core.providers.embedding import AsyncHttpEmbedding, RealEmbeddingProvider


@pytest.mark.parametrize("explicit", [False, True])
def test_sync_and_async_send_identical_model_and_dimension(monkeypatch, explicit):
    seen = []
    result = {"data": [{"embedding": [1.0] * 64}]}

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=result)

    async def async_respond(self, url, *, headers, json):
        seen.append(json)
        return result

    monkeypatch.setattr(AsyncModelHttpClient, "post_json", async_respond)
    settings = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-only",
        model="selected-model",
        dimension=64,
        request_dimensions=explicit,
    )
    sync = RealEmbeddingProvider(settings, max_retries=0, request_interval_ms=0)
    sync._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert sync.embed("same query") == [1.0] * 64
    finally:
        sync.close()

    async def run():
        query = AsyncHttpEmbedding(settings)
        try:
            assert await query.embed("same query") == [1.0] * 64
        finally:
            await query.aclose()

    asyncio.run(run())
    expected = {"input": ["same query"], "model": "selected-model"}
    if explicit:
        expected["dimensions"] = 64
    assert seen == [expected, expected]
